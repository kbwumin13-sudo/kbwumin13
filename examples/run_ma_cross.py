"""Run a sample moving-average crossover backtest from cached data."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from quant_framework.backtest import BacktestRunner  # noqa: E402
from quant_framework.config import BacktestConfig, StrategyConfig  # noqa: E402


def main() -> None:
    config = BacktestConfig(
        symbol="000001",
        start_date="2020-01-01",
        end_date="2024-12-31",
        data_dir=PROJECT_ROOT / "data/raw/daily",
        strategy=StrategyConfig(
            name="ma_cross",
            params={"short_window": 20, "long_window": 60, "position_size": 0.95},
        ),
    )
    result = BacktestRunner().run(config)
    print("Backtest metrics:")
    for key, value in result.metrics.items():
        print(f"  {key}: {value:.6f}")


if __name__ == "__main__":
    main()
