"""Baostock 多标的行情 + PE/PB/ROE 数据流水线。

运行示例：

    python data_scanner.py --start-date 2020-01-01 --end-date 2024-12-31

本脚本做一件事：用 baostock 下载沪深300前 N 只股票的前复权日线行情、
PE(TTM)、PB(MRQ)，并将按公告日可见的 ROE 拼接进日线数据。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import baostock as bs
import pandas as pd


# baostock query_history_k_data_plus 字段。
# peTTM = 市盈率 TTM；pbMRQ = 市净率 MRQ。
FIELDS = "date,code,open,high,low,close,volume,peTTM,pbMRQ"

DEFAULT_OUTPUT_DIR = Path("data/valuation_daily")
DEFAULT_POOL_PATH = Path("data/stock_pool/hs300_top100.csv")


def main() -> int:
    parser = argparse.ArgumentParser(description="使用 baostock 下载多标的前复权行情与 PE/PB 数据。")
    parser.add_argument("--start-date", default="2020-01-01", help="开始日期，格式 YYYY-MM-DD。")
    parser.add_argument("--end-date", default="2024-12-31", help="结束日期，格式 YYYY-MM-DD。")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="CSV 输出目录。")
    parser.add_argument("--pool-path", default=str(DEFAULT_POOL_PATH), help="沪深300测试池保存路径。")
    parser.add_argument("--limit", type=int, default=100, help="选取沪深300成分股前 N 只，默认 100。")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    login_result = bs.login()
    if login_result.error_code != "0":
        raise RuntimeError(f"baostock 登录失败：{login_result.error_code}, {login_result.error_msg}")

    try:
        stock_pool = fetch_hs300_pool(limit=args.limit)
        save_stock_pool(stock_pool, Path(args.pool_path))
        print(f"沪深300测试池：{len(stock_pool)} 只 -> {args.pool_path}")

        for _, row in stock_pool.iterrows():
            stock_code = row["code"]
            print(f"下载 {stock_code} ...")
            try:
                daily = fetch_stock_daily(stock_code, args.start_date, args.end_date)
                roe = fetch_profit_roe(stock_code, args.start_date, args.end_date)
                cleaned = clean_for_backtrader(daily, roe)
                output_path = output_dir / f"{stock_code.replace('.', '_')}.csv"
                cleaned.to_csv(output_path, index=False)
                print(f"  保存 {len(cleaned)} 行 -> {output_path}")
            except Exception as exc:  # noqa: BLE001 - 批量下载允许单只失败后继续。
                print(f"  失败 {stock_code}: {type(exc).__name__}: {exc}")
    finally:
        # baostock 要求结束后显式登出；放在 finally 中保证异常时也会执行。
        bs.logout()

    return 0


def fetch_hs300_pool(limit: int = 100) -> pd.DataFrame:
    """获取沪深300成分股并选取前 limit 只。

    baostock 返回的 code 已经带 sh/sz 前缀，可直接用于历史数据接口。
    """

    result = bs.query_hs300_stocks()
    if result.error_code != "0":
        raise RuntimeError(f"获取沪深300成分股失败：{result.error_code}, {result.error_msg}")

    rows: list[list[str]] = []
    while result.next():
        rows.append(result.get_row_data())
    if not rows:
        raise ValueError("沪深300成分股列表为空。")

    pool = pd.DataFrame(rows, columns=result.fields)
    if "code" not in pool.columns:
        raise ValueError(f"沪深300成分股缺少 code 字段：{pool.columns.tolist()}")
    return pool.head(limit).reset_index(drop=True)


def save_stock_pool(pool: pd.DataFrame, path: Path) -> None:
    """保存本次测试股票池，便于复现实验。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    pool.to_csv(path, index=False)


