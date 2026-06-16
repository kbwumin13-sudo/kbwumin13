"""Backtrader-based backtest runner."""

from __future__ import annotations

from dataclasses import asdict

import backtrader as bt
import pandas as pd

from quant_framework.analysis.metrics import calculate_metrics
from quant_framework.backtest.feeds import daily_dataframe_feed
from quant_framework.config import BacktestConfig, BacktestResult
from quant_framework.data.store import ParquetStore
from quant_framework.strategies import STRATEGIES


class EquityRecorder(bt.Analyzer):
    """Record broker value on every bar."""

    def start(self) -> None:
        self.equity = []

    def next(self) -> None:
        self.equity.append(
            {
                "date": self.strategy.datetime.date(0),
                "equity": float(self.strategy.broker.getvalue()),
            }
        )

    def get_analysis(self):  # noqa: ANN201 - backtrader analyzer API.
        return self.equity


class TradeRecorder(bt.Analyzer):
    """Collect completed trades into a lightweight list of dictionaries."""

    def start(self) -> None:
        self.trades = []

    def notify_trade(self, trade) -> None:  # noqa: ANN001 - backtrader trade object.
        if not trade.isclosed:
            return
        self.trades.append(
            {
                "ref": trade.ref,
                "pnl": float(trade.pnl),
                "pnl_comm": float(trade.pnlcomm),
                "return_pct": float(trade.pnlcomm / abs(trade.value)) if trade.value else 0.0,
                "bar_open": trade.baropen,
                "bar_close": trade.barclose,
                "size": float(trade.size),
            }
        )

    def get_analysis(self):  # noqa: ANN201 - backtrader analyzer API.
        return self.trades


class BacktestRunner:
    """Run single-symbol daily backtests using cached Parquet data."""

    def __init__(self, store: ParquetStore | None = None) -> None:
        self.store = store

    def run(self, config: BacktestConfig) -> BacktestResult:
        store = self.store or ParquetStore(config.data_dir)
        data = store.load_daily(config.symbol, config.start_date, config.end_date)
        if data.empty:
            raise ValueError("No data available for the requested backtest range.")

        strategy_cls = STRATEGIES.get(config.strategy.name)
        if strategy_cls is None:
            raise ValueError(f"Unsupported strategy: {config.strategy.name}")

        cerebro = bt.Cerebro(stdstats=False)
        cerebro.broker.setcash(config.cash)
        cerebro.broker.setcommission(commission=config.commission)
        cerebro.broker.set_slippage_perc(config.slippage)
        cerebro.adddata(daily_dataframe_feed(data), name=config.symbol)
        cerebro.addstrategy(strategy_cls, **config.strategy.params)
        cerebro.addanalyzer(EquityRecorder, _name="equity_recorder")
        cerebro.addanalyzer(TradeRecorder, _name="trade_recorder")

        runs = cerebro.run()
        strategy = runs[0]
        equity_curve = pd.DataFrame(strategy.analyzers.equity_recorder.get_analysis())
        trades = pd.DataFrame(strategy.analyzers.trade_recorder.get_analysis())
        metrics = calculate_metrics(equity_curve)
        metrics["final_value"] = float(equity_curve["equity"].iloc[-1])
        metrics["trade_count"] = float(len(trades))

        return BacktestResult(
            equity_curve=equity_curve,
            trades=trades,
            metrics=metrics,
            params=asdict(config),
            data_range=(
                str(pd.to_datetime(data["date"].iloc[0]).date()),
                str(pd.to_datetime(data["date"].iloc[-1]).date()),
            ),
        )
