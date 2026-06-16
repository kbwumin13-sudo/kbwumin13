"""Configuration objects shared across the framework."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StrategyConfig:
    """Strategy name and its parameter dictionary."""

    name: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BacktestConfig:
    """Inputs needed to run a single-symbol daily backtest."""

    symbol: str
    start_date: str
    end_date: str
    cash: float = 100_000.0
    commission: float = 0.0003
    slippage: float = 0.0001
    strategy: StrategyConfig = field(
        default_factory=lambda: StrategyConfig(
            name="ma_cross",
            params={"short_window": 20, "long_window": 60, "position_size": 0.95},
        )
    )
    data_dir: Path = Path("data/raw/daily")


@dataclass(frozen=True)
class BacktestResult:
    """Normalized result returned by backtest runners."""

    equity_curve: Any
    trades: Any
    metrics: dict[str, float]
    params: dict[str, Any]
    data_range: tuple[str, str]
