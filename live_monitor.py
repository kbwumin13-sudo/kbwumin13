"""A 股全市场 PB + ROE 双因子实盘监控器。

需要安装的第三方库：

    pip install schedule python-dotenv requests baostock pandas

.env 配置示例，严禁把真实密码或授权码写进代码：

    SMTP_HOST=smtp.qq.com
    SMTP_PORT=465
    SMTP_USER=your_email@qq.com
    SMTP_AUTH_CODE=your_smtp_auth_code
    MAIL_TO=receiver@example.com

运行方式：

    python live_monitor.py

调试方式：

    python live_monitor.py --prepare-once
    python live_monitor.py --scan-once

架构：
1. 09:00 北京时间：盘前离线预处理，获取全市场股票并过滤 ROE >= 10% 的观察池。
2. 14:45 北京时间：先检查本地账本持仓的卖出条件，再扫描观察池买入机会。
3. 每次盘中扫描后生成一封全景日报：今日买卖动作 + 更新后的持仓快照。
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import smtplib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

import baostock as bs
import pandas as pd
import requests
import schedule

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


BEIJING_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_DATA_DIR = Path("data/valuation_daily")
DEFAULT_WATCHLIST_PATH = Path("data/live_watchlist/watchlist.json")
DEFAULT_PORTFOLIO_PATH = Path("data/live_portfolio/portfolio.json")
DEFAULT_PERIOD = 500
DEFAULT_BUY_ROE_THRESHOLD = 10.0
DEFAULT_SELL_ROE_THRESHOLD = 5.0
DEFAULT_STD_DEV_MULTIPLIER = 0.8
DEFAULT_BUY_BUDGET = 10_000.0
SINA_BATCH_SIZE = 180
SINA_MAX_WORKERS = 6


@dataclass(frozen=True)
class WatchItem:
    """盘前观察池条目。

    bps 用于盘中实时 PB = 当前价格 / 最新每股净资产。
    pb_lower 是进攻轨买入线，pb_middle 是防守轨估值修复卖出线。
    """

    code: str
    name: str
    bps: float
    roe: float
    pb_lower: float
    pb_middle: float


@dataclass
class PortfolioPosition:
    """本地账本中的单只持仓。"""

    code: str
    name: str
    buy_date: str
    buy_price: float
    quantity: int


@dataclass(frozen=True)
class TradeAction:
    """今日行动指令。"""

    action: str
    code: str
    name: str
    price: float
    quantity: int
    realtime_pb: float
    roe: float
    reason: str


@dataclass(frozen=True)
class PortfolioSnapshot:
    """邮件日报里的持仓快照。"""

    code: str
    name: str
    buy_date: str
    buy_price: float
    quantity: int
    current_price: float
    market_value: float
    pnl_pct: float
    realtime_pb: float
    roe: float


def main() -> int:
    parser = argparse.ArgumentParser(description="A 股全市场 PB + ROE 双因子实盘监控。")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="历史 PB/ROE CSV 目录。")
    parser.add_argument("--watchlist-path", default=str(DEFAULT_WATCHLIST_PATH), help="盘前观察池 JSON 路径。")
    parser.add_argument("--portfolio-path", default=str(DEFAULT_PORTFOLIO_PATH), help="本地持仓账本 JSON 路径。")
    parser.add_argument("--period", type=int, default=DEFAULT_PERIOD, help="历史 PB 通道窗口。")
    parser.add_argument("--std-dev-multiplier", type=float, default=DEFAULT_STD_DEV_MULTIPLIER, help="PB 下轨标准差倍数。")
    parser.add_argument("--roe-threshold", type=float, default=DEFAULT_BUY_ROE_THRESHOLD, help="买入观察池 ROE 下限，单位%%。")
    parser.add_argument("--sell-roe-threshold", type=float, default=DEFAULT_SELL_ROE_THRESHOLD, help="卖出 ROE 下限，单位%%。")
    parser.add_argument("--buy-budget", type=float, default=DEFAULT_BUY_BUDGET, help="模拟单笔买入金额。")
    parser.add_argument("--limit", type=int, default=0, help="调试用：只处理前 N 只，0 表示全市场。")
    parser.add_argument("--prepare-once", action="store_true", help="立即生成一次观察池后退出。")
    parser.add_argument("--scan-once", action="store_true", help="立即执行一次盘中扫描后退出。")
    parser.add_argument("--no-email", action="store_true", help="调试用：只打印日报，不发送邮件。")
    args = parser.parse_args()

    if load_dotenv is not None:
        load_dotenv()

    if args.prepare_once:
        prepare_watchlist(args)
        return 0
    if args.scan_once:
        scan_market_and_report(args)
        return 0

    schedule.every(30).seconds.do(run_scheduled_jobs, args=args)
    print("live_monitor 已启动：09:00 生成观察池，14:45 扫描持仓与买入机会。按 Ctrl+C 退出。")
    while True:
        schedule.run_pending()
        time.sleep(1)


def run_scheduled_jobs(args: argparse.Namespace) -> None:
    """按北京时间触发 09:00 和 14:45 任务，避免依赖服务器本地时区。"""

    now = datetime.now(BEIJING_TZ)
    today = now.strftime("%Y-%m-%d")

    if now.hour == 9 and now.minute == 0 and os.environ.get("LAST_PREPARE_DATE") != today:
        os.environ["LAST_PREPARE_DATE"] = today
        prepare_watchlist(args)

    if now.hour == 14 and now.minute == 45 and os.environ.get("LAST_SCAN_DATE") != today:
        os.environ["LAST_SCAN_DATE"] = today
        scan_market_and_report(args)


def prepare_watchlist(args: argparse.Namespace) -> list[WatchItem]:
    """盘前任务：获取全市场股票，过滤 ROE >= 阈值，生成观察池 JSON。"""

    data_dir = Path(args.data_dir)
    login_result = bs.login()
    if login_result.error_code != "0":
        raise RuntimeError(f"baostock 登录失败：{login_result.error_code}, {login_result.error_msg}")

    try:
        stocks = fetch_all_a_stocks(limit=args.limit)
        print(f"盘前预处理：全市场候选 {len(stocks)} 只")
        watch_items = []
        for index, row in stocks.iterrows():
            code = row["code"]
            name = row.get("code_name", code)
            try:
                item = build_watch_item(
                    code=code,
                    name=name,
                    data_dir=data_dir,
                    period=args.period,
                    std_dev_multiplier=args.std_dev_multiplier,
                )
                if item.roe >= args.roe_threshold:
                    watch_items.append(item)
            except Exception as exc:  # noqa: BLE001 - 全市场扫描不能因单只失败中断。
                print(f"  跳过 {code} {name}: {type(exc).__name__}: {exc}")

            if (index + 1) % 200 == 0:
                print(f"  已处理 {index + 1}/{len(stocks)}，观察池 {len(watch_items)}")
    finally:
        bs.logout()

    save_watchlist(watch_items, Path(args.watchlist_path))
    print(f"观察池生成完成：{len(watch_items)} 只 -> {args.watchlist_path}")
    return watch_items


def fetch_all_a_stocks(limit: int = 0) -> pd.DataFrame:
    """用 baostock 获取全市场 A 股列表，并剔除指数/非 A 股代码。"""

    rows = []
    fields = None
    # query_all_stock 对非交易日/未来日期可能返回空；向前回退寻找最近可用交易日。
    for offset in range(0, 11):
        day = (datetime.now(BEIJING_TZ).date() - timedelta(days=offset)).strftime("%Y-%m-%d")
        result = bs.query_all_stock(day=day)
        if result.error_code != "0":
            continue
        fields = result.fields
        rows = []
        while result.next():
            rows.append(result.get_row_data())
        if rows:
            print(f"使用 {day} 的 baostock 全市场股票列表。")
            break
    if not rows or fields is None:
        raise ValueError("baostock 最近 10 天全市场股票列表均为空。")

    df = pd.DataFrame(rows, columns=fields)
    df = df[df["code"].str.match(r"^(sh|sz)\.\d{6}$", na=False)].copy()
    # 过滤常见指数和非股票代码段，只保留沪深 A 股主体。
    df = df[df["code"].str.contains(r"^(?:sh\.(?:600|601|603|605|688)|sz\.(?:000|001|002|003|300|301))", regex=True)].copy()
    if "code_name" in df.columns:
        df = df[~df["code_name"].astype(str).str.contains("指数|转债|基金|ETF|B股", regex=True)].copy()
    df = df.sort_values("code").reset_index(drop=True)
    if limit > 0:
        df = df.head(limit).reset_index(drop=True)
    return df


def build_watch_item(code: str, name: str, data_dir: Path, period: int, std_dev_multiplier: float) -> WatchItem:
    """为单只股票构建观察池条目：BPS、ROE、历史 PB 下轨和中轨。"""

    historical = load_local_history(data_dir, code)
    latest = historical.dropna(subset=["close", "pb"]).iloc[-1]
    bps = float(latest["close"] / latest["pb"])
    pb_lower = calculate_pb_lower_band(historical["pb"], period, std_dev_multiplier)
    pb_middle = calculate_pb_middle_band(historical["pb"], period)
    roe = latest_roe(code, historical)
    return WatchItem(code=code, name=name, bps=bps, roe=roe, pb_lower=pb_lower, pb_middle=pb_middle)


def load_local_history(data_dir: Path, code: str) -> pd.DataFrame:
    """读取本地历史数据，用于 PB 通道、BPS 和 ROE。

    这里默认读取 data_scanner.py 产出的 baostock 标准 CSV。
    """

    path = data_dir / f"{code.replace('.', '_')}.csv"
    if not path.exists():
        raise FileNotFoundError(f"缺少历史数据文件：{path}")

    df = pd.read_csv(path)
    required = {"date", "close", "pb"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path} 缺少字段：{sorted(missing)}")

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["pb"] = pd.to_numeric(df["pb"], errors="coerce")
    if "roe" in df.columns:
        df["roe"] = pd.to_numeric(df["roe"], errors="coerce")
    return df.dropna(subset=["date", "close", "pb"]).sort_values("date")


def latest_roe(code: str, historical: pd.DataFrame) -> float:
    """优先读取本地最新 ROE；缺失时临时 baostock 拉取最近财报。"""

    if "roe" in historical.columns:
        roe = historical["roe"].dropna()
        if not roe.empty:
            return float(roe.iloc[-1])
    return fetch_latest_roe_with_login(code)


def fetch_latest_roe_with_login(code: str) -> float:
    """带登录保护地临时拉取 ROE，供盘中持仓检查兜底使用。"""

    login_result = bs.login()
    if login_result.error_code != "0":
        raise RuntimeError(f"baostock 登录失败：{login_result.error_code}, {login_result.error_msg}")
    try:
        return fetch_latest_roe_from_baostock(code)
    finally:
        bs.logout()


def fetch_latest_roe_from_baostock(code: str) -> float:
    """拉取最近可用 ROE，返回百分比。"""

    now = datetime.now(BEIJING_TZ)
    for year in [now.year, now.year - 1, now.year - 2]:
        for quarter in [4, 3, 2, 1]:
            result = bs.query_profit_data(code=code, year=year, quarter=quarter)
            if result.error_code != "0":
                continue
            rows = []
            while result.next():
                rows.append(result.get_row_data())
            if not rows:
                continue
            df = pd.DataFrame(rows, columns=result.fields)
            roe = pd.to_numeric(df.get("roeAvg"), errors="coerce").dropna()
            if not roe.empty:
                return float(roe.iloc[-1] * 100.0)
    raise ValueError(f"{code} 未获取到 ROE。")


def calculate_pb_lower_band(pb: pd.Series, period: int, std_dev_multiplier: float) -> float:
    """计算历史 PB 下轨：mu - k * sigma。"""

    window = pd.to_numeric(pb, errors="coerce").dropna().tail(period)
    if len(window) < period:
        raise ValueError(f"PB 窗口不足：{len(window)} < {period}")
    return float(window.mean() - std_dev_multiplier * window.std(ddof=0))


def calculate_pb_middle_band(pb: pd.Series, period: int) -> float:
    """计算历史 PB 中轨：mu，用于持仓估值修复卖出。"""

    window = pd.to_numeric(pb, errors="coerce").dropna().tail(period)
    if len(window) < period:
        raise ValueError(f"PB 窗口不足：{len(window)} < {period}")
    return float(window.mean())


def save_watchlist(items: list[WatchItem], path: Path) -> None:
    """保存观察池 JSON，使用原子写入避免文件半写入损坏。"""

    payload = {
        "created_at": datetime.now(BEIJING_TZ).isoformat(),
        "items": [asdict(item) for item in items],
    }
    atomic_write_json(path, payload)


def load_watchlist(path: Path) -> pd.DataFrame:
    """读取观察池 JSON 为 DataFrame。"""

    if not path.exists():
        raise FileNotFoundError(f"观察池不存在：{path}，请先运行 --prepare-once 或等待 09:00 任务。")
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("items", [])
    if not items:
        raise ValueError(f"观察池为空：{path}")
    df = pd.DataFrame(items)
    # 兼容旧版 watchlist：缺 pb_middle 时盘中防守轨会从本地历史数据实时补算。
    return df


def load_portfolio(path: Path) -> dict[str, PortfolioPosition]:
    """读取本地持仓账本。

    缺文件时返回空账本；坏数据会被跳过，避免一个异常条目阻塞整个监控。
    """

    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    positions = {}
    for item in payload.get("positions", []):
        try:
            position = PortfolioPosition(
                code=str(item["code"]),
                name=str(item.get("name") or item["code"]),
                buy_date=str(item["buy_date"]),
                buy_price=float(item["buy_price"]),
                quantity=int(item["quantity"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            print(f"跳过损坏的账本条目：{item}，原因：{exc}")
            continue
        if position.buy_price <= 0 or position.quantity <= 0:
            print(f"跳过无效持仓：{item}")
            continue
        positions[position.code] = position
    return positions


def save_portfolio(path: Path, positions: dict[str, PortfolioPosition]) -> None:
    """保存本地持仓账本，使用临时文件 + replace 原子替换。"""

    payload = {
        "updated_at": datetime.now(BEIJING_TZ).isoformat(),
        "positions": [asdict(position) for position in sorted(positions.values(), key=lambda item: item.code)],
    }
    atomic_write_json(path, payload)


def atomic_write_json(path: Path, payload: dict) -> None:
    """安全写 JSON：先写 .tmp，再原子替换目标文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def scan_market_and_report(args: argparse.Namespace) -> tuple[list[TradeAction], list[TradeAction], list[PortfolioSnapshot]]:
    """盘中任务：防守轨先卖出，进攻轨再买入，并发送一封全景日报。"""

    data_dir = Path(args.data_dir)
    portfolio_path = Path(args.portfolio_path)
    watchlist = load_watchlist(Path(args.watchlist_path))
    positions = load_portfolio(portfolio_path)

    quote_codes = sorted(set(watchlist["code"].astype(str).tolist()) | set(positions.keys()))
    print(
        f"[{datetime.now(BEIJING_TZ).isoformat()}] 盘中扫描："
        f"观察池 {len(watchlist)} 只，持仓 {len(positions)} 只，行情请求 {len(quote_codes)} 只"
    )

    price_df = fetch_sina_quotes_batched(quote_codes)
    if price_df.empty:
        raise RuntimeError("新浪行情接口未返回任何有效价格。")
    price_map = dict(zip(price_df["code"], price_df["price"], strict=False))

    sell_actions = run_defensive_track(
        positions=positions,
        price_map=price_map,
        data_dir=data_dir,
        period=args.period,
        sell_roe_threshold=args.sell_roe_threshold,
    )

    buy_actions = run_offensive_track(
        positions=positions,
        watchlist=watchlist,
        price_df=price_df,
        buy_budget=args.buy_budget,
    )

    save_portfolio(portfolio_path, positions)
    snapshots = build_portfolio_snapshots(
        positions=positions,
        price_map=price_map,
        data_dir=data_dir,
        period=args.period,
    )

    report_html = build_report_html(buy_actions, sell_actions, snapshots)
    print_plain_report(buy_actions, sell_actions, snapshots)
    if not args.no_email:
        send_report_email(report_html, buy_actions, sell_actions)
        print("全景邮件日报已发送。")
    else:
        print("已启用 --no-email，本次只打印日报，不发送邮件。")

    return buy_actions, sell_actions, snapshots


