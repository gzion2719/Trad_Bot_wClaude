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
    """Only-wins branch returns the string sentinel "Infinity".

    FastAPI's default JSONResponse converts float('inf') to null on the wire,
    so the helper at data.trade_log._round_profit_factor emits a string
    instead. The dashboard renderer (dashboard.js _fmtProfitFactor) handles
    both forms.
    """
    log = fresh_trade_log
    log.record(*_mkfill(1, "SMACrossover-QQQ", action="BUY", qty=10, price=100)[:2])
    log.record(
        *_mkfill(2, "SMACrossover-QQQ", action="SELL", qty=10, price=110, cost_basis=100)[:2]
    )
    meta = get_metadata("SMACrossover-QQQ")
    out = dashboard_app.api_strategy_summary(meta=meta)
    assert out["wins"] == 1
    assert out["losses"] == 0
    assert out["profit_factor"] == "Infinity"


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


# ── DS-27: URL-drift tripwire ──────────────────────────────────────────────


def test_ds27_dashboard_js_fetch_urls_match_routes():
    """Every fetch(`/api/...`) in dashboard.js maps to a registered FastAPI route.

    Catches URL drift like the 2026-05-04 `console/lock/release` typo where the
    JS called a non-existent route, the .catch swallowed the 404, and the
    side effect (lock release) silently never fired.

    The normalization step lets a JS template literal like
    `/api/strategies/${enc}/summary` match the FastAPI declaration
    `/api/strategies/{name}/summary` — both collapse to `/api/strategies/{P}/summary`.
    """
    import re

    js_path = Path(__file__).resolve().parent.parent / "dashboard" / "static" / "dashboard.js"
    js_text = js_path.read_text(encoding="utf-8")

    # Match the URL inside fetch(...) or _fetchJSON(...). The first argument
    # is a string ("...") or template literal (`...`). Capture /api/... up
    # to the first closing quote/backtick, query separator, or whitespace.
    pattern = r"""(?:fetch|_fetchJSON)\s*\(\s*[`"'](/api/[^`"'?\s]+)"""
    js_urls: set[str] = set()
    for m in re.finditer(pattern, js_text):
        url = m.group(1)
        # Collapse ${...} interpolations to a stable placeholder so the
        # match against FastAPI's {name} routes is shape-equal.
        url = re.sub(r"\$\{[^}]+\}", "{P}", url)
        js_urls.add(url)

    # Collect every registered FastAPI route path under /api/...
    server_paths: set[str] = set()
    for r in dashboard_app.app.routes:
        path = getattr(r, "path", None)
        if not path or not path.startswith("/api/"):
            continue
        server_paths.add(re.sub(r"\{[^}]+\}", "{P}", path))

    missing = js_urls - server_paths
    assert not missing, (
        "dashboard.js fetches URLs not registered in FastAPI app: "
        f"{sorted(missing)}. Either the JS URL has drifted from the route "
        "declaration or a new endpoint is missing on the server."
    )

    # Sanity: we should have actually found URLs. If the regex broke, the
    # test would silently pass with an empty js_urls set.
    assert len(js_urls) >= 5, (
        f"Found suspiciously few JS fetch URLs ({len(js_urls)}) — check the "
        "regex in test_ds27 against dashboard.js."
    )


# ── DS-28: profit_factor "Infinity" sentinel round-trips through FastAPI ──


