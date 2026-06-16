"""Market data providers."""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


STANDARD_COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount", "symbol"]


class DataProvider(ABC):
    """Abstract market data provider interface."""

    @abstractmethod
    def fetch_daily(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Fetch daily OHLCV data using YYYYMMDD or YYYY-MM-DD dates."""


class AkshareDataProvider(DataProvider):
    """A-share daily data provider backed by akshare."""

    def fetch_daily(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        import akshare as ak

        start = _compact_date(start_date)
        end = _compact_date(end_date)
        raw = ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start,
            end_date=end,
            adjust="qfq",
        )
        if raw.empty:
            raise ValueError(f"No daily data returned for {symbol} from {start_date} to {end_date}.")
        return normalize_daily(raw, symbol)


def normalize_daily(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Convert akshare-style Chinese columns to the framework schema."""

    rename_map = {
        "日期": "date",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "volume",
        "成交额": "amount",
    }
    normalized = df.rename(columns=rename_map).copy()
    missing = [column for column in STANDARD_COLUMNS[:-1] if column not in normalized.columns]
    if missing:
        raise ValueError(f"Daily data is missing required columns: {missing}")

    normalized["date"] = pd.to_datetime(normalized["date"])
    normalized["symbol"] = symbol
    normalized = normalized[STANDARD_COLUMNS].sort_values("date").reset_index(drop=True)

    numeric_columns = ["open", "high", "low", "close", "volume", "amount"]
    normalized[numeric_columns] = normalized[numeric_columns].apply(pd.to_numeric, errors="coerce")
    normalized = normalized.dropna(subset=["open", "high", "low", "close"])
    if normalized.empty:
        raise ValueError(f"Daily data for {symbol} is empty after normalization.")
    return normalized


def _compact_date(value: str) -> str:
    return value.replace("-", "")
