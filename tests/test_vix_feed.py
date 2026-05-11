"""VIXFeed tests (MS-C3) — fetch-failure alerting and recovery logic.

Covers the four BACKLOG gaps for MS-C3:
  (b) recovery log on yfinance coming back
  (c) ntfy alert on N consecutive yfinance failures (cache may still be fresh)
  (4th) "empty DataFrame" path treated as a failure (not silent fallthrough)
And independence of the stale-cache vs fetch-failure cooldowns (CR fix).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from data.vix_feed import _FETCH_FAILURE_ALERT_THRESHOLD, VIXFeed


def _df_with_close(value: float) -> pd.DataFrame:
    return pd.DataFrame({"Close": [value]}, index=[pd.Timestamp("2026-05-11")])


def _ticker_raising(exc_factory):
    """Build a MagicMock that mimics yf.Ticker(...).history(...) raising."""
    t = MagicMock()
    t.history.side_effect = exc_factory
    return t


def _ticker_returning(df):
    t = MagicMock()
    t.history.return_value = df
    return t


def _ticker_returning_empty():
    return _ticker_returning(pd.DataFrame())


# ── MSC3-01: two consecutive failures fire a fetch-failure alert ──────────────


def test_msc3_01_two_consecutive_failures_fire_alert(monkeypatch):
    """Threshold=2: after 2 consecutive yfinance exceptions, fire exactly one
    fetch-failure ntfy POST. Pre-seed the cache so the stale-cache alert path
    is NOT also triggered (we're isolating the new failure-alert path)."""
    monkeypatch.setenv("NTFY_TOPIC", "test-topic")

    feed = VIXFeed()
    # Pre-seed cache so stale-cache alert is silent (cache age = 0h, fresh).
    feed._cached_value = 18.5
    feed._cached_at = datetime.now(timezone.utc)

    posts = []

    def _record(req, timeout):  # noqa: ARG001
        posts.append(req)
        return MagicMock()

    with patch("yfinance.Ticker", return_value=_ticker_raising(lambda: RuntimeError("boom"))):
        with patch("urllib.request.urlopen", side_effect=_record):
            assert _FETCH_FAILURE_ALERT_THRESHOLD == 2
            r1 = feed.get_latest_close()
            r2 = feed.get_latest_close()

    # Both calls fall back to the fresh cache (not None, no stale-cache alert).
    assert r1 == 18.5 and r2 == 18.5
    # Exactly one fetch-failure POST after 2 failures.
    assert len(posts) == 1, f"expected 1 fetch-failure POST, got {len(posts)}"
    assert b"VIX feed failing" in posts[0].data
    assert b"2 consecutive" in posts[0].data


# ── MSC3-02: one failure then success — no alert, recovery log ────────────────


def test_msc3_02_one_failure_then_success_no_alert(monkeypatch, caplog):
    """1 failure then a successful fetch: 0 POSTs, INFO 'recovered' log,
    counter and latch reset."""
    monkeypatch.setenv("NTFY_TOPIC", "test-topic")

    feed = VIXFeed()
    feed._cached_value = 18.5
    feed._cached_at = datetime.now(timezone.utc)

    posts = []

    def _record(req, timeout):  # noqa: ARG001
        posts.append(req)
        return MagicMock()

    seq = [_ticker_raising(lambda: RuntimeError("flaky")), _ticker_returning(_df_with_close(19.7))]

    with patch("yfinance.Ticker", side_effect=seq):
        with patch("urllib.request.urlopen", side_effect=_record):
            with caplog.at_level(logging.INFO, logger="data.vix_feed"):
                feed.get_latest_close()  # fail #1 — below threshold, no alert
                v = feed.get_latest_close()  # success — recover

    assert v == 19.7
    assert posts == [], "no POST expected when fail/success below threshold"
    assert feed._consecutive_failures == 0
    assert feed._fetch_failure_alert_fired is False
    assert any("recovered after 1 consecutive failures" in r.message for r in caplog.records)


# ── MSC3-03: consecutive successes — no alerts, counter stays at 0 ────────────


def test_msc3_03_consecutive_successes_no_alert(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "test-topic")

    feed = VIXFeed()
    posts = []

    def _record(req, timeout):  # noqa: ARG001
        posts.append(req)
        return MagicMock()

    with patch("yfinance.Ticker", return_value=_ticker_returning(_df_with_close(20.0))):
        with patch("urllib.request.urlopen", side_effect=_record):
            for _ in range(5):
                assert feed.get_latest_close() == 20.0

    assert posts == []
    assert feed._consecutive_failures == 0
    assert feed._fetch_failure_alert_fired is False


# ── MSC3-04: independent cooldowns — both alert types fire within 24h ─────────


def test_msc3_04_independent_cooldowns_both_alerts_fire(monkeypatch):
    """Critical CR fix: a fetch-failure POST must NOT silence a later
    stale-cache POST within the same 24h window. They use separate cooldowns."""
    monkeypatch.setenv("NTFY_TOPIC", "test-topic")

    feed = VIXFeed()
    # Seed a fresh cache for stage 1; we'll age it for stage 2.
    feed._cached_value = 18.5
    feed._cached_at = datetime.now(timezone.utc)

    posts = []

    def _record(req, timeout):  # noqa: ARG001
        posts.append(req)
        return MagicMock()

    with patch("yfinance.Ticker", return_value=_ticker_raising(lambda: RuntimeError("boom"))):
        with patch("urllib.request.urlopen", side_effect=_record):
            # Stage 1: 2 failures with fresh cache → fetch-failure POST only.
            feed.get_latest_close()
            feed.get_latest_close()
            assert len(posts) == 1
            assert b"VIX feed failing" in posts[0].data

            # Stage 2: age the cache past staleness so next failure also fires
            # the stale-cache alert. Reset the fetch-failure latch so we don't
            # care about that path; only assert that stale-cache fires DESPITE
            # the recent fetch-failure POST consuming a (separate) cooldown.
            feed._cached_at = datetime.now(timezone.utc) - timedelta(hours=48)
            feed.get_latest_close()

    assert len(posts) == 2, f"expected 2 distinct alerts (fetch-failure + stale), got {len(posts)}"
    assert b"stale/unavailable" in posts[1].data, "second POST must be the stale-cache alert"


# ── MSC3-05: latch prevents repeat fetch-failure alert during outage ──────────


def test_msc3_05_latch_prevents_repeat_fetch_failure_alert(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "test-topic")

    feed = VIXFeed()
    feed._cached_value = 18.5
    feed._cached_at = datetime.now(timezone.utc)

    posts = []

    def _record(req, timeout):  # noqa: ARG001
        posts.append(req)
        return MagicMock()

    with patch("yfinance.Ticker", return_value=_ticker_raising(lambda: RuntimeError("boom"))):
        with patch("urllib.request.urlopen", side_effect=_record):
            for _ in range(5):  # N=1..5 consecutive failures
                feed.get_latest_close()

    assert len(posts) == 1, "latch must hold; expected exactly 1 POST across 5 failures"
    assert feed._consecutive_failures == 5
    assert feed._fetch_failure_alert_fired is True


# ── MSC3-06: recovery resets latch+counter; next outage re-alerts ─────────────


def test_msc3_06_recovery_resets_latch_and_counter(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "test-topic")

    feed = VIXFeed()
    feed._cached_value = 18.5
    feed._cached_at = datetime.now(timezone.utc)

    posts = []

    def _record(req, timeout):  # noqa: ARG001
        posts.append(req)
        return MagicMock()

    # First outage: 3 failures (alert fires on #2), then recovery.
    seq = [
        _ticker_raising(lambda: RuntimeError("boom")),
        _ticker_raising(lambda: RuntimeError("boom")),
        _ticker_raising(lambda: RuntimeError("boom")),
        _ticker_returning(_df_with_close(21.0)),
    ]
    with patch("yfinance.Ticker", side_effect=seq):
        with patch("urllib.request.urlopen", side_effect=_record):
            for _ in range(3):
                feed.get_latest_close()
            assert feed._fetch_failure_alert_fired is True
            feed.get_latest_close()  # recovery

    assert feed._consecutive_failures == 0
    assert feed._fetch_failure_alert_fired is False
    # Bypass the in-feed cooldown for the second outage by clearing the
    # cooldown timestamp — we're testing latch/counter reset, not cooldown.
    feed._last_ntfy_at_fetch_failure = None

    seq2 = [
        _ticker_raising(lambda: RuntimeError("boom")),
        _ticker_raising(lambda: RuntimeError("boom")),
    ]
    with patch("yfinance.Ticker", side_effect=seq2):
        with patch("urllib.request.urlopen", side_effect=_record):
            feed.get_latest_close()
            feed.get_latest_close()

    assert len(posts) == 2, f"expected 2 POSTs (one per outage), got {len(posts)}"


# ── MSC3-07: empty DataFrame is treated as a failure (4th gap) ────────────────


def test_msc3_07_empty_dataframe_increments_counter(monkeypatch):
    """yfinance returning empty must not be a silent fallthrough; the counter
    increments and the threshold logic eventually alerts."""
    monkeypatch.setenv("NTFY_TOPIC", "test-topic")

    feed = VIXFeed()
    feed._cached_value = 18.5
    feed._cached_at = datetime.now(timezone.utc)

    posts = []

    def _record(req, timeout):  # noqa: ARG001
        posts.append(req)
        return MagicMock()

    with patch("yfinance.Ticker", return_value=_ticker_returning_empty()):
        with patch("urllib.request.urlopen", side_effect=_record):
            feed.get_latest_close()
            feed.get_latest_close()

    assert feed._consecutive_failures == 2
    assert feed._fetch_failure_alert_fired is True
    assert len(posts) == 1
    assert b"empty DataFrame" in posts[0].data


# ── MSC3-08: no NTFY_TOPIC env → no POST attempt, but logic still runs ────────


def test_msc3_08_no_ntfy_topic_no_post(monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)

    feed = VIXFeed()
    feed._cached_value = 18.5
    feed._cached_at = datetime.now(timezone.utc)

    with patch("yfinance.Ticker", return_value=_ticker_raising(lambda: RuntimeError("boom"))):
        with patch("urllib.request.urlopen") as mocked_post:
            feed.get_latest_close()
            feed.get_latest_close()
            assert mocked_post.call_count == 0  # no topic, no POST

    # But the latch still flipped — we still tracked the outage internally.
    assert feed._fetch_failure_alert_fired is True


# ── MSC3-09: backtest mode (series provided) is untouched by failure path ─────


def test_msc3_09_backtest_mode_unaffected():
    """get_for_date should never touch yfinance or counters."""
    series = pd.Series([15.0, 16.0], index=pd.to_datetime(["2024-01-02", "2024-01-03"]))
    feed = VIXFeed(series=series)
    from datetime import date

    with patch("yfinance.Ticker") as mocked:
        v = feed.get_for_date(date(2024, 1, 3))
        assert v == pytest.approx(16.0)
        assert mocked.call_count == 0
    assert feed._consecutive_failures == 0