def test_ds28_profit_factor_infinity_survives_json_response(fresh_trade_log, dashboard_client):
    """Only-wins profit_factor reaches the wire as the literal string "Infinity".

    Direct-call tests (ds18) bypass FastAPI's default JSONResponse encoder, so
    they cannot catch a regression where the helper returns float('inf') again
    and the encoder silently rewrites it to null. This test goes through the
    real HTTP layer to lock that contract.
    """
    log = fresh_trade_log
    log.record(*_mkfill(1, "SMACrossover-QQQ", action="BUY", qty=10, price=100)[:2])
    log.record(
        *_mkfill(2, "SMACrossover-QQQ", action="SELL", qty=10, price=110, cost_basis=100)[:2]
    )

    r = dashboard_client.get("/api/strategies/SMACrossover-QQQ/summary")
    assert r.status_code == 200, r.text
    body = r.json()
    # The wire-format assertion. If FastAPI's encoder silently rewrites
    # float('inf') → null, this fails.
    assert body["profit_factor"] == "Infinity"
    # The raw response text must contain the literal string — guards
    # against an alternative encoder reintroducing JSON5-style Infinity.
    assert '"profit_factor":"Infinity"' in r.text.replace(" ", "")


# ── DS-50..54: auth-failure coverage for per-strategy endpoints (DB-X5) ──────


def test_ds50_strategies_list_unauthenticated_returns_401(dashboard_client_unauth):
    r = dashboard_client_unauth.get("/api/strategies")
    assert r.status_code == 401


def test_ds51_strategy_summary_unauthenticated_returns_401(dashboard_client_unauth):
    # Use the first known strategy from STRATEGY_METADATA so the route resolves.
    name = STRATEGY_METADATA[0].name
    r = dashboard_client_unauth.get(f"/api/strategies/{name}/summary")
    assert r.status_code == 401


def test_ds52_strategy_fills_unauthenticated_returns_401(dashboard_client_unauth):
    name = STRATEGY_METADATA[0].name
    r = dashboard_client_unauth.get(f"/api/strategies/{name}/fills")
    assert r.status_code == 401


def test_ds53_strategy_summary_with_tampered_session_cookie_returns_401(dashboard_client_unauth):
    """Forged dashboard_session cookie must fail _is_valid_session and return 401.

    Replaces the bearer-token variant from the plan: /api/strategies/* uses
    `_require_session` (cookie-only), so a bearer-token test is impossible.
    Tampered-cookie is the closest analog to 'bad credentials'.
    """
    name = STRATEGY_METADATA[0].name
    dashboard_client_unauth.cookies.set("dashboard_session", "not.a.valid.signed.session")
    r = dashboard_client_unauth.get(f"/api/strategies/{name}/summary")
    assert r.status_code == 401


# ── DS-60..65: per-strategy history-table UI contracts (static JS/HTML guards) ──
#
# The history table is a pure UI consumer of /api/strategies/{name}/fills, which
# already has end-to-end test coverage (ds20..25). The tests below lock UI-shape
# invariants by reading the JS/HTML/CSS files directly — cheap, fast, and they
# catch the specific failure modes flagged by the pre-impl CR (polling
# collision, page-size races, offset-cap UX, etc.).


_STATIC_DIR = Path(__file__).resolve().parent.parent / "dashboard" / "static"


def _read_static(name: str) -> str:
    return (_STATIC_DIR / name).read_text(encoding="utf-8")


def _js_decl_end(js: str, start: int) -> int:
    """Return the index of the next TOP-LEVEL declaration after `start`.

    Used to slice a function body for static-grep assertions without
    accidentally swallowing a sibling declaration. `js.find("\\n}\\n")`
    was the original heuristic; it broke on functions whose body contained
    a nested map/arrow expression ending with `}\\n` before the actual
    function end. Anchoring on the next decl keyword is robust to any
    intra-function brace shape because top-level `function`/`async function`/
    `const` declarations always begin a new line.
    """
    candidates = [
        js.find("\nfunction ", start + 1),
        js.find("\nasync function ", start + 1),
        js.find("\nconst ", start + 1),
    ]
    candidates = [c for c in candidates if c != -1]
    if not candidates:
        return len(js)
    return min(candidates)


