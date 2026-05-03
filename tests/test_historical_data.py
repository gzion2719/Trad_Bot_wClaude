"""Section 15: Historical data loader tests — network (yfinance), no IBKR needed."""

import os
import tempfile
import textwrap

import pandas as pd
import pytest

from data.historical import HistoricalDataLoader


def test_hdl01_yfinance_returns_correct_columns():
    df = HistoricalDataLoader.load_yfinance("MSFT", start="2024-01-01", end="2024-02-01")
    assert not df.empty
    for col in ("open", "high", "low", "close", "volume"):
        assert col in df.columns


def test_hdl02_yfinance_index_is_utc():
    df = HistoricalDataLoader.load_yfinance("MSFT", start="2024-01-01", end="2024-02-01")
    assert isinstance(df.index, pd.DatetimeIndex)
    assert str(df.index.tz) == "UTC"


def test_hdl03_yfinance_sorted_ascending():
    df = HistoricalDataLoader.load_yfinance("MSFT", start="2024-01-01", end="2024-02-01")
    assert df.index.is_monotonic_increasing


def test_hdl04_yfinance_bad_symbol_raises():
    with pytest.raises(ValueError):
        HistoricalDataLoader.load_yfinance("XYZXYZ999FAKE", start="2024-01-01", end="2024-02-01")


def test_hdl05_load_csv():
    csv_content = textwrap.dedent("""\
        date,open,high,low,close,volume
        2024-01-02,150.0,155.0,149.0,153.0,1000000
        2024-01-03,153.0,157.0,152.0,156.0,1200000
        2024-01-04,156.0,158.0,154.0,155.0,900000
    """)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(csv_content)
        path = f.name
    try:
        df = HistoricalDataLoader.load_csv(path, symbol="TEST")
        assert len(df) == 3
        assert df["close"].iloc[0] == 153.0
    finally:
        os.unlink(path)
