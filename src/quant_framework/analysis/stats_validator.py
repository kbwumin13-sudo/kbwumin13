"""Statistical validation helpers for backtest trade returns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd
from scipy import stats


@dataclass(frozen=True)
class TTestResult:
    """One-sample t-test result for trade returns."""

    statistic: float
    p_value: float
    mean_return: float
    sample_size: int
    significant: bool


@dataclass(frozen=True)
class BootstrapResult:
    """Bootstrap confidence interval for the mean trade return."""

    mean_return: float
    lower_bound: float
    upper_bound: float
    confidence_level: float
    n_resamples: int


@dataclass(frozen=True)
class BootstrapSharpeResult:
    """Bootstrap confidence interval for trade-level Sharpe ratio."""

    sharpe_ratio: float
    lower_bound: float
    upper_bound: float
    confidence_level: float
    n_resamples: int


@dataclass(frozen=True)
class StatisticalValidationResult:
    """Combined statistical validation report."""

    t_test: TTestResult
    bootstrap: BootstrapResult
    bootstrap_sharpe: BootstrapSharpeResult


def validate_trade_returns(
    trades: pd.DataFrame | Iterable[float],
    return_column: str = "return_pct",
    n_resamples: int = 1000,
    confidence_level: float = 0.95,
    alpha: float = 0.05,
    random_state: int | None = 42,
) -> StatisticalValidationResult:
    """Run t-test and bootstrap validation on trade-level returns."""

    returns = extract_trade_returns(trades, return_column)
    return StatisticalValidationResult(
        t_test=one_sample_t_test(returns, alpha=alpha),
        bootstrap=bootstrap_mean_confidence_interval(
            returns,
            n_resamples=n_resamples,
            confidence_level=confidence_level,
            random_state=random_state,
        ),
        bootstrap_sharpe=bootstrap_sharpe_confidence_interval(
            returns,
            n_resamples=n_resamples,
            confidence_level=confidence_level,
            random_state=random_state,
        ),
    )


def extract_trade_returns(
    trades: pd.DataFrame | Iterable[float],
    return_column: str = "return_pct",
) -> pd.Series:
    """Extract a clean return series from a trade dataframe or iterable."""

    if isinstance(trades, pd.DataFrame):
        if return_column not in trades.columns:
            raise ValueError(f"Trade dataframe is missing return column: {return_column}")
        series = trades[return_column]
    else:
        series = pd.Series(list(trades), dtype="float64")

    returns = pd.to_numeric(series, errors="coerce").dropna()
    if returns.empty:
        raise ValueError("At least one trade return is required for statistical validation.")
    return returns.astype(float)


def one_sample_t_test(returns: pd.Series, alpha: float = 0.05) -> TTestResult:
    """Test whether the mean trade return is significantly greater than zero."""

    if len(returns) < 2:
        raise ValueError("T-test requires at least two trade returns.")

    result = stats.ttest_1samp(returns, popmean=0.0, alternative="greater")
    return TTestResult(
        statistic=float(result.statistic),
        p_value=float(result.pvalue),
        mean_return=float(returns.mean()),
        sample_size=int(len(returns)),
        significant=bool(result.pvalue < alpha),
    )


def bootstrap_mean_confidence_interval(
    returns: pd.Series,
    n_resamples: int = 1000,
    confidence_level: float = 0.95,
    random_state: int | None = 42,
) -> BootstrapResult:
    """Estimate a percentile bootstrap confidence interval for mean returns."""

    if n_resamples < 1000:
        raise ValueError("n_resamples must be at least 1000.")
    if not 0 < confidence_level < 1:
        raise ValueError("confidence_level must be in (0, 1).")

    values = returns.to_numpy(dtype=float)
    rng = np.random.default_rng(random_state)
    sampled_means = np.empty(n_resamples)
    for index in range(n_resamples):
        sample = rng.choice(values, size=len(values), replace=True)
        sampled_means[index] = sample.mean()

    tail = (1 - confidence_level) / 2
    lower, upper = np.quantile(sampled_means, [tail, 1 - tail])
    return BootstrapResult(
        mean_return=float(values.mean()),
        lower_bound=float(lower),
        upper_bound=float(upper),
        confidence_level=float(confidence_level),
        n_resamples=int(n_resamples),
    )


def bootstrap_sharpe_confidence_interval(
    returns: pd.Series,
    n_resamples: int = 1000,
    confidence_level: float = 0.95,
    random_state: int | None = 42,
) -> BootstrapSharpeResult:
    """Estimate a percentile bootstrap CI for trade-level Sharpe ratio."""

    if n_resamples < 1000:
        raise ValueError("n_resamples must be at least 1000.")
    if not 0 < confidence_level < 1:
        raise ValueError("confidence_level must be in (0, 1).")

    values = returns.to_numpy(dtype=float)
    rng = np.random.default_rng(random_state)
    sampled_sharpes = np.empty(n_resamples)
    for index in range(n_resamples):
        sample = rng.choice(values, size=len(values), replace=True)
        sampled_sharpes[index] = _sharpe_ratio(sample)

    tail = (1 - confidence_level) / 2
    lower, upper = np.quantile(sampled_sharpes, [tail, 1 - tail])
    return BootstrapSharpeResult(
        sharpe_ratio=float(_sharpe_ratio(values)),
        lower_bound=float(lower),
        upper_bound=float(upper),
        confidence_level=float(confidence_level),
        n_resamples=int(n_resamples),
    )


def print_statistical_validation(
    trade_returns: pd.DataFrame | Iterable[float],
    return_column: str = "return_pct",
    n_resamples: int = 1000,
    confidence_level: float = 0.95,
    alpha: float = 0.05,
    random_state: int | None = 42,
) -> StatisticalValidationResult:
    """Run validation and print a clear terminal report."""

    result = validate_trade_returns(
        trade_returns,
        return_column=return_column,
        n_resamples=n_resamples,
        confidence_level=confidence_level,
        alpha=alpha,
        random_state=random_state,
    )
    print("\n统计体检")
    print(f"T检验样本数: {result.t_test.sample_size}")
    print(f"T检验均值收益: {result.t_test.mean_return:.6f}")
    print(f"T检验 Statistic: {result.t_test.statistic:.6f}")
    print(f"T检验 P-value: {result.t_test.p_value:.6f}")
    print(f"T检验是否显著(alpha={alpha}): {result.t_test.significant}")
    print(
        "Bootstrap 95% 平均收益置信区间: "
        f"[{result.bootstrap.lower_bound:.6f}, {result.bootstrap.upper_bound:.6f}]"
    )
    print(
        "Bootstrap 95% 夏普比率置信区间: "
        f"[{result.bootstrap_sharpe.lower_bound:.6f}, {result.bootstrap_sharpe.upper_bound:.6f}]"
    )
    print(f"Bootstrap 95% 夏普比率置信区间下限: {result.bootstrap_sharpe.lower_bound:.6f}")
    return result


def _sharpe_ratio(values: np.ndarray) -> float:
    std = values.std(ddof=1)
    if len(values) < 2 or std == 0 or np.isnan(std):
        return 0.0
    return float(values.mean() / std)