def test_ds60_history_fetch_decoupled_from_summary_poll():
    """The 30s summary poller must NOT call fetchStrategyFills.

    A regression here means the 30s tick re-renders the history table out
    from under a user mid-read (jumps back to page 1, breaks scroll, kills
    in-flight pagination). Locks the decoupling promised in the plan.
    """
    js = _read_static("dashboard.js")
    # Find the polling block keyed on _onStratTab.
    idx = js.find("_onStratTab &&")
    assert idx != -1, "could not locate _onStratTab polling block in dashboard.js"
    # Take a generous window after the gate so the assert covers the whole if-body.
    window = js[idx : idx + 800]
    assert "fetchStrategySummary" in window, (
        "sanity: expected the summary poll inside the _onStratTab block — "
        "if this fails the test is locating the wrong block, not the regression."
    )
    assert "fetchStrategyFills" not in window, (
        "fetchStrategyFills appears inside the _onStratTab polling block — "
        "history must be fetched only on user action (strategy switch / pager / "
        "page-size). A summary-tick refresh would re-render the table mid-read."
    )


def test_ds61_history_next_button_respects_server_offset_cap():
    """Next button must disable at _STRAT_OFFSET_CAP even when total > cap.

    /api/strategies/{name}/fills clamps offset to 10_000 (dashboard/app.py).
    Without the client-side guard, clicking Next at offset=10_000 silently
    re-fetches the same page; the UI shows duplicate rows.
    """
    js = _read_static("dashboard.js")
    assert "_STRAT_OFFSET_CAP" in js, "expected _STRAT_OFFSET_CAP constant in dashboard.js"
    # The constant must match the server cap; if either side moves, both move together.
    import re

    m = re.search(r"_STRAT_OFFSET_CAP\s*=\s*([\d_]+)", js)
    assert m, "could not parse _STRAT_OFFSET_CAP value"
    cap = int(m.group(1).replace("_", ""))
    assert cap == 10_000, f"client cap drifted from server (app.py:409): {cap} != 10000"

    # Slice the Next click handler specifically: from the addEventListener
    # registration to the next `});`. Substring searches inside this slice
    # cannot leak into prev/size handlers.
    next_idx = js.find('next.addEventListener("click"')
    assert next_idx != -1, "Next click handler not found"
    next_end = js.find("});", next_idx)
    handler = js[next_idx:next_end]
    assert "_STRAT_OFFSET_CAP" in handler, "Next handler does not reference _STRAT_OFFSET_CAP"

    # The handler must use `>` (strict), not `>=`. Strict comparison lets
    # the user reach offset=10_000 (the last legal page); `>=` would strand
    # them one page early. Match the operator immediately adjacent to the
    # constant on either side, so the assertion is impervious to incidental
    # `>` characters elsewhere in the slice (e.g. arrow fn `=>`).
    op_match = re.search(
        r"(?:>=?|<=?)\s*_STRAT_OFFSET_CAP|_STRAT_OFFSET_CAP\s*(?:>=?|<=?)",
        handler,
    )
    assert op_match, "Next handler: no comparison operator adjacent to _STRAT_OFFSET_CAP"
    operator = re.search(r"(>=|<=|>|<)", op_match.group(0)).group(1)
    assert operator == ">", (
        f"Next click handler must use `>` (strict) against _STRAT_OFFSET_CAP, "
        f"not `{operator}`. `>=` would disable Next one page before the cap "
        "is reachable — see post-impl CR HIGH finding."
    )

    # Pager state guard: the disabled flip in _renderHistoryPager uses `>=`
    # (offset >= cap ⇒ disabled), which is correct precisely BECAUSE the
    # click handler uses `>`. Lock both halves so future refactors can't
    # silently desync.
    pager_idx = js.find("function _renderHistoryPager")
    assert pager_idx != -1
    pager_end = _js_decl_end(js, pager_idx)
    pager = js[pager_idx:pager_end]
    assert "offset >= _STRAT_OFFSET_CAP" in pager, (
        "Pager disabled-flip must use `offset >= _STRAT_OFFSET_CAP` so that "
        "Next disables at the cap. Paired with the click handler's `>`, the "
        "user can reach the cap but not advance past it."
    )


