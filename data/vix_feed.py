"""
VIXFeed — daily VIX close for RSI2-MR strategy.

Backtest mode: pre-loaded from yfinance (^VIX), served per bar date.
Live mode: single yfinance fetch per signal evaluation; caches last good
value with timestamp; if stale > 24h or fetch fails, blocks entry AND
fires one ntfy alert per stale day (not per tick).
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

_STALE_THRESHOLD_HOURS = 24
_NTFY_COOLDOWN_HOURS = 24  # fire at most one ntfy per day for VIX staleness


class VIXFeed:
    """
    Daily VIX close provider.

    Args:
        series: Optional pre-loaded Series (date → float). Used in backtests.
                If None, the feed fetches from yfinance on each get_latest_close() call.
    """

    def __init__(self, series: Optional[pd.Series] = None) -> None:
        self._series: Optional[pd.Series] = series
        self._cached_value: Optional[float] = None
        self._cached_at: Optional[datetime] = None
        self._last_ntfy_at: Optional[datetime] = None  # rate-limit alerts

    # ── Backtest mode ─────────────────────────────────────────────────────────

    def get_for_date(self, d: date) -> Optional[float]:
        """
        Return VIX close for a specific date (backtest use only).
        Returns None if data is missing for that date.
        """
        if self._series is None:
            return None
        key = pd.Timestamp(d)
        try:
            val = self._series.get(key)
            if val is None:
                # Try date-only index
                for idx_val in self._series.index:
                    idx_date = idx_val.date() if hasattr(idx_val, "date") else idx_val
                    if idx_date == d:
                        return float(self._series[idx_val])
            return float(val) if val is not None else None
        except Exception:
            return None

    # ── Live mode ─────────────────────────────────────────────────────────────

    def get_latest_close(self) -> Optional[float]:
        """
        Fetch the most recent VIX daily close from yfinance.

        Returns None (blocks entry) if:
          - yfinance is unavailable
          - cached value is stale (> 24h)
        Fires a single ntfy alert per 24h period when stale/unavailable.
        """
        now = datetime.now(timezone.utc)

        # Try a fresh fetch
        try:
            import yfinance as yf

            df = yf.Ticker("^VIX").history(period="5d", interval="1d", auto_adjust=True)
            if not df.empty:
                val = float(df["Close"].dropna().iloc[-1])
                self._cached_value = val
                self._cached_at = now
                return val
        except Exception as exc:
            logger.warning("VIXFeed: yfinance fetch failed: %s", exc)

        # Fall back to cached if fresh enough
        if self._cached_value is not None and self._cached_at is not None:
            age_hours = (now - self._cached_at).total_seconds() / 3600
            if age_hours <= _STALE_THRESHOLD_HOURS:
                logger.debug(
                    "VIXFeed: using cached VIX %.2f (age=%.1fh)", self._cached_value, age_hours
                )
                return self._cached_value
            else:
                logger.warning(
                    "VIXFeed: cached VIX is stale (%.1fh old) — blocking entry.", age_hours
                )
                self._fire_stale_alert(now)
                return None

        # No cache at all
        logger.warning("VIXFeed: no VIX data available — blocking entry.")
        self._fire_stale_alert(now)
        return None

    def _fire_stale_alert(self, now: datetime) -> None:
        """Fire ntfy alert at most once per _NTFY_COOLDOWN_HOURS."""
        if self._last_ntfy_at is not None:
            hours_since = (now - self._last_ntfy_at).total_seconds() / 3600
            if hours_since < _NTFY_COOLDOWN_HOURS:
                return
        self._last_ntfy_at = now
        try:
            topic = os.environ.get("NTFY_TOPIC", "")
            if not topic:
                return
            import urllib.request

            req = urllib.request.Request(
                f"https://ntfy.sh/{topic}",
                data=b"RSI2MR: VIX data stale/unavailable -- entry blocked until resolved",
                headers={"Title": "TradeBot VIX alert", "Priority": "high"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception as exc:
            logger.debug("VIXFeed: ntfy alert failed (non-critical): %s", exc)


# ── Factory ───────────────────────────────────────────────────────────────────


def load_vix_series(start: str, end: str) -> pd.Series:
    """
    Load ^VIX daily closes from yfinance as a date-indexed Series.
    Used to build the backtest VIXFeed.

    Args:
        start: "YYYY-MM-DD"
        end:   "YYYY-MM-DD"

    Returns:
        pd.Series indexed by Timestamp, values are float VIX closes.
        Forward-fills up to 5 consecutive missing days.
    """
    import yfinance as yf

    logger.info("Downloading ^VIX from yfinance | %s → %s", start, end)
    df = yf.Ticker("^VIX").history(start=start, end=end, interval="1d", auto_adjust=True)
    if df.empty:
        raise ValueError(f"No VIX data returned for {start} → {end}")

    series = df["Close"].dropna()
    # Forward-fill up to 5 bars (handles occasional CBOE missing days)
    series = series.ffill(limit=5)

    missing_pct = series.isna().mean() * 100
    if missing_pct > 5:
        logger.warning(
            "VIX data has %.1f%% missing days (after forward-fill) — "
            "data quality issue; check source.",
            missing_pct,
        )

    logger.info("VIX series loaded: %d bars, %.1f%% filled", len(series), missing_pct)
    return series
