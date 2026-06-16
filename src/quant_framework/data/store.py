"""Local Parquet storage for normalized market data."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


class ParquetStore:
    """Persist daily market data as one Parquet file per symbol."""

    def __init__(self, root: str | Path = "data/raw/daily") -> None:
        self.root = Path(root)

    def save_daily(self, symbol: str, df: pd.DataFrame) -> Path:
        if df.empty:
            raise ValueError("Cannot save an empty daily dataframe.")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(symbol)
        saved = df.copy()
        saved["date"] = pd.to_datetime(saved["date"])
        saved = saved.sort_values("date").reset_index(drop=True)
        saved.to_parquet(path, index=False)
        return path

    def load_daily(
        self,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        path = self._path(symbol)
        if not path.exists():
            raise FileNotFoundError(f"No cached daily data found for {symbol}: {path}")
        df = pd.read_parquet(path)
        df["date"] = pd.to_datetime(df["date"])
        if start_date:
            df = df[df["date"] >= pd.to_datetime(start_date)]
        if end_date:
            df = df[df["date"] <= pd.to_datetime(end_date)]
        return df.sort_values("date").reset_index(drop=True)

    def _path(self, symbol: str) -> Path:
        safe_symbol = symbol.replace("/", "_").replace("\\", "_")
        return self.root / f"{safe_symbol}.parquet"
