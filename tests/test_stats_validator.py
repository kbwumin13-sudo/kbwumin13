from __future__ import annotations

import pandas as pd
import pytest

from quant_framework.analysis.stats_validator import (
    bootstrap_mean_confidence_interval,
    one_sample_t_test,
    validate_trade_returns,
)


def test_validate_trade_returns_combines_t_test_and_bootstrap() -> None:
    trades = pd.DataFrame({"return_pct": [0.02, 0.01, 0.03, -0.005, 0.015]})

    result = validate_trade_returns(trades, n_resamples=1000, random_state=7)

    assert result.t_test.sample_size == 5
    assert result.t_test.mean_return > 0
    assert result.bootstrap.n_resamples == 1000
    assert result.bootstrap.lower_bound <= result.bootstrap.mean_return <= result.bootstrap.upper_bound


def test_t_test_requires_multiple_returns() -> None:
    with pytest.raises(ValueError, match="at least two"):
        one_sample_t_test(pd.Series([0.01]))


def test_bootstrap_requires_at_least_1000_resamples() -> None:
    with pytest.raises(ValueError, match="at least 1000"):
        bootstrap_mean_confidence_interval(pd.Series([0.01, 0.02]), n_resamples=999)
