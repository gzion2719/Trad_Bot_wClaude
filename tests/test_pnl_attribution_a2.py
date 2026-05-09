"""
MS-A2 tests — per-strategy P&L attribution wiring.

A2 wires PnLPoller to query TradeLog per strategy (replacing the account-level
RealizedPnL feed that made every strategy halt when account total breached any
single cap). A2 covers:

  1. TradeLog.realized_pnl_since(strategy, cutoff) returns the right number.
  2. ET-trading-day cutoff (UTC ISO) excludes pre-9:30-ET fills.
  3. RiskManager gains a strategy_name field; backtest path with default None
     keeps working unchanged.
  4. RiskManager sticky halt: once breached today, stays halted until reset_daily
     even if intraday P&L recovers above the cap.
  5. StrategyRunner.update_daily_pnl_per_strategy feeds each RM its own number.
  6. The bug-of-record: SMA breaches cap → SMA halts → RSI2MR keeps trading.

No IBKR connection required.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

try:
    import zoneinfo

    _ET = zoneinfo.ZoneInfo("America/New_York")
except (ImportError, KeyError):  # pragma: no cover
    _ET = timezone(timedelta(hours=-5))  # fallback, tests will still run


from data.trade_log import TradeLog
from models.order import OrderResult, OrderStatus
from risk.risk_manager import RiskManager

# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════


def _result(
    *,
    action: str,
    symbol: str = "QQQ",
    filled: float = 100.0,
    avg_fill_price: float = 460.0,
    cost_basis=None,
    submitted_at=None,
    order_id: int = 1,
) -> OrderResult:
    r = OrderResult(
        order_id=order_id,
        symbol=symbol,
        action=action,
        quantity=filled,
        order_type="MKT",
        tif="GTC",
        status=OrderStatus.FILLED,
        filled=filled,
        remaining=0.0,
        avg_fill_price=avg_fill_price,
        limit_price=None,
        stop_price=None,
        cost_basis=cost_basis,
    )
    if submitted_at is not None:
        r.submitted_at = submitted_at
    return r


def _et_trading_cutoff(now_et: datetime) -> str:
    """Replicates the cutoff logic in main.py for tests."""
    today_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    if now_et < today_open:
        today_open -= timedelta(days=1)
    return today_open.astimezone(timezone.utc).isoformat()


# ══════════════════════════════════════════════════════════════════════════════
# A2.01 — single-strategy sum
# ══════════════════════════════════════════════════════════════════════════════


def test_a2_01_realized_pnl_today_single_strategy(tmp_path):
    log = TradeLog(db_path=tmp_path / "trades.db")
    cutoff = "2026-05-09T13:30:00+00:00"
    fills = [
        _result(action="SELL", filled=10, avg_fill_price=460, cost_basis=450),  # +100
        _result(action="SELL", filled=10, avg_fill_price=455, cost_basis=450, order_id=2),  # +50
        _result(action="SELL", filled=10, avg_fill_price=445, cost_basis=450, order_id=3),  # -50
    ]
    for r in fills:
        r.submitted_at = datetime(2026, 5, 9, 14, 0, 0, tzinfo=timezone.utc)  # > cutoff
        log.record(r, strategy_name="SMA-QQQ")

    pnl = log.realized_pnl_since("SMA-QQQ", cutoff)
    assert pnl == pytest.approx(100.0)  # 100 + 50 - 50


# ══════════════════════════════════════════════════════════════════════════════
# A2.02 — filter by strategy_name
# ══════════════════════════════════════════════════════════════════════════════


def test_a2_02_filters_by_strategy(tmp_path):
    log = TradeLog(db_path=tmp_path / "trades.db")
    cutoff = "2026-05-09T13:30:00+00:00"
    later = datetime(2026, 5, 9, 16, 0, 0, tzinfo=timezone.utc)

    sma_fill = _result(
        action="SELL", filled=10, avg_fill_price=460, cost_basis=450, submitted_at=later
    )
    rsi_fill = _result(
        action="SELL",
        symbol="SPY",
        filled=10,
        avg_fill_price=410,
        cost_basis=400,
        submitted_at=later,
        order_id=2,
    )
    log.record(sma_fill, strategy_name="SMA-QQQ")
    log.record(rsi_fill, strategy_name="RSI-SPY")

    assert log.realized_pnl_since("SMA-QQQ", cutoff) == pytest.approx(100.0)
    assert log.realized_pnl_since("RSI-SPY", cutoff) == pytest.approx(100.0)
    assert log.realized_pnl_since("UNKNOWN", cutoff) == pytest.approx(0.0)


# ══════════════════════════════════════════════════════════════════════════════
# A2.03 — no fills returns 0.0
# ══════════════════════════════════════════════════════════════════════════════


def test_a2_03_no_fills_returns_zero(tmp_path):
    log = TradeLog(db_path=tmp_path / "trades.db")
    assert log.realized_pnl_since("SMA-QQQ", "2026-05-09T13:30:00+00:00") == 0.0


# ══════════════════════════════════════════════════════════════════════════════
# A2.04 — pre-9:30-ET fill excluded from today's window
# ══════════════════════════════════════════════════════════════════════════════


def test_a2_04_pre_market_fill_excluded(tmp_path):
    log = TradeLog(db_path=tmp_path / "trades.db")
    # Cutoff = today 13:30 UTC (9:30 EDT). Fill at 13:00 UTC (9:00 EDT) precedes.
    cutoff = "2026-05-09T13:30:00+00:00"
    pre_open = datetime(2026, 5, 9, 13, 0, 0, tzinfo=timezone.utc)
    log.record(
        _result(
            action="SELL", filled=10, avg_fill_price=460, cost_basis=450, submitted_at=pre_open
        ),
        strategy_name="SMA-QQQ",
    )
    assert log.realized_pnl_since("SMA-QQQ", cutoff) == 0.0


# ══════════════════════════════════════════════════════════════════════════════
# A2.05 — overnight fill attributed to previous trading day
# ══════════════════════════════════════════════════════════════════════════════


def test_a2_05_overnight_fill_attributed_to_prev_day(tmp_path):
    log = TradeLog(db_path=tmp_path / "trades.db")
    # Cutoff = today's 9:30 EDT = 13:30 UTC. Overnight stop fires at 22:00 ET
    # (= 02:00 UTC the next day). For "today" — i.e. the day after the overnight
    # — that fill is BEFORE the cutoff, so attributed to prior trading day.
    cutoff = "2026-05-09T13:30:00+00:00"
    overnight = datetime(2026, 5, 9, 2, 0, 0, tzinfo=timezone.utc)  # 22:00 ET prev day
    log.record(
        _result(
            action="SELL", filled=10, avg_fill_price=440, cost_basis=450, submitted_at=overnight
        ),
        strategy_name="SMA-QQQ",
    )
    assert log.realized_pnl_since("SMA-QQQ", cutoff) == 0.0


# ══════════════════════════════════════════════════════════════════════════════
# A2.06 / A2.07 — DST transitions (cutoff handling)
# ══════════════════════════════════════════════════════════════════════════════


def test_a2_06_dst_spring_forward_cutoff():
    """On 2026-03-08 (US spring forward), 9:30 ET = 13:30 UTC (EDT, UTC-4)."""
    now = datetime(2026, 3, 8, 10, 0, 0, tzinfo=_ET)  # after the spring-forward jump
    cutoff = _et_trading_cutoff(now)
    assert cutoff.endswith("+00:00")
    assert "13:30:00" in cutoff


def test_a2_07_dst_fall_back_cutoff():
    """On 2026-11-01 (US fall back), 9:30 ET = 14:30 UTC (EST, UTC-5)."""
    now = datetime(2026, 11, 1, 10, 0, 0, tzinfo=_ET)
    cutoff = _et_trading_cutoff(now)
    assert "14:30:00" in cutoff


# ══════════════════════════════════════════════════════════════════════════════
# A2.08 — runner per-strategy routing
# ══════════════════════════════════════════════════════════════════════════════


def _make_runner_for_test(handles, trade_log):
    """Build a StrategyRunner via __new__ so we exercise the real method body
    against real handles + trade_log without booting full infrastructure."""
    from runtime.strategy_runner import StrategyRunner as RealRunner

    runner = RealRunner.__new__(RealRunner)
    runner.handles = handles
    runner.trade_log = trade_log
    return runner


def test_a2_08_runner_per_strategy_routing(tmp_path):
    """StrategyRunner.update_daily_pnl_per_strategy feeds each RM its own number."""
    from runtime.strategy_runner import StrategyHandle

    log = TradeLog(db_path=tmp_path / "trades.db")
    cutoff = "2026-05-09T13:30:00+00:00"
    later = datetime(2026, 5, 9, 16, 0, 0, tzinfo=timezone.utc)

    log.record(
        _result(action="SELL", filled=10, avg_fill_price=440, cost_basis=450, submitted_at=later),
        strategy_name="SMA-QQQ",
    )  # -100
    log.record(
        _result(
            action="SELL",
            symbol="SPY",
            filled=20,
            avg_fill_price=410,
            cost_basis=400,
            submitted_at=later,
            order_id=2,
        ),
        strategy_name="RSI-SPY",
    )  # +200

    rm_sma = RiskManager(
        client=MagicMock(),
        order_manager=MagicMock(),
        max_daily_loss=-2_000.0,
        strategy_name="SMA-QQQ",
    )
    rm_rsi = RiskManager(
        client=MagicMock(),
        order_manager=MagicMock(),
        max_daily_loss=-2_000.0,
        strategy_name="RSI-SPY",
    )

    cfg_sma = MagicMock(name="SMA-QQQ")
    cfg_sma.name = "SMA-QQQ"
    cfg_rsi = MagicMock(name="RSI-SPY")
    cfg_rsi.name = "RSI-SPY"

    h_sma = StrategyHandle(cfg_sma, MagicMock(), rm_sma)
    h_rsi = StrategyHandle(cfg_rsi, MagicMock(), rm_rsi)

    runner = _make_runner_for_test([h_sma, h_rsi], log)
    runner.update_daily_pnl_per_strategy(cutoff)

    assert rm_sma.daily_pnl() == pytest.approx(-100.0)
    assert rm_rsi.daily_pnl() == pytest.approx(200.0)


# ══════════════════════════════════════════════════════════════════════════════
# A2.09 — THE BUG OF RECORD: independent halt
# ══════════════════════════════════════════════════════════════════════════════


def test_a2_09_independent_halt_one_strategy_breach_other_keeps_trading(tmp_path):
    """SMA loses $2100 (over cap), RSI2MR loses $400 (under cap). Pre-A2 both
    halt because account total -$2500 ≤ $-2000. With A2, only SMA halts."""
    from runtime.strategy_runner import StrategyHandle

    log = TradeLog(db_path=tmp_path / "trades.db")
    cutoff = "2026-05-09T13:30:00+00:00"
    later = datetime(2026, 5, 9, 16, 0, 0, tzinfo=timezone.utc)

    # SMA: 1 SELL with -$2100 P&L (entry 450, exit 429, qty 100)
    log.record(
        _result(action="SELL", filled=100, avg_fill_price=429, cost_basis=450, submitted_at=later),
        strategy_name="SMA-QQQ",
    )
    # RSI2MR: 1 SELL with -$400 P&L (entry 400, exit 396, qty 100)
    log.record(
        _result(
            action="SELL",
            symbol="SPY",
            filled=100,
            avg_fill_price=396,
            cost_basis=400,
            submitted_at=later,
            order_id=2,
        ),
        strategy_name="RSI-SPY",
    )

    rm_sma = RiskManager(
        client=MagicMock(),
        order_manager=MagicMock(),
        max_daily_loss=-2_000.0,
        strategy_name="SMA-QQQ",
    )
    rm_rsi = RiskManager(
        client=MagicMock(),
        order_manager=MagicMock(),
        max_daily_loss=-2_000.0,
        strategy_name="RSI-SPY",
    )

    cfg_sma = MagicMock(name="SMA-QQQ")
    cfg_sma.name = "SMA-QQQ"
    cfg_rsi = MagicMock(name="RSI-SPY")
    cfg_rsi.name = "RSI-SPY"

    runner = _make_runner_for_test(
        [
            StrategyHandle(cfg_sma, MagicMock(), rm_sma),
            StrategyHandle(cfg_rsi, MagicMock(), rm_rsi),
        ],
        log,
    )
    runner.update_daily_pnl_per_strategy(cutoff)

    # SMA breached its $-2000 cap → halted.
    assert rm_sma.is_halted() is True
    # RSI2MR is at -$400 (well under cap) → keeps trading. THIS is the bug fix.
    assert rm_rsi.is_halted() is False


# ══════════════════════════════════════════════════════════════════════════════
# A2.10 — NULL realized_pnl rows treated as 0
# ══════════════════════════════════════════════════════════════════════════════


def test_a2_10_null_realized_pnl_treated_as_zero(tmp_path):
    log = TradeLog(db_path=tmp_path / "trades.db")
    cutoff = "2026-05-09T13:30:00+00:00"
    later = datetime(2026, 5, 9, 16, 0, 0, tzinfo=timezone.utc)
    # No cost_basis → realized_pnl stored as NULL.
    log.record(
        _result(action="SELL", filled=10, avg_fill_price=460, cost_basis=None, submitted_at=later),
        strategy_name="SMA-QQQ",
    )
    assert log.realized_pnl_since("SMA-QQQ", cutoff) == 0.0
    assert log.count_null_pnl_since("SMA-QQQ", cutoff) == 1


# ══════════════════════════════════════════════════════════════════════════════
# A2.11 — RiskManager strategy_name=None backwards compat (backtest path)
# ══════════════════════════════════════════════════════════════════════════════


def test_a2_11_riskmanager_default_strategy_name_none():
    rm = RiskManager(
        client=MagicMock(),
        order_manager=MagicMock(),
        max_daily_loss=-500.0,
    )
    assert rm.strategy_name is None
    rm.update_daily_pnl(-1000.0)
    assert rm.is_halted() is True


# ══════════════════════════════════════════════════════════════════════════════
# A2.12 — Sticky halt: stays halted after intraday recovery
# ══════════════════════════════════════════════════════════════════════════════


def test_a2_12_sticky_halt_persists_after_recovery():
    rm = RiskManager(
        client=MagicMock(),
        order_manager=MagicMock(),
        max_daily_loss=-2_000.0,
        strategy_name="SMA-QQQ",
    )
    # Breach on first poll
    rm.update_daily_pnl(-2_100.0)
    assert rm.is_halted() is True
    # Intraday "recovery" (e.g., spike retraces) — still halted
    rm.update_daily_pnl(-1_500.0)
    assert rm.is_halted() is True
    rm.update_daily_pnl(0.0)
    assert rm.is_halted() is True
    # Reset clears sticky flag
    rm.reset_daily()
    assert rm.is_halted() is False


# ══════════════════════════════════════════════════════════════════════════════
# A2.14 — check() also honors sticky halt (CR critical fix)
# ══════════════════════════════════════════════════════════════════════════════


def test_a2_14_check_honors_sticky_halt_after_recovery():
    """Defense-in-depth: even if a caller bypasses is_halted() and calls check()
    directly with a recovered live P&L, the sticky flag must still block."""
    from models.order import OrderAction, OrderRequest
    from risk.risk_manager import RiskViolationError

    om = MagicMock()
    om.get_open_orders.return_value = []
    rm = RiskManager(
        client=MagicMock(),
        order_manager=om,
        max_daily_loss=-2_000.0,
        max_order_value=100_000.0,
        max_position_value=100_000.0,
        strategy_name="SMA-QQQ",
    )

    # Breach, then recover above cap.
    rm.update_daily_pnl(-2_100.0)
    rm.update_daily_pnl(-1_500.0)
    assert rm.daily_pnl() == pytest.approx(-1_500.0)  # live number reflects recovery

    req = OrderRequest(
        symbol="QQQ", action=OrderAction.BUY, quantity=10, exchange="SMART", currency="USD"
    )
    with pytest.raises(RiskViolationError, match="halted"):
        rm.check(req, current_price=450.0)


# ══════════════════════════════════════════════════════════════════════════════
# A2.13 — Index migration is idempotent (re-init works on existing DB)
# ══════════════════════════════════════════════════════════════════════════════


def test_a2_13_index_migration_idempotent(tmp_path):
    db_path = tmp_path / "trades.db"
    log1 = TradeLog(db_path=db_path)
    log1.record(
        _result(
            action="SELL",
            filled=10,
            avg_fill_price=460,
            cost_basis=450,
            submitted_at=datetime(2026, 5, 9, 16, 0, 0, tzinfo=timezone.utc),
        ),
        strategy_name="SMA-QQQ",
    )
    # Re-open the same DB — index creation must be idempotent.
    log2 = TradeLog(db_path=db_path)
    assert log2.count() == 1
    # Confirm index actually exists.
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
            ("idx_trades_strategy_filled",),
        ).fetchall()
    assert len(rows) == 1
