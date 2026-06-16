"""多标的 PB + ROE 双因子轮动策略。

运行示例：

    python valuation_strategy.py

本模块包含：
1. PandasDataWithValuation：扩展 Backtrader 数据源，支持 pe_ttm、pb 与 roe。
2. PBReversionStrategy：基于 PB 低估 + ROE 盈利质量过滤的双因子策略。
3. run_backtest：从 data/valuation_daily/ 读取 Baostock CSV 并快速回测。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import backtrader as bt
import pandas as pd

from quant_framework.analysis.stats_validator import print_statistical_validation


DEFAULT_DATA_DIR = Path("data/valuation_daily")
DEFAULT_OUTPUT_DIR = Path("outputs/valuation_strategy")
DEFAULT_CASH = 100_000.0
PERIOD_GRID = [250, 500]
STD_DEV_MULTIPLIER_GRID = [0.8, 1.0, 1.2]


class PandasDataWithValuation(bt.feeds.PandasData):
    """扩展 Backtrader PandasData，使策略可读取 pe_ttm、pb 和 roe。

    CSV/DataFrame 必须至少包含：
    date, open, high, low, close, volume, openinterest, pe_ttm, pb, roe
    """

    lines = ("pe_ttm", "pb", "roe")
    params = (
        ("datetime", None),
        ("open", "open"),
        ("high", "high"),
        ("low", "low"),
        ("close", "close"),
        ("volume", "volume"),
        ("openinterest", "openinterest"),
        ("pe_ttm", "pe_ttm"),
        ("pb", "pb"),
        ("roe", "roe"),
    )


class PBReversionStrategy(bt.Strategy):
    """PB + ROE 双因子策略。

    买入：未持仓股票 PB 跌破历史下轨，且 ROE > roe_buy_threshold。
    卖出：PB 回升至中轨，或 ROE < roe_sell_threshold。
    仓位：当天所有买入信号等权，使用目标权重控制总持仓分散。
    """

    params = (
        ("period", 500),
        ("std_dev_multiplier", 0.8),
        ("roe_buy_threshold", 10.0),
        ("roe_sell_threshold", 5.0),
        ("max_positions", 5),
        ("target_exposure", 0.95),
        ("printlog", True),
    )

    def __init__(self) -> None:
        if self.p.period <= 1:
            raise ValueError("period must be greater than 1.")
        if self.p.std_dev_multiplier <= 0:
            raise ValueError("std_dev_multiplier must be positive.")
        if self.p.roe_buy_threshold <= self.p.roe_sell_threshold:
            raise ValueError("roe_buy_threshold should be greater than roe_sell_threshold.")
        if self.p.max_positions <= 0:
            raise ValueError("max_positions must be positive.")
        if not 0 < self.p.target_exposure <= 1:
            raise ValueError("target_exposure must be in (0, 1].")

        self.pb_mean: dict[bt.LineRoot, bt.Indicator] = {}
        self.pb_std: dict[bt.LineRoot, bt.Indicator] = {}
        self.lower_band: dict[bt.LineRoot, bt.LineRoot] = {}
        self.middle_band: dict[bt.LineRoot, bt.LineRoot] = {}
        self.upper_band: dict[bt.LineRoot, bt.LineRoot] = {}
        self.pending_orders: dict[bt.LineRoot, bt.Order] = {}

        for data in self.datas:
            mean = bt.indicators.SimpleMovingAverage(data.pb, period=self.p.period)
            std = bt.indicators.StandardDeviation(data.pb, period=self.p.period)
            self.pb_mean[data] = mean
            self.pb_std[data] = std
            self.lower_band[data] = mean - self.p.std_dev_multiplier * std
            self.middle_band[data] = mean
            self.upper_band[data] = mean + self.p.std_dev_multiplier * std

    def notify_order(self, order) -> None:  # noqa: ANN001 - Backtrader order object.
        data = order.data
        if order.status in [order.Completed, order.Canceled, order.Margin, order.Rejected]:
            self.pending_orders.pop(data, None)

            if self.p.printlog and order.status == order.Completed:
                action = "BUY" if order.isbuy() else "SELL"
                self.log(
                    f"{action} {data._name} price={order.executed.price:.2f} "
                    f"size={order.executed.size:.0f} value={order.executed.value:.2f}"
                )

    def next(self) -> None:
        buy_candidates = []
        held_datas = []

        # 1. 先处理卖出：估值回到中轨或突破上轨时清仓。
        for data in self.datas:
            if data in self.pending_orders:
                continue
            if len(data) < self.p.period:
                continue

            position = self.getposition(data)
            current_pb = float(data.pb[0])
            current_roe = float(data.roe[0])
            middle = float(self.middle_band[data][0])
            upper = float(self.upper_band[data][0])
            lower = float(self.lower_band[data][0])

            if not _is_valid_number(current_pb, current_roe, middle, upper, lower):
                continue

            if position.size > 0 and (current_pb >= middle or current_roe < self.p.roe_sell_threshold):
                self.log(
                    f"SELL SIGNAL {data._name}: pb={current_pb:.3f}, mid={middle:.3f}, "
                    f"upper={upper:.3f}, roe={current_roe:.2f}"
                )
                self.pending_orders[data] = self.close(data=data)
                continue
            if position.size > 0:
                held_datas.append(data)

            # 2. 再做截面扫描：未持仓、PB 低估、ROE 高于 10%，进入待买池。
            if position.size == 0 and current_pb < lower and current_roe > self.p.roe_buy_threshold:
                buy_candidates.append(data)

        if not buy_candidates:
            return

        # 3. 等权仓位管理：基于“当前继续持有 + 新触发买入”的目标组合统一等权。
        available_slots = max(self.p.max_positions - len(held_datas), 0)
        if available_slots <= 0:
            return

        target_datas = held_datas + buy_candidates[:available_slots]
        target_weight = self.p.target_exposure / max(len(target_datas), 1)
        for data in target_datas:
            if data in self.pending_orders:
                continue
            current_pb = float(data.pb[0])
            current_roe = float(data.roe[0])
            lower = float(self.lower_band[data][0])
            action = "REBALANCE" if self.getposition(data).size > 0 else "BUY SIGNAL"
            self.log(
                f"{action} {data._name}: pb={current_pb:.3f}, lower={lower:.3f}, "
                f"roe={current_roe:.2f}, target={target_weight:.2%}"
            )
            self.pending_orders[data] = self.order_target_percent(data=data, target=target_weight)

    def current_position_count(self) -> int:
        """统计当前持仓数量。"""

        return sum(1 for data in self.datas if self.getposition(data).size > 0)

    def log(self, message: str) -> None:
        """打印带日期的策略日志。"""

        if self.p.printlog:
            date = self.datas[0].datetime.date(0).isoformat()
            print(f"{date} | {message}")


class ClosedTradeRecorder(bt.Analyzer):
    """记录每一笔已平仓交易，输出单笔 PnL% 供统计检验使用。"""

    def start(self) -> None:
        self.trades = []

    def notify_trade(self, trade) -> None:  # noqa: ANN001 - Backtrader trade object.
        if not trade.isclosed:
            return

        entry_value = abs(_trade_entry_value(trade))
        pnl_comm = float(trade.pnlcomm)
        return_pct = pnl_comm / entry_value if entry_value else 0.0
        self.trades.append(
            {
                "data": trade.data._name,
                "ref": trade.ref,
                "bar_open": trade.baropen,
                "bar_close": trade.barclose,
                "pnl": float(trade.pnl),
                "pnl_comm": pnl_comm,
                "entry_value": entry_value,
                "return_pct": return_pct,
            }
        )

    def get_analysis(self):  # noqa: ANN201 - Backtrader analyzer API.
        return self.trades


class FinalValueAnalyzer(bt.Analyzer):
    """记录策略结束时的最终账户权益。"""

    def stop(self) -> None:
        self.final_value = float(self.strategy.broker.getvalue())

    def get_analysis(self):  # noqa: ANN201 - Backtrader analyzer API.
        return {"final_value": self.final_value}


def run_backtest(
    data_dir: str | Path = DEFAULT_DATA_DIR,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    cash: float = DEFAULT_CASH,
    period: int = 500,
    std_dev_multiplier: float = 1.5,
    max_files: int = 100,
) -> None:
    """读取本地 CSV，启动 Backtrader 多标的 PB 回归策略回测。"""

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cerebro = build_cerebro(
        data_dir=data_dir,
        cash=cash,
        period=period,
        max_files=max_files,
        print_data_load=True,
    )

    cerebro.addstrategy(
        PBReversionStrategy,
        period=period,
        std_dev_multiplier=std_dev_multiplier,
    )
    add_analyzers(cerebro)

    start_value = cerebro.broker.getvalue()
    print(f"初始资金：{start_value:.2f}")
    # 多标的 CSV 长度和交易日可能不同；Backtrader 的 runonce 向量化模式在长周期指标
    # 如 500 日 PB 均线/标准差上可能触发 array assignment index out of range。
    # 关闭 runonce 改用逐 bar 模式，速度略慢但对多数据源最稳。
    results = cerebro.run(runonce=False, tradehistory=True)
    strategy = results[0]
    final_value = cerebro.broker.getvalue()

    sharpe = strategy.analyzers.sharpe.get_analysis()
    drawdown = strategy.analyzers.drawdown.get_analysis()
    returns = strategy.analyzers.returns.get_analysis()
    trade_analyzer = strategy.analyzers.trade_analyzer.get_analysis()
    trades = pd.DataFrame(strategy.analyzers.closed_trade_recorder.get_analysis())
    trades_path = output_dir / "trades.csv"
    trades.to_csv(trades_path, index=False)

    print("\n回测完成")
    print(f"期初资产：{start_value:.2f}")
    print(f"期末资产：{final_value:.2f}")
    print(f"总收益率：{final_value / start_value - 1:.2%}")
    print(f"年化收益率：{returns.get('rnorm100', 0.0):.2f}%")
    print(f"夏普比率：{sharpe.get('sharperatio')}")
    print(f"最大回撤：{drawdown.get('max', {}).get('drawdown', 0.0):.2f}%")
    print(f"已平仓交易数：{trade_analyzer.get('total', {}).get('closed', 0)}")
    print(f"交易明细：{trades_path}")

    if len(trades) >= 2:
        print_statistical_validation(trades, return_column="return_pct")
    else:
        print("\n统计体检跳过：已平仓交易少于 2 笔，无法进行 T 检验和 Bootstrap。")


def run_optimization(
    data_dir: str | Path = DEFAULT_DATA_DIR,
    cash: float = DEFAULT_CASH,
    max_files: int = 100,
    period_grid: list[int] | None = None,
    std_grid: list[float] | None = None,
    maxcpus: int = 1,
) -> list[dict[str, float | int]]:
    """运行 Backtrader 网格搜索并打印最终资金排名 Top 5。"""

    period_grid = period_grid or PERIOD_GRID
    std_grid = std_grid or STD_DEV_MULTIPLIER_GRID
    min_period = min(period_grid)
    cerebro = build_cerebro(
        data_dir=data_dir,
        cash=cash,
        period=min_period,
        max_files=max_files,
        print_data_load=True,
    )
    cerebro.optstrategy(
        PBReversionStrategy,
        period=period_grid,
        std_dev_multiplier=std_grid,
        roe_buy_threshold=10.0,
        roe_sell_threshold=5.0,
        printlog=False,
    )
    add_analyzers(cerebro)

    print("\n开始参数优化")
    print(f"period grid: {period_grid}")
    print(f"std_dev_multiplier grid: {std_grid}")
    print(f"maxcpus: {maxcpus} (macOS 建议 1，避免多进程序列化问题)")

    results = cerebro.run(runonce=False, tradehistory=True, maxcpus=maxcpus)
    ranked = collect_optimization_results(results, cash)
    ranked.sort(key=lambda item: item["final_value"], reverse=True)
    print_optimization_top5(ranked)
    return ranked


def build_cerebro(
    data_dir: str | Path,
    cash: float,
    period: int,
    max_files: int,
    print_data_load: bool,
) -> bt.Cerebro:
    """创建 Cerebro，并加载长度足够的多标的 valuation CSV。"""

    data_dir = Path(data_dir)
    csv_files = sorted(data_dir.glob("*.csv"))[:max_files]
    if not csv_files:
        raise FileNotFoundError(f"未在 {data_dir} 找到 CSV 数据，请先运行 data_scanner.py。")

    cerebro = bt.Cerebro()
    cerebro.broker.setcash(cash)
    cerebro.broker.setcommission(commission=0.0003)

    for csv_file in csv_files:
        df = load_valuation_csv(csv_file)
        if len(df) <= period:
            if print_data_load:
                print(f"跳过数据不足：{csv_file} rows={len(df)}, period={period}")
            continue
        data = PandasDataWithValuation(dataname=df, name=csv_file.stem)
        cerebro.adddata(data)
        if print_data_load:
            print(f"加载数据：{csv_file} rows={len(df)}")

    if not cerebro.datas:
        raise ValueError(f"没有足够长的数据可回测；请缩短 period 或扩大下载日期区间。period={period}")
    return cerebro


def add_analyzers(cerebro: bt.Cerebro) -> None:
    """挂载普通回测和优化都需要的分析器。"""

    cerebro.addanalyzer(ClosedTradeRecorder, _name="closed_trade_recorder")
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe", timeframe=bt.TimeFrame.Days)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trade_analyzer")
    cerebro.addanalyzer(FinalValueAnalyzer, _name="final_value")


def collect_optimization_results(results, initial_cash: float) -> list[dict[str, float | int]]:  # noqa: ANN001
    """从 optstrategy 二维返回结果中提取参数和绩效。"""

    rows: list[dict[str, float | int]] = []
    for run in results:
        strategy = run[0]
        trade_analyzer = strategy.analyzers.trade_analyzer.get_analysis()
        final_value = float(strategy.analyzers.final_value.get_analysis()["final_value"])
        total_return = final_value / initial_cash - 1
        rows.append(
            {
                "period": int(strategy.p.period),
                "std_dev_multiplier": float(strategy.p.std_dev_multiplier),
                "final_value": float(final_value),
                "total_trades": int(trade_analyzer.get("total", {}).get("closed", 0)),
                "total_return": float(total_return),
            }
        )
    return rows


def print_optimization_top5(rows: list[dict[str, float | int]]) -> None:
    """按最终资金打印前 5 名参数组合。"""

    print("\n参数优化 Top 5（按最终资金排序）")
    print("rank | period | std_dev_multiplier | final_value | total_return | total_trades")
    for rank, row in enumerate(rows[:5], start=1):
        print(
            f"{rank:>4} | "
            f"{row['period']:>6} | "
            f"{row['std_dev_multiplier']:>18.2f} | "
            f"{row['final_value']:>11.2f} | "
            f"{row['total_return']:>11.2%} | "
            f"{row['total_trades']:>12}"
        )


def load_valuation_csv(path: Path) -> pd.DataFrame:
    """读取并清洗 Baostock 数据 CSV。"""

    df = pd.read_csv(path)
    required = {"date", "open", "high", "low", "close", "volume", "openinterest", "pe_ttm", "pb", "roe"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path} 缺少必要字段：{sorted(missing)}")

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for column in ["open", "high", "low", "close", "volume", "openinterest", "pe_ttm", "pb", "roe"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df = df.dropna(subset=["date", "open", "high", "low", "close", "volume", "pb", "roe"])
    df = df[df["volume"] > 0].sort_values("date").drop_duplicates("date", keep="last")
    return df.set_index("date")


def _is_valid_number(*values: float) -> bool:
    """过滤 NaN/inf，避免指标未就绪时触发交易。"""

    return all(pd.notna(value) and value not in (float("inf"), float("-inf")) for value in values)


def _trade_entry_value(trade) -> float:  # noqa: ANN001
    """Extract opening notional from Backtrader trade history."""

    for event in trade.history:
        size = abs(float(event.event.size))
        price = abs(float(event.event.price))
        if size > 0 and price > 0:
            return size * price
    return abs(float(trade.pnl - trade.pnlcomm))


def main() -> int:
    parser = argparse.ArgumentParser(description="运行多标的 PB 估值通道轮动策略。")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Baostock CSV 数据目录。")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="交易和统计结果输出目录。")
    parser.add_argument("--cash", type=float, default=DEFAULT_CASH, help="初始资金。")
    parser.add_argument("--period", type=int, default=500, help="PB 通道滚动窗口。")
    parser.add_argument("--lookback", type=int, default=None, help="兼容旧参数；如提供则覆盖 --period。")
    parser.add_argument("--std-dev-multiplier", type=float, default=1.5, help="PB 通道标准差倍数。")
    parser.add_argument("--max-files", type=int, default=100, help="最多加载多少个 CSV 文件。")
    parser.add_argument("--optimize", action="store_true", help="启用 Backtrader 参数网格搜索。")
    parser.add_argument("--period-grid", default="250,500", help="优化 period 网格，逗号分隔。")
    parser.add_argument("--std-grid", default="0.8,1.0,1.2", help="优化标准差倍数网格，逗号分隔。")
    parser.add_argument("--maxcpus", type=int, default=1, help="优化进程数；macOS 建议保持 1。")
    args = parser.parse_args()

    period = args.lookback if args.lookback is not None else args.period
    if args.optimize:
        run_optimization(
            data_dir=args.data_dir,
            cash=args.cash,
            max_files=args.max_files,
            period_grid=parse_int_grid(args.period_grid),
            std_grid=parse_float_grid(args.std_grid),
            maxcpus=args.maxcpus,
        )
    else:
        run_backtest(
            data_dir=args.data_dir,
            output_dir=args.output_dir,
            cash=args.cash,
            period=period,
            std_dev_multiplier=args.std_dev_multiplier,
            max_files=args.max_files,
        )
    return 0


def parse_int_grid(value: str) -> list[int]:
    """Parse comma-separated integer grid."""

    return [int(item.strip()) for item in value.split(",") if item.strip()]


def parse_float_grid(value: str) -> list[float]:
    """Parse comma-separated float grid."""

    return [float(item.strip()) for item in value.split(",") if item.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