def run_defensive_track(
    positions: dict[str, PortfolioPosition],
    price_map: dict[str, float],
    data_dir: Path,
    period: int,
    sell_roe_threshold: float,
) -> list[TradeAction]:
    """防守轨：优先检查已持仓股票，满足卖出条件则从账本移除。"""

    sell_actions = []
    for code, position in list(positions.items()):
        price = price_map.get(code)
        if price is None or price <= 0:
            print(f"防守轨跳过 {code}：没有有效实时价格。")
            continue

        try:
            fundamentals = load_realtime_fundamentals(data_dir, code, period)
        except Exception as exc:  # noqa: BLE001 - 单只失败不应中断整轮扫描。
            print(f"防守轨跳过 {code}：{type(exc).__name__}: {exc}")
            continue

        realtime_pb = price / fundamentals["bps"]
        reasons = []
        if realtime_pb >= fundamentals["pb_middle"]:
            reasons.append(f"PB 回归中轨 {fundamentals['pb_middle']:.3f}")
        if fundamentals["roe"] < sell_roe_threshold:
            reasons.append(f"ROE 跌破 {sell_roe_threshold:.1f}%")

        if reasons:
            sell_actions.append(
                TradeAction(
                    action="SELL",
                    code=code,
                    name=position.name,
                    price=price,
                    quantity=position.quantity,
                    realtime_pb=realtime_pb,
                    roe=fundamentals["roe"],
                    reason="；".join(reasons),
                )
            )
            del positions[code]

    return sell_actions


