from __future__ import annotations

import pandas as pd
import pytest

from quant_framework.backtest import BacktestRunner
from quant_framework.config import BacktestConfig, StrategyConfig
from quant_framework.data.store import ParquetStore
from quant_framework.strategies import STRATEGIES


def _market_data() -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=140, freq="D")
    closes = []
    price = 20.0
    for index in range(len(dates)):
        if index < 40:
            price += 0.05
        elif index < 90:
            price += 0.6
        else:
            price -= 0.35
        closes.append(round(price, 2))
    return pd.DataFrame(
        {
            "date": dates,
            "open": [value * 0.995 for value in closes],
            "high": [value * 1.02 for value in closes],
            "low": [value * 0.98 for value in closes],
            "close": closes,
            "volume": [1000] * len(closes),
            "amount": [10_000] * len(closes),
            "symbol": ["000001"] * len(closes),
        }
    )


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("dual_thrust", {"window_size": 5, "k1": 0.2, "k2": 0.5, "position_size": 0.95}),
        ("boll_breakout", {"period": 20, "devfactor": 2.0, "position_size": 0.95}),
        (
            "turtle",
            {
                "entry_window": 20,
                "exit_window": 10,
                "atr_window": 20,
                "risk_fraction": 0.01,
                "max_units": 4,
            },
        ),
    ],
)
def test_external_strategy_adapters_run_in_backtrader(tmp_path, name, params) -> None:
    store = ParquetStore(tmp_path)
    store.save_daily("000001", _market_data())

    result = BacktestRunner(store).run(
        BacktestConfig(
            symbol="000001",
            start_date="2024-01-01",
            end_date="2024-05-30",
            strategy=StrategyConfig(name=name, params=params),
        )
    )

    assert name in STRATEGIES
    assert not result.equity_curve.empty
    assert "final_value" in result.metrics
    assert "trade_count" in result.metrics
