"""Placeholders for future statistical validation methods."""

from __future__ import annotations

import pandas as pd


def t_test_returns(_returns: pd.Series) -> None:
    """Reserved for a future return-series t-test implementation."""

    raise NotImplementedError("T-test validation will be implemented in a later milestone.")


def bootstrap_confidence_interval(_returns: pd.Series, _n_resamples: int = 1000) -> None:
    """Reserved for a future bootstrap confidence interval implementation."""

    raise NotImplementedError("Bootstrap validation will be implemented in a later milestone.")
