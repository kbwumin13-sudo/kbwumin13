"""Data feed adapters for backtrader."""

from __future__ import annotations

import pandas as pd
import backtrader as bt


def daily_dataframe_feed(df: pd.DataFrame) -> bt.feeds.PandasData:
    """Build a backtrader PandasData feed from normalized daily OHLCV data."""

    required = {"date", "open", "high", "low", "close", "volume"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Backtest data is missing required columns: {missing}")

    feed_df = df.copy()
    feed_df["date"] = pd.to_datetime(feed_df["date"])
    feed_df = feed_df.sort_values("date").set_index("date")
    return bt.feeds.PandasData(
        dataname=feed_df,
        open="open",
        high="high",
        low="low",
        close="close",
        volume="volume",
        openinterest=None,
    )