def test_ds62_history_page_size_change_resets_offset():
    """Page-size change resets offset to 0; otherwise the page indicator lies.

    Without this, switching from 50-per-page (offset=200) to 200-per-page
    leaves the user on row 200+ with no visual cue that page 1 now includes
    rows 1..200.
    """
    js = _read_static("dashboard.js")
    # Anchor on the size variable's change listener — the only place page-size
    # mutation is processed. Slice from the listener registration to the next
    # closing `});` so the assertion can't accidentally pass on offset-resets
    # that live elsewhere (e.g. strategy-switch in _setActiveStrategy).
    idx = js.find('size.addEventListener("change"')
    assert idx != -1, "size.addEventListener('change') not found in dashboard.js"
    end = js.find("});", idx)
    assert end != -1, "could not find end of size change handler"
    handler = js[idx:end]
    assert (
        "_stratHistoryOffset = 0" in handler
    ), "page-size change handler does not reset _stratHistoryOffset to 0"


def test_ds63_history_abort_controller_replaced_per_fetch():
    """Every fetchStrategyFills call must replace _stratHistoryAbort, not reuse.

    AbortController instances are single-use; reusing one means the second
    fetch's signal is already aborted and the request never lands. Locks the
    'one controller per call' contract.
    """
    js = _read_static("dashboard.js")
    fn_idx = js.find("async function fetchStrategyFills")
    assert fn_idx != -1
    body = js[fn_idx : fn_idx + 2000]
    assert "_stratHistoryAbort.abort" in body, "no abort() of prior controller"
    assert (
        "_stratHistoryAbort = new AbortController" in body
    ), "_stratHistoryAbort must be REPLACED with a new AbortController, not reused"


def test_ds64_history_columns_match_endpoint_payload_keys():
    """Every column key in _STRAT_HISTORY_COLS must be a real fills payload key.

    Drift between the JS column list and the server payload would silently
    render `—` for an entire column when the server changes the field name.
    Catches schema renames before deploy.
    """
    js = _read_static("dashboard.js")
    import re

    m = re.search(r"_STRAT_HISTORY_COLS\s*=\s*\[(.*?)\];", js, re.DOTALL)
    assert m, "could not locate _STRAT_HISTORY_COLS in dashboard.js"
    keys = set(re.findall(r'"(\w+)"\s*,\s*"', m.group(1)))
    assert keys, "column key list parsed empty — check the regex"

    # Spot-check the keys we know come from /api/strategies/{name}/fills:
    # dashboard/app.py:424-432 returns a dict from the trades row, which has
    # filled_at/action/quantity/fill_price/cost_basis/realized_pnl/real_r_multiple/strategy_params.
    expected = {
        "filled_at",
        "action",
        "quantity",
        "fill_price",
        "cost_basis",
        "realized_pnl",
        "real_r_multiple",
        "strategy_params",
    }
    missing = expected - keys
    extra = keys - expected
    assert not missing, f"history columns missing expected keys: {missing}"
    assert not extra, f"history columns include unexpected keys: {extra}"


def test_ds66_history_fetch_passes_abort_signal():
    """fetchStrategyFills must pass the controller's signal to fetch().

    Replacing the controller is necessary but not sufficient — if a refactor
    drops the `signal: myAbort.signal` argument, abort() becomes a no-op
    and stale responses can race-render over newer ones.
    """
    js = _read_static("dashboard.js")
    fn_idx = js.find("async function fetchStrategyFills")
    assert fn_idx != -1
    body = js[fn_idx : fn_idx + 2000]
    assert "signal: myAbort.signal" in body, (
        "fetch(...) inside fetchStrategyFills does not pass signal: myAbort.signal — "
        "AbortController is wired but never consulted by the request."
    )


