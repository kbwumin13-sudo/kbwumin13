"""CLI for data download and backtest execution."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from quant_framework.analysis.stats_validator import validate_trade_returns
from quant_framework.backtest import BacktestRunner
from quant_framework.config import BacktestConfig, StrategyConfig
from quant_framework.data import AkshareDataProvider, ParquetStore
from quant_framework.strategies import STRATEGIES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quant-framework")
    subparsers = parser.add_subparsers(dest="command", required=True)

    download = subparsers.add_parser("download", help="Download A-share daily data.")
    download.add_argument("--symbol", required=True)
    download.add_argument("--start-date", required=True)
    download.add_argument("--end-date", required=True)
    download.add_argument("--data-dir", default="data/raw/daily")

    backtest = subparsers.add_parser("backtest", help="Run a single-symbol daily backtest.")
    backtest.add_argument("--symbol", required=True)
    backtest.add_argument("--start-date", required=True)
    backtest.add_argument("--end-date", required=True)
    backtest.add_argument("--data-dir", default="data/raw/daily")
    backtest.add_argument("--strategy", choices=sorted(STRATEGIES), default="ma_cross")
    backtest.add_argument("--cash", type=float, default=100_000)
    backtest.add_argument("--commission", type=float, default=0.0003)
    backtest.add_argument("--slippage", type=float, default=0.0001)
    backtest.add_argument("--short-window", type=int, default=20)
    backtest.add_argument("--long-window", type=int, default=60)
    backtest.add_argument("--position-size", type=float, default=0.95)
    backtest.add_argument("--window-size", type=int, default=5)
    backtest.add_argument("--k1", type=float, default=0.2)
    backtest.add_argument("--k2", type=float, default=0.5)
    backtest.add_argument("--boll-period", type=int, default=20)
    backtest.add_argument("--boll-devfactor", type=float, default=2.0)
    backtest.add_argument("--entry-window", type=int, default=20)
    backtest.add_argument("--exit-window", type=int, default=10)
    backtest.add_argument("--atr-window", type=int, default=20)
    backtest.add_argument("--risk-fraction", type=float, default=0.01)
    backtest.add_argument("--max-units", type=int, default=4)
    backtest.add_argument("--stats-resamples", type=int, default=1000)
    backtest.add_argument("--output-dir", default="outputs")

    args = parser.parse_args(argv)
    if args.command == "download":
        return _download(args)
    if args.command == "backtest":
        return _backtest(args)
    raise ValueError(f"Unknown command: {args.command}")


def _download(args: argparse.Namespace) -> int:
    provider = AkshareDataProvider()
    store = ParquetStore(args.data_dir)
    df = provider.fetch_daily(args.symbol, args.start_date, args.end_date)
    path = store.save_daily(args.symbol, df)
    print(f"Saved {len(df)} rows to {path}")
    return 0


def _backtest(args: argparse.Namespace) -> int:
    config = BacktestConfig(
        symbol=args.symbol,
        start_date=args.start_date,
        end_date=args.end_date,
        cash=args.cash,
        commission=args.commission,
        slippage=args.slippage,
        data_dir=Path(args.data_dir),
        strategy=StrategyConfig(name=args.strategy, params=_strategy_params(args)),
    )
    result = BacktestRunner().run(config)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result.equity_curve.to_csv(output_dir / f"{args.symbol}_equity.csv", index=False)
    result.trades.to_csv(output_dir / f"{args.symbol}_trades.csv", index=False)
    pd.Series(result.metrics).to_csv(output_dir / f"{args.symbol}_metrics.csv", header=["value"])
    if len(result.trades) >= 2 and "return_pct" in result.trades.columns:
        validation = validate_trade_returns(result.trades, n_resamples=args.stats_resamples)
        pd.Series(
            {
                "t_statistic": validation.t_test.statistic,
                "t_p_value": validation.t_test.p_value,
                "t_mean_return": validation.t_test.mean_return,
                "t_sample_size": validation.t_test.sample_size,
                "t_significant": validation.t_test.significant,
                "bootstrap_mean_return": validation.bootstrap.mean_return,
                "bootstrap_lower_bound": validation.bootstrap.lower_bound,
                "bootstrap_upper_bound": validation.bootstrap.upper_bound,
                "bootstrap_confidence_level": validation.bootstrap.confidence_level,
                "bootstrap_n_resamples": validation.bootstrap.n_resamples,
            }
        ).to_csv(output_dir / f"{args.symbol}_stats_validation.csv", header=["value"])

    print("Backtest metrics:")
    for key, value in result.metrics.items():
        print(f"  {key}: {value:.6f}")
    return 0


def _strategy_params(args: argparse.Namespace) -> dict[str, float | int]:
    if args.strategy == "ma_cross":
        return {
            "short_window": args.short_window,
            "long_window": args.long_window,
            "position_size": args.position_size,
        }
    if args.strategy == "dual_thrust":
        return {
            "window_size": args.window_size,
            "k1": args.k1,
            "k2": args.k2,
            "position_size": args.position_size,
        }
    if args.strategy == "boll_breakout":
        return {
            "period": args.boll_period,
            "devfactor": args.boll_devfactor,
            "position_size": args.position_size,
        }
    if args.strategy == "turtle":
        return {
            "entry_window": args.entry_window,
            "exit_window": args.exit_window,
            "atr_window": args.atr_window,
            "risk_fraction": args.risk_fraction,
            "max_units": args.max_units,
        }
    raise ValueError(f"Unsupported strategy: {args.strategy}")


if __name__ == "__main__":
    raise SystemExit(main())
