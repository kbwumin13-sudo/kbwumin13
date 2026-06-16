"""Performance metrics for equity curves and return series."""

from __future__ import annotations

import math

import pandas as pd


TRADING_DAYS_PER_YEAR = 252


def calculate_metrics(equity_curve: pd.DataFrame | pd.Series) -> dict[str, float]:
    """Calculate basic backtest performance metrics from an equity curve."""

    equity = _extract_equity(equity_curve)
    if len(equity) < 2:
        return {
            "total_return": 0.0,
            "annual_return": 0.0,
            "annual_volatility": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown": 0.0,
        }

    returns = equity.pct_change().dropna()
    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1)
    annual_return = float((1 + total_return) ** (TRADING_DAYS_PER_YEAR / max(len(equity) - 1, 1)) - 1)
    annual_volatility = float(returns.std(ddof=0) * math.sqrt(TRADING_DAYS_PER_YEAR))
    sharpe_ratio = float(annual_return / annual_volatility) if annual_volatility else 0.0
    drawdown = equity / equity.cummax() - 1

    return {
        "total_return": total_return,
        "annual_return": annual_return,
        "annual_volatility": annual_volatility,
        "sharpe_ratio": sharpe_ratio,
        "max_drawdown": float(drawdown.min()),
    }


def _extract_equity(equity_curve: pd.DataFrame | pd.Series) -> pd.Series:
    if isinstance(equity_curve, pd.Series):
        equity = equity_curve.copy()
    elif "equity" in equity_curve.columns:
        equity = equity_curve["equity"].copy()
    elif "value" in equity_curve.columns:
        equity = equity_curve["value"].copy()
    else:
        raise ValueError("Equity curve must be a Series or contain an 'equity'/'value' column.")
    return pd.to_numeric(equity, errors="coerce").dropna()