def test_ds67_strategy_switch_resets_history_offset():
    """_setActiveStrategy must reset _stratHistoryOffset to 0 before fetching fills.

    Without the reset, switching from a paginated view of Strat-A (offset=200)
    to Strat-B would render Strat-B starting at offset=200 — likely empty,
    confusing, and dependent on whatever the previous strategy's state was.
    """
    js = _read_static("dashboard.js")
    fn_idx = js.find("function _setActiveStrategy")
    assert fn_idx != -1, "_setActiveStrategy not found in dashboard.js"
    body = js[fn_idx : _js_decl_end(js, fn_idx)]
    assert (
        "_stratHistoryOffset = 0" in body
    ), "_setActiveStrategy does not reset _stratHistoryOffset before fetching"
    assert (
        "fetchStrategyFills" in body
    ), "_setActiveStrategy does not call fetchStrategyFills — the hook is missing"


def test_ds68_empty_state_branches_on_total():
    """Empty-state placeholder must distinguish 'no fills ever' from 'empty page'.

    Showing "No fills yet" when the user has paginated past the last filled
    page (total > 0 but page returned []) misleads them into thinking the
    strategy has no history. The renderer must branch on _stratHistoryTotal.
    """
    js = _read_static("dashboard.js")
    fn_idx = js.find("function _renderHistoryRows")
    assert fn_idx != -1
    body = js[fn_idx : _js_decl_end(js, fn_idx)]
    assert "_stratHistoryTotal" in body, (
        "_renderHistoryRows does not consult _stratHistoryTotal — empty-state "
        "placeholder cannot distinguish 'no fills ever' from 'paginated past last'"
    )
    assert "No fills on this page" in body, "missing the 'No fills on this page' placeholder branch"


def test_ds69_history_column_order_matches_thead_and_colspan_locked():
    """Lock three things together: (1) JS column display-name order matches
    the static `<thead>` order in index.html; (2) the count matches; (3) the
    static-row `colspan` literal in HTML matches `_STRAT_HISTORY_COLS.length`.

    Catches three classes of regression that ds64 misses:
      * Someone reorders `_STRAT_HISTORY_COLS` — visual data moves under the
        wrong header without changing the key membership set.
      * Someone adds a `<th>` to index.html without adding a JS column.
      * Someone changes `colspan` in HTML or JS but not the other; the empty
        / loading-state row then misrenders during the brief no-data window.
    """
    import html as html_lib
    import re

    js = _read_static("dashboard.js")
    html_text = _read_static("index.html")

    # 1. Display-name list from _STRAT_HISTORY_COLS (snake-case key + display).
    cols_m = re.search(r"_STRAT_HISTORY_COLS\s*=\s*\[(.*?)\];", js, re.DOTALL)
    assert cols_m, "could not locate _STRAT_HISTORY_COLS in dashboard.js"
    # Each entry is `["snake_case", "Display Name"]` — capture the second slot.
    display_names = re.findall(r'\[\s*"\w+"\s*,\s*"([^"]+)"\s*\]', cols_m.group(1))
    assert display_names, "_STRAT_HISTORY_COLS parse returned no display names"

    # 2. <thead> of the strat-history table specifically. Slice between the
    # table id and the FIRST </thead> after it, so other tables in index.html
    # (e.g. fills-table) don't bleed in.
    tbl_idx = html_text.find('id="strat-history"')
    assert tbl_idx != -1, "strat-history table not found in index.html"
    thead_end = html_text.find("</thead>", tbl_idx)
    assert thead_end != -1, "strat-history <thead> not found"
    thead = html_text[tbl_idx:thead_end]

    # <th>...</th> text (markup is clean — no nested tags). Unescape `&amp;`
    # so "Realized P&amp;L" → "Realized P&L" matches the JS display name.
    th_texts = [html_lib.unescape(t).strip() for t in re.findall(r"<th[^>]*>([^<]+)</th>", thead)]
    assert th_texts, "no <th> cells parsed from strat-history thead"

    # 3a. Count parity.
    assert len(th_texts) == len(display_names), (
        f"<th> count ({len(th_texts)}) != _STRAT_HISTORY_COLS length "
        f"({len(display_names)}). thead={th_texts!r}, cols={display_names!r}"
    )

    # 3b. Order parity.
    assert th_texts == display_names, (
        f"<th> order does not match _STRAT_HISTORY_COLS display names. "
        f"thead={th_texts!r}, cols={display_names!r}. "
        "Visual data will render under the wrong header."
    )

    # 4. colspan literal in the static placeholder row must equal column count.
    span_m = re.search(
        r'<tbody[^>]*id="strat-history-body".*?colspan="(\d+)"',
        html_text,
        re.DOTALL,
    )
    assert span_m, "could not locate colspan on the strat-history placeholder row"
    static_span = int(span_m.group(1))
    assert static_span == len(display_names), (
        f'static colspan="{static_span}" in index.html does not equal '
        f"_STRAT_HISTORY_COLS.length ({len(display_names)}). The empty/loading "
        "row will misrender."
    )