def fetch_stock_daily(stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """拉取单只股票的前复权日线行情与估值数据。

    关键点：
    - adjustflag="2" 表示前复权，避免分红派息导致虚假价格跳变。
    - fields 一次性包含 OHLCV、peTTM、pbMRQ，避免后续跨源对齐产生前视偏差。
    """

    result = bs.query_history_k_data_plus(
        code=stock_code,
        fields=FIELDS,
        start_date=start_date,
        end_date=end_date,
        frequency="d",
        adjustflag="2",
    )
    if result.error_code != "0":
        raise RuntimeError(f"{stock_code} 下载失败：{result.error_code}, {result.error_msg}")

    rows: list[list[str]] = []
    while result.next():
        rows.append(result.get_row_data())

    if not rows:
        raise ValueError(f"{stock_code} 在 {start_date} 至 {end_date} 没有返回数据。")

    return pd.DataFrame(rows, columns=result.fields)


def fetch_profit_roe(stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """获取历史季报 ROE，并保留真实公告日 pubDate。

    防前视偏差核心：
    - baostock 的 query_profit_data 返回 pubDate（公告日）和 statDate（报告期）。
    - 策略在 T 日只能看到 pubDate <= T 的 ROE。
    - 因此后续拼接使用 pubDate 做 merge_asof，而不是使用 statDate。
    """

    start_year = pd.Timestamp(start_date).year - 1
    end_year = pd.Timestamp(end_date).year
    rows: list[list[str]] = []
    fields: list[str] | None = None

    for year in range(start_year, end_year + 1):
        for quarter in range(1, 5):
            result = bs.query_profit_data(code=stock_code, year=year, quarter=quarter)
            if result.error_code != "0":
                raise RuntimeError(
                    f"{stock_code} ROE 下载失败: year={year}, quarter={quarter}, "
                    f"{result.error_code}, {result.error_msg}"
                )
            fields = result.fields
            while result.next():
                rows.append(result.get_row_data())

    if not rows or fields is None:
        raise ValueError(f"{stock_code} 未返回 ROE 季报数据。")

    roe = pd.DataFrame(rows, columns=fields)
    roe["pubDate"] = pd.to_datetime(roe["pubDate"], errors="coerce")
    roe["statDate"] = pd.to_datetime(roe["statDate"], errors="coerce")
    roe["roe"] = pd.to_numeric(roe["roeAvg"], errors="coerce") * 100.0
    roe = roe.dropna(subset=["pubDate", "roe"])
    roe = roe.sort_values("pubDate").drop_duplicates(subset=["pubDate"], keep="last")
    return roe[["pubDate", "statDate", "roe"]].reset_index(drop=True)


def clean_for_backtrader(df: pd.DataFrame, roe_df: pd.DataFrame) -> pd.DataFrame:
    """清洗为 Backtrader 可直接读取的日线 CSV。

    清洗规则：
    1. 将 baostock 返回的字符串统一转成 datetime/float。
    2. 剔除 volume == 0 的停牌行，避免滚动标准差和成交逻辑失真。
    3. 将字段名统一为 Backtrader 友好格式：
       code -> symbol, peTTM -> pe_ttm, pbMRQ -> pb。
    4. 用 pubDate 将 ROE 拼接到日线，且只向后填充，杜绝前视偏差。
    5. 增加 openinterest=0，便于 Backtrader PandasData 扩展读取。
    """

    cleaned = df.rename(
        columns={
            "code": "symbol",
            "peTTM": "pe_ttm",
            "pbMRQ": "pb",
        }
    ).copy()

    cleaned["date"] = pd.to_datetime(cleaned["date"], errors="coerce")
    numeric_columns = ["open", "high", "low", "close", "volume", "pe_ttm", "pb"]
    for column in numeric_columns:
        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")

    cleaned = cleaned.dropna(subset=["date", "open", "high", "low", "close", "volume"])
    cleaned = cleaned.sort_values("date").drop_duplicates(subset=["date"], keep="last")

    # A 股停牌日可能返回 volume=0；这些行不应进入回测。
    cleaned = cleaned[cleaned["volume"] > 0].copy()

    # PE/PB 偶尔可能为空；用历史已知值向前填充，不引入未来数据。
    cleaned[["pe_ttm", "pb"]] = cleaned[["pe_ttm", "pb"]].ffill()

    roe_cleaned = roe_df.copy()
    roe_cleaned["pubDate"] = pd.to_datetime(roe_cleaned["pubDate"], errors="coerce")
    roe_cleaned["roe"] = pd.to_numeric(roe_cleaned["roe"], errors="coerce")
    roe_cleaned = roe_cleaned.dropna(subset=["pubDate", "roe"]).sort_values("pubDate")

    # Critical: 用财报真实公告日 pubDate 做 asof 合并。
    # 例如 2023 一季报 statDate=2023-03-31，但 pubDate=2023-04-26；
    # 2023-03-31 到 2023-04-25 的日线绝不能读到该 ROE。
    cleaned = pd.merge_asof(
        cleaned.sort_values("date"),
        roe_cleaned[["pubDate", "roe"]],
        left_on="date",
        right_on="pubDate",
        direction="backward",
    )
    cleaned["roe"] = cleaned["roe"].ffill()

    cleaned["openinterest"] = 0.0
    cleaned = cleaned[
        [
            "date",
            "symbol",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "openinterest",
            "pe_ttm",
            "pb",
            "roe",
        ]
    ].reset_index(drop=True)

    if cleaned.empty:
        raise ValueError("清洗后数据为空，请检查日期区间或股票是否停牌。")
    return cleaned


if __name__ == "__main__":
    raise SystemExit(main())
