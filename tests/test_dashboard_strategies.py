"""
Dashboard per-strategy endpoint tests (Session 1).

Covers:
  - /api/strategies — REGISTRY metadata exposure
  - /api/strategies/{name}/summary — KPIs + cache + legacy NULL-basis surfacing
  - /api/strategies/{name}/fills — pagination + strategy_params JSON parsing
  - Path-safety: {name} validated against REGISTRY only (404 on traversal)
  - Sync invariant: STRATEGY_METADATA <-> REGISTRY classes stay in lockstep
"""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

from config.strategies import REGISTRY, _STRATEGY_CLASSES
from config.strategy_metadata import STRATEGY_METADATA, get_metadata
from dashboard import app as dashboard_app
from data.trade_log import TradeLog
from models.order import OrderResult, OrderStatus

# ── helpers ────────────────────────────────────────────────────────────────


def _mkfill(
    oid: int,
    strategy: str,
    symbol: str = "QQQ",
    action: str = "BUY",
    qty: float = 10,
    price: float = 100.0,
    cost_basis=None,
    real_r=None,
    submitted_at=None,
    strategy_params=None,
) -> tuple[OrderResult, str, dict | None]:
    res = OrderResult(
        order_id=oid,
        symbol=symbol,
        action=action,
        quantity=qty,
        order_type="MKT",
        tif="GTC",
        filled=qty,
        remaining=0.0,
        avg_fill_price=price,
        limit_price=None,
        stop_price=None,
        status=OrderStatus.FILLED,
        submitted_at=submitted_at or datetime.now(timezone.utc),
        cost_basis=cost_basis,
        real_r_multiple=real_r,
    )
    return res, strategy, strategy_params


@pytest.fixture
def fresh_trade_log():
    """Swap dashboard_app._trade_log onto a temp DB and restore afterwards."""
    tmp = tempfile.mkdtemp()
    db_path = Path(tmp) / "trades.db"
    log = TradeLog(db_path=db_path)
    original = dashboard_app._trade_log
    dashboard_app._trade_log = log
    # Bust the summary cache so tests don't see stale entries from prior runs.
    with dashboard_app._summary_cache_lock:
        dashboard_app._summary_cache.clear()
    yield log
    dashboard_app._trade_log = original
    with dashboard_app._summary_cache_lock:
        dashboard_app._summary_cache.clear()


# ── DS-01 .. DS-03: /api/strategies metadata exposure ──────────────────────


def test_ds01_api_strategies_returns_all_registered():
    out = dashboard_app.api_strategies()
    assert isinstance(out, list)
    names = [m["name"] for m in out]
    assert "SMACrossover-QQQ" in names
    assert "RSI2MR-SPY" in names
    assert len(out) == len(STRATEGY_METADATA)


def test_ds02_api_strategies_includes_state_file_path():
    out = dashboard_app.api_strategies()
    by_name = {m["name"]: m for m in out}
    assert by_name["SMACrossover-QQQ"]["state_file_path"] is None
    assert by_name["RSI2MR-SPY"]["state_file_path"] == "data/rsi2_mr_state.json"


def test_ds03_api_strategies_serializes_schedule_and_caps():
    out = dashboard_app.api_strategies()
    sma = next(m for m in out if m["name"] == "SMACrossover-QQQ")
    assert sma["schedule"]["kind"] == "DailyAt"
    assert sma["schedule"]["hour"] == 16
    assert sma["schedule"]["minute"] == 10
    assert sma["risk_caps"]["max_risk_per_trade_pct"] == 0.02
    assert sma["params"] == {"sma_fast": 10, "sma_slow": 30}


# ── DS-10 .. DS-15: /api/strategies/{name}/summary ─────────────────────────


def test_ds10_summary_empty_db(fresh_trade_log):
    meta = get_metadata("SMACrossover-QQQ")
    out = dashboard_app.api_strategy_summary(meta=meta)
    assert out["total_fills"] == 0
    assert out["sells"] == 0
    assert out["legacy_null_basis_sells"] == 0
    assert out["realized_pnl_lifetime"] is None
    assert out["realized_pnl_today"] == 0.0
    assert out["win_rate"] is None
    assert out["profit_factor"] is None
    assert out["avg_r_multiple"] is None
    assert out["r_multiple_count"] == 0
    assert out["symbol"] == "QQQ"