def run_offensive_track(
    positions: dict[str, PortfolioPosition],
    watchlist: pd.DataFrame,
    price_df: pd.DataFrame,
    buy_budget: float,
) -> list[TradeAction]:
    """进攻轨：扫描观察池买入机会，跳过已持仓股票并模拟买入写账本。"""

    if watchlist.empty:
        return []

    merged = watchlist.merge(price_df, on="code", how="left")
    numeric_columns = ["price", "bps", "pb_lower", "roe"]
    for column in numeric_columns:
        merged[column] = pd.to_numeric(merged[column], errors="coerce")
    merged = merged.dropna(subset=numeric_columns)
    merged = merged[(merged["price"] > 0) & (merged["bps"] > 0)].copy()
    merged["realtime_pb"] = merged["price"] / merged["bps"]

    held_codes = set(positions.keys())
    candidates = merged[
        (~merged["code"].isin(held_codes)) & (merged["realtime_pb"] < merged["pb_lower"])
    ].copy()
    candidates = candidates.sort_values("realtime_pb")

    buy_actions = []
    buy_date = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    for row in candidates.itertuples(index=False):
        quantity = int(buy_budget // float(row.price))
        if quantity <= 0:
            print(f"进攻轨跳过 {row.code}：单笔预算 {buy_budget:.2f} 不足以买入 1 股。")
            continue

        position = PortfolioPosition(
            code=str(row.code),
            name=str(row.name),
            buy_date=buy_date,
            buy_price=float(row.price),
            quantity=quantity,
        )
        positions[position.code] = position
        buy_actions.append(
            TradeAction(
                action="BUY",
                code=position.code,
                name=position.name,
                price=position.buy_price,
                quantity=position.quantity,
                realtime_pb=float(row.realtime_pb),
                roe=float(row.roe),
                reason=f"实时 PB {float(row.realtime_pb):.3f} 跌破下轨 {float(row.pb_lower):.3f}，且 ROE {float(row.roe):.2f}%",
            )
        )

    return buy_actions


def load_realtime_fundamentals(data_dir: Path, code: str, period: int) -> dict[str, float]:
    """从本地历史 CSV 推导当前监控需要的 BPS、ROE、PB 中轨。

    BPS = 最近复权收盘价 / 最近 PB。实盘使用时应定期刷新 data_scanner.py，
    确保最近财报披露后的 ROE 和 PB 序列及时进入本地数据。
    """

    historical = load_local_history(data_dir, code)
    latest = historical.dropna(subset=["close", "pb"]).iloc[-1]
    bps = float(latest["close"] / latest["pb"])
    return {
        "bps": bps,
        "roe": latest_roe(code, historical),
        "pb_middle": calculate_pb_middle_band(historical["pb"], period),
    }


def build_portfolio_snapshots(
    positions: dict[str, PortfolioPosition],
    price_map: dict[str, float],
    data_dir: Path,
    period: int,
) -> list[PortfolioSnapshot]:
    """生成更新后的持仓快照，用于邮件日报。"""

    snapshots = []
    for code, position in sorted(positions.items()):
        price = price_map.get(code)
        if price is None or price <= 0:
            continue
        try:
            fundamentals = load_realtime_fundamentals(data_dir, code, period)
        except Exception as exc:  # noqa: BLE001
            print(f"持仓快照跳过 {code}：{type(exc).__name__}: {exc}")
            continue

        realtime_pb = price / fundamentals["bps"]
        pnl_pct = (price / position.buy_price - 1.0) * 100.0
        snapshots.append(
            PortfolioSnapshot(
                code=code,
                name=position.name,
                buy_date=position.buy_date,
                buy_price=position.buy_price,
                quantity=position.quantity,
                current_price=price,
                market_value=price * position.quantity,
                pnl_pct=pnl_pct,
                realtime_pb=realtime_pb,
                roe=fundamentals["roe"],
            )
        )
    return snapshots


def fetch_sina_quotes_batched(codes: list[str]) -> pd.DataFrame:
    """批量并发请求新浪行情，严禁逐只股票循环请求。

    每批约 180 只股票，避免 URL 过长；批次之间并发执行。
    """

    unique_codes = sorted(set(codes))
    batches = [unique_codes[index : index + SINA_BATCH_SIZE] for index in range(0, len(unique_codes), SINA_BATCH_SIZE)]
    frames = []
    with ThreadPoolExecutor(max_workers=SINA_MAX_WORKERS) as executor:
        futures = [executor.submit(fetch_sina_quote_batch, batch) for batch in batches if batch]
        for future in as_completed(futures):
            try:
                frame = future.result()
                if not frame.empty:
                    frames.append(frame)
            except Exception as exc:  # noqa: BLE001 - 单批失败不应导致全部失败。
                print(f"新浪行情批次失败：{type(exc).__name__}: {exc}")
    if not frames:
        return pd.DataFrame(columns=["code", "quote_name", "price"])
    return pd.concat(frames, ignore_index=True).drop_duplicates("code", keep="last")


def fetch_sina_quote_batch(codes: list[str]) -> pd.DataFrame:
    """请求一批新浪实时行情。"""

    sina_codes = [code.replace(".", "") for code in codes]
    url = "http://hq.sinajs.cn/list=" + ",".join(sina_codes)
    headers = {
        "Referer": "https://finance.sina.com.cn/",
        "User-Agent": "Mozilla/5.0",
    }
    response = requests.get(url, headers=headers, timeout=10)
    response.encoding = "gbk"
    response.raise_for_status()
    return parse_sina_response(response.text)


def parse_sina_response(text: str) -> pd.DataFrame:
    """解析新浪 JS API 返回文本。"""

    rows = []
    pattern = re.compile(r"var hq_str_(?P<code>\w+)=\"(?P<body>.*?)\";")
    for match in pattern.finditer(text):
        body = match.group("body")
        fields = body.split(",")
        if len(fields) < 4 or not fields[0]:
            continue
        price = pd.to_numeric(fields[3], errors="coerce")
        if pd.isna(price):
            continue
        rows.append(
            {
                "code": to_baostock_code(match.group("code")),
                "quote_name": fields[0],
                "price": float(price),
            }
        )
    return pd.DataFrame(rows)


def to_baostock_code(sina_code: str) -> str:
    """sh600000 -> sh.600000。"""

    return f"{sina_code[:2]}.{sina_code[2:]}"


def send_report_email(html_body: str, buy_actions: list[TradeAction], sell_actions: list[TradeAction]) -> None:
    """发送一封全景日报，账号和授权码只能来自环境变量或 .env。"""

    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_auth_code = os.environ.get("SMTP_AUTH_CODE")
    mail_to = [item.strip() for item in os.environ.get("MAIL_TO", "").split(",") if item.strip()]

    if not smtp_host or not smtp_user or not smtp_auth_code or not mail_to:
        raise RuntimeError("邮件环境变量不完整，请配置 SMTP_HOST/SMTP_USER/SMTP_AUTH_CODE/MAIL_TO。")

    action_summary = f"买入 {len(buy_actions)} / 卖出 {len(sell_actions)}"
    message = MIMEMultipart("alternative")
    message["Subject"] = f"A股双因子实盘日报 {datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M')} | {action_summary}"
    message["From"] = smtp_user
    message["To"] = ", ".join(mail_to)
    message.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20) as server:
        server.login(smtp_user, smtp_auth_code)
        server.sendmail(smtp_user, mail_to, message.as_string())


