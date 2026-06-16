from __future__ import annotations

import pandas as pd

from quant_framework.backtest import BacktestRunner
from quant_framework.config import BacktestConfig, StrategyConfig
from quant_framework.data.store import ParquetStore


def test_backtest_runner_returns_metrics_and_equity_curve(tmp_path) -> None:
    dates = pd.date_range("2024-01-01", periods=100, freq="D")
    closes = list(range(10, 110))
    df = pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": [value + 1 for value in closes],
            "low": [value - 1 for value in closes],
            "close": closes,
            "volume": [1000] * len(closes),
            "amount": [10_000] * len(closes),
            "symbol": ["000001"] * len(closes),
        }
    )
    store = ParquetStore(tmp_path)
    store.save_daily("000001", df)

    result = BacktestRunner(store).run(
        BacktestConfig(
            symbol="000001",
            start_date="2024-01-01",
            end_date="2024-04-30",
            strategy=StrategyConfig(
                name="ma_cross",
                params={"short_window": 3, "long_window": 8, "position_size": 0.95},
            ),
        )
    )

    assert not result.equity_curve.empty
    assert "final_value" in result.metrics
    assert result.data_range == ("2024-01-01", "2024-04-09")