def test_ds11_summary_one_winner_one_loser(fresh_trade_log):
    log = fresh_trade_log
    log.record(*_mkfill(1, "SMACrossover-QQQ", action="BUY", qty=10, price=100)[:2])
    log.record(
        *_mkfill(2, "SMACrossover-QQQ", action="SELL", qty=10, price=110, cost_basis=100)[:2]
    )
    log.record(*_mkfill(3, "SMACrossover-QQQ", action="BUY", qty=10, price=200)[:2])
    log.record(
        *_mkfill(4, "SMACrossover-QQQ", action="SELL", qty=10, price=190, cost_basis=200)[:2]
    )
    meta = get_metadata("SMACrossover-QQQ")
    out = dashboard_app.api_strategy_summary(meta=meta)
    assert out["wins"] == 1
    assert out["losses"] == 1
    assert out["win_rate"] == 0.5
    assert out["realized_pnl_lifetime"] == 0.0
    assert out["gross_profit"] == 100.0
    assert out["gross_loss"] == 100.0
    assert out["profit_factor"] == 1.0


def test_ds12_summary_surfaces_legacy_null_basis_sells(fresh_trade_log):
    """CR CRITICAL #1: pre-MS-A1 fills with NULL cost_basis must be visible."""
    log = fresh_trade_log
    # legacy SELL — no cost_basis
    log.record(*_mkfill(1, "SMACrossover-QQQ", action="SELL", qty=5, price=95, cost_basis=None)[:2])
    # modern SELL — has cost_basis
    log.record(*_mkfill(2, "SMACrossover-QQQ", action="BUY", qty=5, price=100)[:2])
    log.record(*_mkfill(3, "SMACrossover-QQQ", action="SELL", qty=5, price=110, cost_basis=100)[:2])
    meta = get_metadata("SMACrossover-QQQ")
    out = dashboard_app.api_strategy_summary(meta=meta)
    assert out["sells"] == 2
    assert out["sells_with_basis"] == 1
    assert out["legacy_null_basis_sells"] == 1
    # Lifetime P&L sums realized_pnl across all SELLs; legacy NULL contributes 0
    assert out["realized_pnl_lifetime"] == 50.0


def test_ds13_summary_avg_r_with_denominator(fresh_trade_log):
    """CR MEDIUM #9: Avg R needs a denominator (r_multiple_count)."""
    log = fresh_trade_log
    log.record(*_mkfill(1, "RSI2MR-SPY", symbol="SPY", action="BUY", qty=1, price=50)[:2])
    log.record(
        *_mkfill(
            2, "RSI2MR-SPY", symbol="SPY", action="SELL", qty=1, price=60, cost_basis=50, real_r=1.5
        )[:2]
    )
    log.record(*_mkfill(3, "RSI2MR-SPY", symbol="SPY", action="BUY", qty=1, price=50)[:2])
    log.record(
        *_mkfill(
            4,
            "RSI2MR-SPY",
            symbol="SPY",
            action="SELL",
            qty=1,
            price=40,
            cost_basis=50,
            real_r=-1.0,
        )[:2]
    )
    meta = get_metadata("RSI2MR-SPY")
    out = dashboard_app.api_strategy_summary(meta=meta)
    assert out["avg_r_multiple"] == pytest.approx(0.25)
    assert out["r_multiple_count"] == 2


def test_ds14_summary_avg_r_none_when_zero_rows(fresh_trade_log):
    """SMA never sets real_r_multiple — avg_r must be None, count 0."""
    log = fresh_trade_log
    log.record(*_mkfill(1, "SMACrossover-QQQ", action="BUY", qty=10, price=100)[:2])
    log.record(
        *_mkfill(2, "SMACrossover-QQQ", action="SELL", qty=10, price=110, cost_basis=100)[:2]
    )
    meta = get_metadata("SMACrossover-QQQ")
    out = dashboard_app.api_strategy_summary(meta=meta)
    assert out["avg_r_multiple"] is None
    assert out["r_multiple_count"] == 0


def test_ds15_summary_cache_busts_on_new_fill(fresh_trade_log):
    """Cache key includes MAX(id); a fresh fill must show up immediately."""
    log = fresh_trade_log
    meta = get_metadata("SMACrossover-QQQ")
    first = dashboard_app.api_strategy_summary(meta=meta)
    assert first["total_fills"] == 0
    # Add a fill — cache must invalidate (last_id changed).
    log.record(*_mkfill(1, "SMACrossover-QQQ", action="BUY", qty=10, price=100)[:2])
    second = dashboard_app.api_strategy_summary(meta=meta)
    assert second["total_fills"] == 1


def test_ds16_summary_single_fill_edge_case(fresh_trade_log):
    """CR test case (e): single fill — closed=0, win_rate=None, PF=None."""
    log = fresh_trade_log
    log.record(*_mkfill(1, "SMACrossover-QQQ", action="BUY", qty=10, price=100)[:2])
    meta = get_metadata("SMACrossover-QQQ")
    out = dashboard_app.api_strategy_summary(meta=meta)
    assert out["total_fills"] == 1
    assert out["wins"] == 0
    assert out["losses"] == 0
    assert out["win_rate"] is None
    assert out["profit_factor"] is None