def test_ds65_history_html_has_required_ids_and_aria():
    """index.html must declare every id the JS reads, plus aria-busy on the table.

    A missing id makes the JS silently no-op (getElementById returns null,
    early return). aria-busy is required for screen-reader users to perceive
    loading state.
    """
    html = _read_static("index.html")
    for elem_id in (
        "strat-history",
        "strat-history-body",
        "strat-history-status",
        "strat-history-page",
        "strat-history-prev",
        "strat-history-next",
        "strat-history-pagesize",
    ):
        assert f'id="{elem_id}"' in html, f"index.html missing id={elem_id!r}"
    # aria-busy must be present on the table for the JS to flip it.
    assert 'id="strat-history"' in html
    assert 'aria-busy="false"' in html, "history table missing aria-busy attribute"


def test_ds54_reset_all_rate_state_clears_both_dicts():
    """Unit test for `_reset_all_rate_state`: both rate-state dicts get cleared.

    Strictly stronger than a fixture-teardown guard — does not depend on
    pytest finalizer ordering, and would FAIL if a future refactor stops
    clearing `_SESSION_RATE_STATE`. The earlier ad-hoc helper in
    `test_dashboard.py` only cleared `_rate_state`, masking the per-session
    equity-history quota leak this test now locks.
    """
    from tests.conftest import _reset_all_rate_state

    with dashboard_app._rate_lock:
        dashboard_app._rate_state["10.0.0.99"] = {"attempts": [1.0]}
    with dashboard_app._session_rate_lock:
        dashboard_app._SESSION_RATE_STATE["any-session-id"] = {"attempts": [1.0]}

    _reset_all_rate_state()

    with dashboard_app._rate_lock:
        assert dashboard_app._rate_state == {}
    with dashboard_app._session_rate_lock:
        assert dashboard_app._SESSION_RATE_STATE == {}


# ── DS-70..79: CSV export via ?format=csv (Session 3c) ─────────────────────
#
# `?format=csv` on /api/strategies/{name}/fills returns a buffered CSV
# attachment. Buffered (not streamed) because TradeLog.connection() closes its
# sqlite conn on __exit__ — a lazy StreamingResponse generator would iterate
# after close and raise. These tests lock the wire format (BOM, CRLF, headers),
# the formula-injection guard, the 401→404→400 precedence, and the row cap.

_CSV_HEADER = ",".join(dashboard_app._CSV_COLUMNS)