def build_report_html(
    buy_actions: list[TradeAction],
    sell_actions: list[TradeAction],
    snapshots: list[PortfolioSnapshot],
) -> str:
    """构建包含行动指令和持仓快照的 HTML 日报。"""

    return f"""
    <html>
      <body>
        <h2>A股 PB + ROE 双因子实盘日报</h2>
        <p>生成时间：{html.escape(datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S 北京时间'))}</p>
        <h3>今日行动指令</h3>
        <h4>买入</h4>
        {build_action_table(buy_actions, empty_text="今日无买入信号")}
        <h4>卖出</h4>
        {build_action_table(sell_actions, empty_text="今日无卖出信号")}
        <h3>当前持仓快照</h3>
        {build_snapshot_table(snapshots)}
      </body>
    </html>
    """


def build_action_table(actions: list[TradeAction], empty_text: str) -> str:
    """构建买入/卖出动作表。"""

    if not actions:
        return f"<p>{html.escape(empty_text)}</p>"
    rows = "\n".join(
        (
            "<tr>"
            f"<td>{html.escape(action.action)}</td>"
            f"<td>{html.escape(action.code)}</td>"
            f"<td>{html.escape(action.name)}</td>"
            f"<td>{action.price:.2f}</td>"
            f"<td>{action.quantity}</td>"
            f"<td>{action.realtime_pb:.3f}</td>"
            f"<td>{action.roe:.2f}%</td>"
            f"<td>{html.escape(action.reason)}</td>"
            "</tr>"
        )
        for action in actions
    )
    return f"""
    <table border="1" cellpadding="6" cellspacing="0">
      <thead>
        <tr>
          <th>动作</th>
          <th>代码</th>
          <th>名称</th>
          <th>价格</th>
          <th>数量</th>
          <th>实时 PB</th>
          <th>ROE</th>
          <th>触发原因</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    """