def test_ds17_profit_factor_none_when_all_losses(fresh_trade_log):
    """CR #5 fix: only-losses must return PF=None, not 0.0 (UI-honest)."""
    log = fresh_trade_log
    log.record(*_mkfill(1, "SMACrossover-QQQ", action="BUY", qty=10, price=100)[:2])
    log.record(*_mkfill(2, "SMACrossover-QQQ", action="SELL", qty=10, price=90, cost_basis=100)[:2])
    log.record(*_mkfill(3, "SMACrossover-QQQ", action="BUY", qty=10, price=200)[:2])
    log.record(
        *_mkfill(4, "SMACrossover-QQQ", action="SELL", qty=10, price=180, cost_basis=200)[:2]
    )
    meta = get_metadata("SMACrossover-QQQ")
    out = dashboard_app.api_strategy_summary(meta=meta)
    assert out["wins"] == 0
    assert out["losses"] == 2
    assert out["gross_profit"] == 0.0
    assert out["gross_loss"] == 300.0
    assert out["profit_factor"] is None  # NOT 0.0
    assert out["win_rate"] == 0.0


def test_ds18_profit_factor_inf_when_all_wins(fresh_trade_log):
    """Only-wins branch returns +inf — JSON-serializable via fastapi."""
    import math

    log = fresh_trade_log
    log.record(*_mkfill(1, "SMACrossover-QQQ", action="BUY", qty=10, price=100)[:2])
    log.record(
        *_mkfill(2, "SMACrossover-QQQ", action="SELL", qty=10, price=110, cost_basis=100)[:2]
    )
    meta = get_metadata("SMACrossover-QQQ")
    out = dashboard_app.api_strategy_summary(meta=meta)
    assert out["wins"] == 1
    assert out["losses"] == 0
    assert math.isinf(out["profit_factor"])


# ── DS-20 .. DS-23: /api/strategies/{name}/fills pagination + JSON parsing ──


def test_ds20_fills_empty(fresh_trade_log):
    meta = get_metadata("SMACrossover-QQQ")
    out = dashboard_app.api_strategy_fills(meta=meta)
    assert out["fills"] == []
    assert out["total"] == 0
    assert out["limit"] == 50
    assert out["offset"] == 0


def test_ds21_fills_pagination(fresh_trade_log):
    log = fresh_trade_log
    for i in range(75):
        log.record(*_mkfill(i + 1, "SMACrossover-QQQ", action="BUY", qty=1, price=100 + i)[:2])
    meta = get_metadata("SMACrossover-QQQ")
    page1 = dashboard_app.api_strategy_fills(meta=meta, limit=20, offset=0)
    assert page1["total"] == 75
    assert len(page1["fills"]) == 20
    page2 = dashboard_app.api_strategy_fills(meta=meta, limit=20, offset=20)
    assert len(page2["fills"]) == 20
    # No overlap between pages — id DESC ordering
    p1_ids = {f["id"] for f in page1["fills"]}
    p2_ids = {f["id"] for f in page2["fills"]}
    assert p1_ids.isdisjoint(p2_ids)


def test_ds22_fills_parses_strategy_params_json(fresh_trade_log):
    """CR CRITICAL #2: strategy_params stored as TEXT — must be parsed to dict."""
    log = fresh_trade_log
    res, name, _ = _mkfill(1, "SMACrossover-QQQ", action="BUY", qty=10, price=100)
    log.record(res, name, strategy_params={"sma_fast": 10, "sma_slow": 30})
    meta = get_metadata("SMACrossover-QQQ")
    out = dashboard_app.api_strategy_fills(meta=meta)
    assert out["fills"][0]["strategy_params"] == {"sma_fast": 10, "sma_slow": 30}


def test_ds23_fills_with_comma_in_strategy_params(fresh_trade_log):
    """CR test case (f): comma in JSON — round-trip parse OK."""
    log = fresh_trade_log
    res, name, _ = _mkfill(1, "SMACrossover-QQQ", action="BUY", qty=10, price=100)
    log.record(res, name, strategy_params={"note": "hello, world", "sma_fast": 10})
    meta = get_metadata("SMACrossover-QQQ")
    out = dashboard_app.api_strategy_fills(meta=meta)
    assert out["fills"][0]["strategy_params"]["note"] == "hello, world"


