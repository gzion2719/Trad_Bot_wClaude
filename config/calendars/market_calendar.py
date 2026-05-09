"""
Market calendar helpers for RSI2-MR strategy filters.

Uses pandas_market_calendars (NYSE schedule) for holidays and early closes.
All helpers are pure functions — no state, safe to call in any thread.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from functools import lru_cache

logger = logging.getLogger(__name__)


# ── NYSE calendar (lazy-loaded once) ─────────────────────────────────────────


@lru_cache(maxsize=1)
def _nyse_trading_days(start: str = "2007-01-01", end: str = "2028-12-31") -> frozenset[date]:
    """Return a frozenset of all NYSE trading days in [start, end]."""
    try:
        import pandas_market_calendars as mcal

        nyse = mcal.get_calendar("NYSE")
        schedule = nyse.schedule(start_date=start, end_date=end)
        return frozenset(d.date() for d in schedule.index)
    except Exception as exc:
        logger.warning("pandas_market_calendars unavailable (%s) — using fallback.", exc)
        return _fallback_trading_days(start, end)


def _fallback_trading_days(start: str, end: str) -> frozenset[date]:
    """Minimal fallback: weekdays only (no holiday exclusion). Used if mcal missing."""
    from datetime import datetime

    s = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    days: set[date] = set()
    cur = s
    while cur <= e:
        if cur.weekday() < 5:  # Mon-Fri
            days.add(cur)
        cur += timedelta(days=1)
    return frozenset(days)


# ── Public helpers ────────────────────────────────────────────────────────────


def is_trading_day(d: date) -> bool:
    """Return True if NYSE is open on `d`."""
    return d in _nyse_trading_days()


def next_trading_day(d: date) -> date:
    """Return the first NYSE trading day strictly after `d`."""
    candidate = d + timedelta(days=1)
    trading = _nyse_trading_days()
    while candidate not in trading:
        candidate += timedelta(days=1)
        if (candidate - d).days > 14:
            raise RuntimeError(f"No trading day found within 14 days of {d}")
    return candidate


def is_russell_rebalance_window(d: date) -> bool:
    """
    Return True if `d` falls within the Russell rebalance window.

    Russell rebalance = last Friday of June.
    The strategy skips entries on the day before, the day of, and the day after.
    """
    if d.month != 6:
        return False
    # Find last Friday of June for d's year
    last_day = date(d.year, 6, 30)
    # Walk back to Friday (weekday 4)
    while last_day.weekday() != 4:
        last_day -= timedelta(days=1)
    # Window: last_friday - 1 .. last_friday + 1
    return abs((d - last_day).days) <= 1


def is_pre_long_holiday_closure(d: date) -> bool:
    """
    Return True if the market is closed for more than 1 consecutive trading day
    starting the day after `d`.

    Used for forced-flat logic: if True, exit by close of day `d` (MOC order).
    Concretely: True when the next-next trading day is > 1 calendar day after the
    next trading day (i.e., a multi-day closure is immediately ahead).
    """
    try:
        nxt = next_trading_day(d)
        nxt2 = next_trading_day(nxt)
        gap = (nxt2 - nxt).days
        return gap > 3  # more than a long weekend
    except RuntimeError:
        return False


def trading_days_between(start: date, end: date) -> int:
    """Return number of NYSE trading days in (start, end] exclusive of start."""
    trading = _nyse_trading_days()
    count = 0
    cur = start + timedelta(days=1)
    while cur <= end:
        if cur in trading:
            count += 1
        cur += timedelta(days=1)
    return count


def nth_trading_day_after(d: date, n: int) -> date:
    """Return the date that is exactly n trading days after d."""
    trading = _nyse_trading_days()
    cur = d
    remaining = n
    while remaining > 0:
        cur += timedelta(days=1)
        if cur in trading:
            remaining -= 1
        if (cur - d).days > 30:
            raise RuntimeError(f"Could not find {n} trading days after {d}")
    return cur