def test_ds70_csv_export_happy_path_wire_format(fresh_trade_log, dashboard_client):
    """End-to-end: status, content-type, BOM, CRLF header row, attachment headers."""
    import csv as _csv
    import io as _io

    log = fresh_trade_log
    log.record(*_mkfill(1, "SMACrossover-QQQ", action="BUY", qty=10, price=100)[:2])

    r = dashboard_client.get("/api/strategies/SMACrossover-QQQ/fills?format=csv")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "text/csv; charset=utf-8"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["content-disposition"] == (
        'attachment; filename="SMACrossover-QQQ-fills.csv"; '
        "filename*=UTF-8''SMACrossover-QQQ-fills.csv"
    )
    body = r.content
    # UTF-8 BOM so Excel detects encoding.
    assert body.startswith(b"\xef\xbb\xbf")
    text = body.decode("utf-8-sig")
    lines = text.split("\r\n")
    # RFC 4180 CRLF terminator; header row matches _CSV_COLUMNS order.
    assert lines[0] == _CSV_HEADER
    # The data row must carry the seeded fill's actual values — a weak
    # len()>=2 check would pass even if the row were malformed.
    rows = list(_csv.reader(_io.StringIO(text)))
    assert len(rows) == 2  # header + exactly one seeded fill
    data = dict(zip(rows[0], rows[1]))
    assert data["action"] == "BUY"
    assert data["quantity"] == "10.0"
    assert data["fill_price"] == "100.0"


def test_ds71_csv_columns_locked_against_js_constant(fresh_trade_log):
    """Server _CSV_COLUMNS must mirror dashboard.js _STRAT_HISTORY_COLS keys/order.

    Locks the DB-X10 drift risk: a server-side column rename that the JS table
    constant doesn't follow (or vice versa) would silently desync the export
    from the on-screen table. This is the symmetric guard to ds64/ds69.
    """
    import re

    js = _read_static("dashboard.js")
    m = re.search(r"_STRAT_HISTORY_COLS\s*=\s*\[(.*?)\];", js, re.DOTALL)
    assert m, "could not locate _STRAT_HISTORY_COLS in dashboard.js"
    js_keys = re.findall(r'\[\s*"(\w+)"\s*,', m.group(1))
    assert js_keys, "parsed no keys from _STRAT_HISTORY_COLS"
    assert js_keys == dashboard_app._CSV_COLUMNS, (
        f"_CSV_COLUMNS (server) drifted from _STRAT_HISTORY_COLS (JS): "
        f"server={dashboard_app._CSV_COLUMNS}, js={js_keys}"
    )


def test_ds72_csv_safe_cell_guards_strings_not_numbers():
    """_csv_safe_cell neutralises formula-leading STRINGS, leaves numbers raw."""
    f = dashboard_app._csv_safe_cell
    # Formula-injection prefixes on string cells get an apostrophe guard.
    for danger in ("=cmd", "+1", "-1+2", "@SUM", "\tx", "\rx", "\nx"):
        assert f(danger) == "'" + danger, f"{danger!r} not guarded"
    # Numeric cells are returned raw — a negative realized_pnl is a number,
    # not a formula; guarding it would corrupt the export into text.
    assert f(-5.0) == -5.0
    assert f(-100) == -100
    assert f(0.0) == 0.0
    # None → empty string; safe-leading strings pass through untouched.
    assert f(None) == ""
    assert f('{"sma_fast": 10}') == '{"sma_fast": 10}'
    assert f("BUY") == "BUY"


def test_ds73_csv_negative_pnl_raw_and_injection_blob_guarded(fresh_trade_log):
    """Integration: negative realized_pnl exports raw; a =-leading params blob is guarded."""
    import csv as _csv
    import io as _io
    import sqlite3 as _sqlite

    log = fresh_trade_log
    log.record(*_mkfill(1, "SMACrossover-QQQ", action="BUY", qty=10, price=100)[:2])
    # SELL at a loss → realized_pnl = (90 - 100) * 10 = -100.0
    log.record(*_mkfill(2, "SMACrossover-QQQ", action="SELL", qty=10, price=90, cost_basis=100)[:2])
    # Force a formula-leading strategy_params blob onto the SELL row.
    with _sqlite.connect(log._db_path) as conn:
        conn.execute("UPDATE trades SET strategy_params = '=DANGER()' WHERE id = 2")

    body = dashboard_app._build_strategy_fills_csv("SMACrossover-QQQ")
    rows = list(_csv.reader(_io.StringIO(body.decode("utf-8-sig"))))
    header = rows[0]
    # rows are id DESC → first data row is the SELL (id=2)
    sell = dict(zip(header, rows[1]))
    assert sell["realized_pnl"] == "-100.0"  # raw negative number, NOT '-100.0
    assert not sell["realized_pnl"].startswith("'")
    assert sell["strategy_params"] == "'=DANGER()"  # apostrophe-guarded


