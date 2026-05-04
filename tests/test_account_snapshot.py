"""Account snapshot tests (AS-01..AS-10) — no live IB connection needed."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import patch

from data.account_snapshot import (
    AccountSnapshotPoller,
    EQUITY_FILENAME_PREFIX,
    SNAPSHOT_FILENAME,
    _prune_old_files,
    downsample,
    read_snapshot,
)

# ── Stub IB client ────────────────────────────────────────────────────────────


class _AccountValue:
    """Minimal stand-in for ib_insync AccountValue."""

    def __init__(self, tag: str, value: str, currency: str = "USD") -> None:
        self.tag = tag
        self.value = value
        self.currency = currency


class _Contract:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.description = symbol


class _PortfolioItem:
    def __init__(self, symbol: str, position: int = 100, market_value: float = 50000.0) -> None:
        self.contract = _Contract(symbol)
        self.position = position
        self.marketValue = market_value
        self.averageCost = 450.0
        self.marketPrice = 500.0
        self.unrealizedPNL = 5000.0
        self.realizedPNL = 0.0


def _make_account_values(extra: Optional[dict] = None) -> list:
    base = {
        "NetLiquidation": "100000.00",
        "SettledCash": "50000.00",
        "UnrealizedPnL": "1234.56",
        "RealizedPnL": "789.00",
        "MaintMarginReq": "10000.00",
        "ExcessLiquidity": "40000.00",
        "BuyingPower": "80000.00",
        "AvailableFunds": "45000.00",
        "TotalCashValue": "55000.00",
        "EquityWithLoanValue": "100000.00",
        "InitMarginReq": "15000.00",
    }
    if extra:
        base.update(extra)
    return [_AccountValue(tag, val) for tag, val in base.items()]


class _StubClient:
    def __init__(
        self,
        account_values: Optional[list] = None,
        positions: Optional[list] = None,
        raise_on_summary: bool = False,
        account: str = "STUB-ACCT",
    ) -> None:
        self.account = account
        self._account_values = (
            account_values if account_values is not None else _make_account_values()
        )
        self._positions = positions if positions is not None else []
        self._raise_on_summary = raise_on_summary

    def get_account_summary_threadsafe(self) -> list:
        if self._raise_on_summary:
            raise RuntimeError("simulated IB error")
        return self._account_values

    def get_positions_threadsafe(self) -> list:
        return self._positions


def _poller(tmp_path: Path, client: Optional[_StubClient] = None) -> AccountSnapshotPoller:
    return AccountSnapshotPoller(client or _StubClient(), tmp_path, interval=9999)


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_as01_snapshot_writes_atomic(tmp_path):
    """_capture_and_write produces a valid snapshot file atomically."""
    p = _poller(tmp_path)
    p._capture_and_write()

    snap_file = tmp_path / SNAPSHOT_FILENAME
    assert snap_file.exists(), "snapshot file must be created"

    data = json.loads(snap_file.read_text(encoding="utf-8"))
    assert data["v"] == 1
    assert "captured_at" in data
    assert data["captured_at"].endswith("+00:00")
    s = data["summary"]
    for key in (
        "net_liquidation",
        "settled_cash",
        "unrealized_pnl",
        "realized_pnl",
        "maintenance_margin",
        "excess_liquidity",
        "buying_power",
        "available_funds",
        "cash",
        "equity_with_loan",
        "initial_margin",
    ):
        assert key in s, f"missing required summary key: {key}"
    assert "positions" in data


def test_as02_optional_fields_none_when_missing(tmp_path):
    """Optional tags absent from the stub client produce None in the snapshot."""
    avs = _make_account_values()  # no optional tags
    p = _poller(tmp_path, _StubClient(account_values=avs))
    p._capture_and_write()

    data = json.loads((tmp_path / SNAPSHOT_FILENAME).read_text(encoding="utf-8"))
    s = data["summary"]
    for key in ("sma", "leverage", "previous_day_ewl", "regulation_t_ewl", "gross_position_value"):
        assert key in s
        assert s[key] is None, f"expected None for optional key {key}, got {s[key]!r}"


def test_as03_run_loop_survives_ib_exception(tmp_path, caplog):
    """run() continues after a capture exception rather than crashing the thread.

    Drives the actual run() method: raises on every capture attempt, lets the
    loop tick twice, then stops. Asserts the thread exited cleanly (no crash)
    and logged a WARNING.
    """
    import logging

    call_count = 0

    class _AlwaysRaisingClient:
        account = "STUB-ACCT"

        def get_account_summary_threadsafe(self) -> list:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("simulated IB error")

        def get_positions_threadsafe(self) -> list:
            return []

    # Use a very short interval so the test completes quickly.
    p = AccountSnapshotPoller(_AlwaysRaisingClient(), tmp_path, interval=0.05)

    with caplog.at_level(logging.WARNING, logger="data.account_snapshot"):
        p.start()
        # Wait until at least 2 capture attempts have been made, then stop.
        deadline = __import__("time").monotonic() + 3.0
        while call_count < 2 and __import__("time").monotonic() < deadline:
            __import__("time").sleep(0.02)
        p.stop()
        p.join(timeout=2)

    assert not p.is_alive(), "poller thread should have exited after stop()"
    assert call_count >= 2, "run() loop must have continued after the first exception"
    assert any(
        "capture error" in r.message for r in caplog.records
    ), "expected WARNING log for capture error"
    snap = tmp_path / SNAPSHOT_FILENAME
    assert not snap.exists(), "no snapshot file should be written when all captures fail"


def test_as04_prune_keeps_today_and_recent(tmp_path):
    """Prune deletes files older than retention_days but keeps today and recent ones."""
    today = datetime.now(timezone.utc).date()

    files = {
        "today": tmp_path / f"{EQUITY_FILENAME_PREFIX}{today}.jsonl",
        "recent": tmp_path / f"{EQUITY_FILENAME_PREFIX}{today - timedelta(days=30)}.jsonl",
        "old": tmp_path / f"{EQUITY_FILENAME_PREFIX}{today - timedelta(days=400)}.jsonl",
    }
    for f in files.values():
        f.write_text("{}\n", encoding="utf-8")

    _prune_old_files(tmp_path, retention_days=365)

    assert files["today"].exists(), "today's file must survive pruning"
    assert files["recent"].exists(), "30-day-old file within retention must survive"
    assert not files["old"].exists(), "400-day-old file must be deleted"


def test_as05_equity_appends_one_line_per_capture(tmp_path):
    """Three consecutive captures produce 3 valid equity lines in today's file."""
    p = _poller(tmp_path)
    for _ in range(3):
        p._capture_and_write()

    today = datetime.now(timezone.utc).date()
    eq_file = tmp_path / f"{EQUITY_FILENAME_PREFIX}{today}.jsonl"
    assert eq_file.exists()

    lines = [ln for ln in eq_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 3

    for line in lines:
        obj = json.loads(line)
        assert "t" in obj
        assert "net_liq" in obj
        assert isinstance(obj["net_liq"], (int, float))


def test_as06_midnight_rollover_uses_captured_at_date(tmp_path):
    """Lines land in per-day files derived from captured_at, not wall-clock date."""
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    today = datetime.now(timezone.utc).date()

    yesterday_dt = datetime(
        yesterday.year, yesterday.month, yesterday.day, 23, 59, 59, tzinfo=timezone.utc
    )
    today_dt = datetime(today.year, today.month, today.day, 0, 0, 1, tzinfo=timezone.utc)

    p = _poller(tmp_path)

    with patch("data.account_snapshot.datetime") as mock_dt:
        mock_dt.now.return_value = yesterday_dt
        mock_dt.fromisoformat = datetime.fromisoformat
        p._capture_and_write()

    with patch("data.account_snapshot.datetime") as mock_dt:
        mock_dt.now.return_value = today_dt
        mock_dt.fromisoformat = datetime.fromisoformat
        p._capture_and_write()

    yf = tmp_path / f"{EQUITY_FILENAME_PREFIX}{yesterday}.jsonl"
    tf = tmp_path / f"{EQUITY_FILENAME_PREFIX}{today}.jsonl"
    assert yf.exists(), "yesterday's file must exist after midnight rollover"
    assert tf.exists(), "today's file must exist after midnight rollover"

    y_lines = [ln for ln in yf.read_text(encoding="utf-8").splitlines() if ln.strip()]
    t_lines = [ln for ln in tf.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(y_lines) == 1
    assert len(t_lines) == 1


def test_as07_read_snapshot_returns_unreadable_on_corrupt_json(tmp_path):
    """Corrupt JSON in the snapshot file returns status='unreadable'."""
    snap = tmp_path / SNAPSHOT_FILENAME
    snap.write_text("{not json", encoding="utf-8")

    result = read_snapshot(tmp_path)
    assert result["status"] == "unreadable"


def test_as08_read_snapshot_returns_unreadable_on_schema_mismatch(tmp_path):
    """Unknown schema version returns status='unreadable'."""
    snap = tmp_path / SNAPSHOT_FILENAME
    snap.write_text(json.dumps({"v": 99}), encoding="utf-8")

    result = read_snapshot(tmp_path)
    assert result["status"] == "unreadable"


def test_as09_downsample_passthrough_when_below_max():
    """Points below max_points are returned unchanged (identity)."""
    pts = [{"t": str(i), "net_liq": float(i)} for i in range(100)]
    result = downsample(pts, max_points=2000)
    assert len(result) == 100
    assert result is pts  # same object — no copy


def test_as10_downsample_buckets_when_above_max():
    """10000 points downsampled to <=2000 with monotone non-decreasing timestamps."""
    pts = [{"t": str(i).zfill(5), "net_liq": float(i)} for i in range(10000)]
    result = downsample(pts, max_points=2000)
    assert len(result) <= 2000
    # t values are monotone non-decreasing (bucket first.t is always the earliest)
    ts = [p["t"] for p in result]
    assert ts == sorted(ts), "timestamps in downsampled output must be monotone non-decreasing"