def build_snapshot_table(snapshots: list[PortfolioSnapshot]) -> str:
    """构建持仓快照表。"""

    if not snapshots:
        return "<p>当前无持仓。</p>"
    rows = "\n".join(
        (
            "<tr>"
            f"<td>{html.escape(item.code)}</td>"
            f"<td>{html.escape(item.name)}</td>"
            f"<td>{html.escape(item.buy_date)}</td>"
            f"<td>{item.buy_price:.2f}</td>"
            f"<td>{item.quantity}</td>"
            f"<td>{item.current_price:.2f}</td>"
            f"<td>{item.market_value:.2f}</td>"
            f"<td>{item.pnl_pct:.2f}%</td>"
            f"<td>{item.realtime_pb:.3f}</td>"
            f"<td>{item.roe:.2f}%</td>"
            "</tr>"
        )
        for item in snapshots
    )
    return f"""
    <table border="1" cellpadding="6" cellspacing="0">
      <thead>
        <tr>
          <th>代码</th>
          <th>名称</th>
          <th>买入日期</th>
          <th>买入价</th>
          <th>数量</th>
          <th>当前价</th>
          <th>市值</th>
          <th>浮盈亏</th>
          <th>实时 PB</th>
          <th>ROE</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    """


def print_plain_report(
    buy_actions: list[TradeAction],
    sell_actions: list[TradeAction],
    snapshots: list[PortfolioSnapshot],
) -> None:
    """终端打印简版日报，便于 --no-email 调试。"""

    print("\n=== 今日行动指令 ===")
    if not buy_actions and not sell_actions:
        print("无买入/卖出动作。")
    for action in buy_actions:
        print(f"BUY  {action.code} {action.name} 价格={action.price:.2f} 数量={action.quantity} 原因={action.reason}")
    for action in sell_actions:
        print(f"SELL {action.code} {action.name} 价格={action.price:.2f} 数量={action.quantity} 原因={action.reason}")

    print("\n=== 当前持仓快照 ===")
    if not snapshots:
        print("当前无持仓。")
    for item in snapshots:
        print(
            f"{item.code} {item.name} 数量={item.quantity} 买入={item.buy_price:.2f} "
            f"现价={item.current_price:.2f} 浮盈亏={item.pnl_pct:.2f}% PB={item.realtime_pb:.3f}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