def test_ds24_fills_corrupt_strategy_params_returns_none(fresh_trade_log):
    """If a row has bad JSON in strategy_params, endpoint serves null, not crashes."""
    log = fresh_trade_log
    log.record(*_mkfill(1, "SMACrossover-QQQ", action="BUY", qty=10, price=100)[:2])
    # Manually corrupt the strategy_params blob
    with sqlite3.connect(log._db_path) as conn:
        conn.execute("UPDATE trades SET strategy_params = '{not valid json' WHERE id = 1")
    meta = get_metadata("SMACrossover-QQQ")
    out = dashboard_app.api_strategy_fills(meta=meta)
    assert out["fills"][0]["strategy_params"] is None


def test_ds25_fills_offset_clamped(fresh_trade_log):
    """CR #6 fix: offset is clamped at 10k to kill the trivial DoS via huge OFFSET."""
    log = fresh_trade_log
    log.record(*_mkfill(1, "SMACrossover-QQQ", action="BUY", qty=10, price=100)[:2])
    meta = get_metadata("SMACrossover-QQQ")
    # Huge offset must NOT echo back literally — it is clamped.
    out = dashboard_app.api_strategy_fills(meta=meta, offset=10**9)
    assert out["offset"] == 10_000
    assert out["fills"] == []  # past the actual data
    # Negative offsets are coerced to 0 (existing behavior).
    out2 = dashboard_app.api_strategy_fills(meta=meta, offset=-5)
    assert out2["offset"] == 0


def test_ds26_trade_log_connection_helper_sets_pragmas():
    """Fix CR #1: connection() must set busy_timeout (and idempotent WAL)."""
    import tempfile

    tmp = tempfile.mkdtemp()
    db_path = Path(tmp) / "p.db"
    log = TradeLog(db_path=db_path)
    with log.connection() as conn:
        # busy_timeout returns the value in ms — must be 5000 (5s).
        bt = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert bt == 5000
        jm = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
        assert jm == "wal"
    with log.connection(row_factory=True) as conn:
        # row_factory=True must yield sqlite3.Row, addressable by column name.
        log.record(*_mkfill(1, "SMACrossover-QQQ", action="BUY", qty=10, price=100)[:2])
        row = conn.execute("SELECT id, symbol FROM trades LIMIT 1").fetchone()
        assert row["symbol"] == "QQQ"


# ── DS-30 .. DS-33: Path-safety on {name} ──────────────────────────────────


@pytest.mark.parametrize(
    "bad_name",
    [
        "../../../etc/passwd",
        "SMACrossover-QQQ/../foo",
        "..%2F",
        "..\\windows\\system32",
        "smacrossover-qqq",  # case-sensitive: lowercase must 404
        "nonexistent",
        "",
        "'; DROP TABLE trades; --",
    ],
)
def test_ds30_resolve_strategy_rejects_traversal_and_unknown(bad_name):
    with pytest.raises(HTTPException) as exc_info:
        dashboard_app._resolve_strategy(bad_name)
    assert exc_info.value.status_code == 404


def test_ds31_resolve_strategy_accepts_registered_names():
    for meta in STRATEGY_METADATA:
        resolved = dashboard_app._resolve_strategy(meta.name)
        assert resolved.name == meta.name


# ── DS-40: Sync invariant ──────────────────────────────────────────────────


def test_ds40_strategy_metadata_and_classes_stay_in_sync():
    """Every STRATEGY_METADATA name has a class binding and vice versa.

    This is the lockstep guarantee enforced by `config/strategies._build_registry`.
    A direct test gives a clearer error than waiting for module import to fail.
    """
    metadata_names = {m.name for m in STRATEGY_METADATA}
    class_names = set(_STRATEGY_CLASSES.keys())
    assert metadata_names == class_names, (
        f"STRATEGY_METADATA and _STRATEGY_CLASSES drifted. "
        f"Only in metadata: {metadata_names - class_names}. "
        f"Only in classes: {class_names - metadata_names}."
    )
    # Every REGISTRY entry has the expected class wired.
    for cfg in REGISTRY:
        assert isinstance(cfg.strategy_class, type)
        assert _STRATEGY_CLASSES[cfg.name] is cfg.strategy_class


def test_ds41_registry_entry_with_no_fills_returns_clean_summary(fresh_trade_log):
    """CR test case (c): a REGISTRY entry that has never traded."""
    log = fresh_trade_log
    # Add fills for one strategy only
    log.record(*_mkfill(1, "SMACrossover-QQQ", action="BUY", qty=10, price=100)[:2])
    meta = get_metadata("RSI2MR-SPY")
    out = dashboard_app.api_strategy_summary(meta=meta)
    assert out["total_fills"] == 0
    assert out["realized_pnl_lifetime"] is None
    assert out["legacy_null_basis_sells"] == 0
