from __future__ import annotations

import pandas as pd

from quant_framework.analysis.metrics import calculate_metrics


def test_calculate_metrics_from_equity_curve() -> None:
    equity_curve = pd.DataFrame({"equity": [100.0, 110.0, 105.0, 120.0]})

    metrics = calculate_metrics(equity_curve)

    assert round(metrics["total_return"], 6) == 0.2
    assert metrics["annual_return"] > 0
    assert metrics["annual_volatility"] > 0
    assert metrics["max_drawdown"] < 0
