from __future__ import annotations

import pandas as pd

from quant_framework.data.provider import normalize_daily
from quant_framework.data.store import ParquetStore


def test_normalize_daily_renames_and_sorts_columns() -> None:
    raw = pd.DataFrame(
        {
            "日期": ["2024-01-03", "2024-01-02"],
            "开盘": [10, 9],
            "最高": [11, 10],
            "最低": [9, 8],
            "收盘": [10.5, 9.5],
            "成交量": [1000, 900],
            "成交额": [10_000, 8_000],
        }
    )

    df = normalize_daily(raw, "000001")

    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume", "amount", "symbol"]
    assert df["date"].dt.strftime("%Y-%m-%d").tolist() == ["2024-01-02", "2024-01-03"]
    assert df["symbol"].tolist() == ["000001", "000001"]


def test_parquet_store_round_trip_filters_dates(tmp_path) -> None:
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
            "open": [1, 2, 3],
            "high": [2, 3, 4],
            "low": [0.5, 1.5, 2.5],
            "close": [1.5, 2.5, 3.5],
            "volume": [100, 200, 300],
            "amount": [1000, 2000, 3000],
            "symbol": ["000001", "000001", "000001"],
        }
    )
    store = ParquetStore(tmp_path)

    store.save_daily("000001", df)
    loaded = store.load_daily("000001", "2024-01-02", "2024-01-03")

    assert loaded["date"].dt.strftime("%Y-%m-%d").tolist() == ["2024-01-02", "2024-01-03"]
    assert loaded["close"].tolist() == [2.5, 3.5]
