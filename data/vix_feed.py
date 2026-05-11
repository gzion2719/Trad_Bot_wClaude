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
_FETCH_FAILURE_ALERT_THRESHOLD = 2
# Threshold rationale: VIXFeed.get_latest_close() is called once per RSI2MR
# tick (DailyAt 16:10 ET). Failures only gate NEW entries — exits don't read
# VIX. Threshold=2 ≈ 48h of consecutive failures, comparable to the existing
# 24h stale-cache window but catches sustained intermittent flakiness when
# cache happens to be fresh. Threshold=1 would alert on every transient
# yfinance hiccup. No held/flat asymmetry needed here (unlike MS-C in
# strategies/rsi2_mr.py) because VIX outage never blocks an exit.


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
        # In-memory cooldowns: a restart during a multi-day outage resets
        # both and may re-fire on first failure post-restart. Acceptable
        # tradeoff mirroring MS-C in strategies/rsi2_mr.py; persisting is
        # tracked as MS-C3-persist in BACKLOG. The two cooldowns are
        # SEPARATE so a transient fetch-failure alert can never silence the
        # later (more serious) stale-cache alert that signals entry is
        # actually blocked.
        self._last_ntfy_at_stale: Optional[datetime] = None
        self._last_ntfy_at_fetch_failure: Optional[datetime] = None
        # Consecutive-failure tracking (mirrors MS-C `_refresh_history_failures`
        # in strategies/rsi2_mr.py). Counter increments on every fetch
        # exception OR empty-DataFrame return; resets on a successful fetch
        # that updates the cache. Latch ensures one alert per outage.
        self._consecutive_failures: int = 0
        self._fetch_failure_alert_fired: bool = False

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

        # Try a fresh fetch. Treat both raised exceptions AND empty-DataFrame
        # returns as failures so the operator is alerted on the "yfinance
        # reachable but returned nothing" mode (otherwise invisible).
        fetch_exc_str: Optional[str] = None
        try:
            import yfinance as yf

            df = yf.Ticker("^VIX").history(period="5d", interval="1d", auto_adjust=True)
            if not df.empty:
                val = float(df["Close"].dropna().iloc[-1])
                self._cached_value = val
                self._cached_at = now
                if self._consecutive_failures > 0:
                    logger.info(
                        "VIXFeed: yfinance fetch recovered after %d consecutive failures",
                        self._consecutive_failures,
                    )
                self._consecutive_failures = 0
                self._fetch_failure_alert_fired = False
                return val
            fetch_exc_str = "yfinance returned empty DataFrame"
            logger.warning("VIXFeed: %s", fetch_exc_str)
        except Exception as exc:
            fetch_exc_str = str(exc)
            logger.warning("VIXFeed: yfinance fetch failed: %s", exc)

        # We failed (raised exception OR empty df). Bookkeep + maybe alert.
        self._consecutive_failures += 1
        if (
            not self._fetch_failure_alert_fired
            and self._consecutive_failures >= _FETCH_FAILURE_ALERT_THRESHOLD
        ):
            self._fetch_failure_alert_fired = True
            self._fire_fetch_failure_alert(
                now=now,
                consecutive=self._consecutive_failures,
                last_exc_str=fetch_exc_str or "unknown",
            )

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
        """Fire stale-cache ntfy alert at most once per _NTFY_COOLDOWN_HOURS.

        Independent of `_fire_fetch_failure_alert` so a transient fetch-failure
        alert cannot silence this (more serious) entry-blocked signal.
        """
        if self._last_ntfy_at_stale is not None:
            hours_since = (now - self._last_ntfy_at_stale).total_seconds() / 3600
            if hours_since < _NTFY_COOLDOWN_HOURS:
                return
        self._last_ntfy_at_stale = now
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

    def _fire_fetch_failure_alert(self, now: datetime, consecutive: int, last_exc_str: str) -> None:
        """Fire fetch-failure ntfy alert when consecutive failures cross the
        threshold. Uses an independent cooldown from `_fire_stale_alert` so the
        two distinct signals (yfinance flaky vs. cache exhausted) never silence
        each other. Mirrors `_fire_history_failure_alert` in
        strategies/rsi2_mr.py.
        """
        if self._last_ntfy_at_fetch_failure is not None:
            hours_since = (now - self._last_ntfy_at_fetch_failure).total_seconds() / 3600
            if hours_since < _NTFY_COOLDOWN_HOURS:
                return
        self._last_ntfy_at_fetch_failure = now
        try:
            topic = os.environ.get("NTFY_TOPIC", "")
            if not topic:
                return
            import urllib.request

            msg = (
                f"RSI2MR VIX feed failing ({consecutive} consecutive yfinance "
                f"failures) -- last error: {last_exc_str}"
            )
            req = urllib.request.Request(
                f"https://ntfy.sh/{topic}",
                data=msg.encode(),
                headers={"Title": "TradeBot VIX outage", "Priority": "high"},
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