def test_ds74_csv_unauth_returns_401_before_404(dashboard_client_unauth):
    """An unauth request to a *bad* strategy name must return 401, not 404.

    This is the load-bearing precedence check: if `_resolve_strategy` ran
    before `_require_session`, the bad name would raise 404 first. Getting 401
    proves `_require_session` is evaluated first (pre-impl CR C1). The 400
    bad-format leg is trivially also beaten — 400 is raised in the handler
    body, which never runs when a dependency raises. The 404-beats-400 leg is
    covered by ds75 + ds76.
    """
    r = dashboard_client_unauth.get("/api/strategies/NOTAREALSTRATEGY/fills?format=xml")
    assert r.status_code == 401


def test_ds75_csv_unknown_strategy_returns_404(fresh_trade_log, dashboard_client):
    """Authenticated + unknown strategy → 404 (resolve_strategy), even with format=csv."""
    r = dashboard_client.get("/api/strategies/NOTAREALSTRATEGY/fills?format=csv")
    assert r.status_code == 404


def test_ds76_csv_bad_format_returns_400(fresh_trade_log, dashboard_client):
    """Authenticated + valid strategy + unknown format → 400."""
    r = dashboard_client.get("/api/strategies/SMACrossover-QQQ/fills?format=xml")
    assert r.status_code == 400


def test_ds77_csv_empty_db_is_exactly_bom_header_crlf(fresh_trade_log):
    """Empty DB → body is exactly BOM + header row + CRLF, nothing else."""
    body = dashboard_app._build_strategy_fills_csv("SMACrossover-QQQ")
    expected = ("\ufeff" + _CSV_HEADER + "\r\n").encode("utf-8")
    assert body == expected


def test_ds78_csv_corrupt_strategy_params_exports_raw(fresh_trade_log):
    """Corrupt JSON in strategy_params exports the raw blob — never crashes the build.

    Mirrors ds24 (which asserts the JSON branch serves null). The CSV branch
    deliberately does NOT json.loads the column, so a corrupt blob round-trips
    verbatim instead of becoming null or raising.
    """
    import csv as _csv
    import io as _io
    import sqlite3 as _sqlite

    log = fresh_trade_log
    log.record(*_mkfill(1, "SMACrossover-QQQ", action="BUY", qty=10, price=100)[:2])
    with _sqlite.connect(log._db_path) as conn:
        conn.execute("UPDATE trades SET strategy_params = '{not valid json' WHERE id = 1")

    body = dashboard_app._build_strategy_fills_csv("SMACrossover-QQQ")
    rows = list(_csv.reader(_io.StringIO(body.decode("utf-8-sig"))))
    row = dict(zip(rows[0], rows[1]))
    # Leading "{" is a safe char → exported verbatim, not guarded, not nulled.
    assert row["strategy_params"] == "{not valid json"


def test_ds79_csv_row_cap_returns_413(fresh_trade_log, dashboard_client, monkeypatch):
    """A strategy with more fills than _CSV_ROW_CAP returns 413 — never silent truncation."""
    monkeypatch.setattr(dashboard_app, "_CSV_ROW_CAP", 2)
    log = fresh_trade_log
    for i in range(3):
        log.record(*_mkfill(i + 1, "SMACrossover-QQQ", action="BUY", qty=1, price=100 + i)[:2])
    r = dashboard_client.get("/api/strategies/SMACrossover-QQQ/fills?format=csv")
    assert r.status_code == 413
