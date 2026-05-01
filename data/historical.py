from __future__ import annotations

"""
HistoricalDataLoader — Task 3.2

Loads OHLCV price history from two sources:
  - yfinance:  Free, no API key, works offline. Primary source for backtesting.
  - IBKR:      Via ib_insync reqHistoricalData. Rate-limited; use for
               IBKR-specific data or when yfinance is unavailable.

Both methods return a pandas DataFrame with a DatetimeIndex and standard
columns: open, high, low, close, volume. The format is identical regardless
of source — strategies and the backtester don't need to know where data came from.

Usage:
    df = HistoricalDataLoader.load_yfinance("AAPL", start="2024-01-01", end="2024-12-31")
    df = HistoricalDataLoader.load_ibkr("AAPL", duration="1 Y", bar_size="1 day", client=client)
"""

import logging
import time

import pandas as pd

logger = logging.getLogger(__name__)

# Standard column names used throughout the project
_COLUMNS = ["open", "high", "low", "close", "volume"]

# IBKR enforces a pacing limit of roughly 1 historical data request per 10 seconds
# for the same contract. We enforce 11 seconds to give a small safety margin.
_IBKR_MIN_INTERVAL = 11.0


class HistoricalDataLoader:
    """
    Static utility for loading OHLCV data. No state, no side effects.

    All methods return a pd.DataFrame with:
      - DatetimeIndex (UTC, timezone-aware)
      - Columns: open, high, low, close, volume (all lowercase)
      - Sorted ascending by date
      - No NaN rows (dropped automatically)
    """

    # Class-level timestamp of the last load_ibkr() call — used for rate limiting.
    # Float (seconds since epoch). 0.0 = no previous call this session.
    _last_ibkr_call: float = 0.0

    @staticmethod
    def load_yfinance(
        symbol: str,
        start: str,
        end: str,
        interval: str = "1d",
    ) -> pd.DataFrame:
        """
        Load historical OHLCV data from Yahoo Finance (free, no API key).

        Recommended for backtesting — reliable, fast, covers most US stocks
        going back 20+ years for daily data.

        Args:
            symbol:   Ticker symbol, e.g. "AAPL"
            start:    Start date as "YYYY-MM-DD"
            end:      End date as "YYYY-MM-DD" (exclusive)
            interval: Bar size. Common values:
                        "1d"  — daily bars (most reliable)
                        "1h"  — hourly (last 730 days only)
                        "5m"  — 5-minute (last 60 days only)
                        "1m"  — 1-minute (last 7 days only)

        Returns:
            DataFrame with columns [open, high, low, close, volume].

        Raises:
            ImportError:  yfinance is not installed.
            ValueError:   No data returned (bad symbol or date range).
            RuntimeError: Download failed.
        """
        try:
            import yfinance as yf
        except ImportError:
            raise ImportError("yfinance is not installed. Run: pip install yfinance")

        logger.info(
            "Downloading %s from yfinance | %s → %s | interval=%s",
            symbol,
            start,
            end,
            interval,
        )

        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start, end=end, interval=interval, auto_adjust=True)

        if df.empty:
            raise ValueError(
                f"No data returned for {symbol} ({start} → {end}, interval={interval}). "
                "Check the symbol and date range."
            )

        # Normalize column names to lowercase
        df.columns = [c.lower() for c in df.columns]

        # Keep only the standard OHLCV columns
        available = [c for c in _COLUMNS if c in df.columns]
        df = df[available].copy()

        # Ensure timezone-aware UTC index
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        df = df.sort_index()
        df = df.dropna()

        logger.info(
            "Loaded %d bars for %s (%s → %s).",
            len(df),
            symbol,
            df.index[0].date(),
            df.index[-1].date(),
        )
        return df

    @staticmethod
    def load_ibkr(
        symbol: str,
        duration: str,
        bar_size: str,
        client,  # IBKRClient — avoids circular import at module level
        what_to_show: str = "TRADES",
        use_rth: bool = True,
    ) -> pd.DataFrame:
        """
        Load historical OHLCV data from IBKR via reqHistoricalData.

        Use when you need IBKR-specific data or yfinance doesn't have
        what you need. Note: IBKR rate-limits historical data requests —
        wait 10+ seconds between calls for different symbols.

        Args:
            symbol:       Ticker symbol, e.g. "AAPL"
            duration:     How far back to go. Examples:
                            "1 Y"  — 1 year
                            "6 M"  — 6 months
                            "30 D" — 30 days
            bar_size:     Bar size string. Examples:
                            "1 day", "1 hour", "30 mins", "5 mins", "1 min"
            client:       Connected IBKRClient instance.
            what_to_show: Data type. "TRADES" for price/volume. "MIDPOINT" for
                          bid/ask midpoint (useful for illiquid instruments).
            use_rth:      True = regular trading hours only. False = include
                          pre/post market.

        Returns:
            DataFrame with columns [open, high, low, close, volume].

        Raises:
            ConnectionError: client is not connected.
            RuntimeError:    IBKR returned no data.
        """
        if not client.is_connected:
            raise ConnectionError("IBKRClient is not connected.")

        # Enforce IBKR pacing: wait if the previous request was too recent.
        elapsed = time.time() - HistoricalDataLoader._last_ibkr_call
        if HistoricalDataLoader._last_ibkr_call > 0 and elapsed < _IBKR_MIN_INTERVAL:
            wait = _IBKR_MIN_INTERVAL - elapsed
            logger.info(
                "IBKR rate limit: waiting %.1fs before historical data request for %s.",
                wait,
                symbol,
            )
            time.sleep(wait)
        HistoricalDataLoader._last_ibkr_call = time.time()

        from ib_insync import Stock

        logger.info(
            "Requesting historical data from IBKR | %s | %s | %s",
            symbol,
            duration,
            bar_size,
        )

        contract = client.qualify_contract(Stock(symbol, "SMART", "USD"))
        bars = client.ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=use_rth,
            formatDate=1,
        )

        if not bars:
            raise RuntimeError(
                f"IBKR returned no historical data for {symbol} "
                f"(duration={duration}, bar_size={bar_size}). "
                "Check that the symbol and bar size are valid, and that you "
                "are not exceeding IBKR's rate limits."
            )

        # Convert ib_insync BarData objects to DataFrame
        records = [
            {
                "timestamp": bar.date,
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": int(bar.volume),
            }
            for bar in bars
        ]
        df = pd.DataFrame(records).set_index("timestamp")

        # Normalize index to UTC
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        df = df.sort_index().dropna()

        logger.info(
            "Loaded %d bars for %s from IBKR (%s → %s).",
            len(df),
            symbol,
            df.index[0].date(),
            df.index[-1].date(),
        )
        return df

    @staticmethod
    def load_csv(filepath: str, symbol: str) -> pd.DataFrame:
        """
        Load OHLCV data from a local CSV file.

        The CSV must have a date/datetime column (auto-detected) and
        columns named open, high, low, close, volume (case-insensitive).

        Useful for custom data, alternative data providers, or offline testing.

        Args:
            filepath: Path to the CSV file.
            symbol:   Symbol name to attach to the data (metadata only).

        Returns:
            DataFrame with columns [open, high, low, close, volume].

        Raises:
            FileNotFoundError: File does not exist.
            ValueError:        Required columns are missing.
        """
        logger.info("Loading CSV: %s for symbol %s", filepath, symbol)

        df = pd.read_csv(filepath)
        df.columns = [c.lower().strip() for c in df.columns]

        # Auto-detect date column
        date_col = next(
            (c for c in df.columns if c in ("date", "datetime", "timestamp", "time")),
            None,
        )
        if date_col is None:
            raise ValueError(
                f"Could not find a date column in {filepath}. "
                "Rename the date column to 'date', 'datetime', or 'timestamp'."
            )

        df[date_col] = pd.to_datetime(df[date_col], utc=True)
        df = df.set_index(date_col)
        df.index.name = "timestamp"

        # Validate required columns
        missing = [c for c in _COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(
                f"CSV is missing required columns: {missing}. " f"Found: {list(df.columns)}"
            )

        df = df[_COLUMNS].sort_index().dropna()

        logger.info(
            "Loaded %d bars for %s from CSV (%s → %s).",
            len(df),
            symbol,
            df.index[0].date(),
            df.index[-1].date(),
        )
        return df
