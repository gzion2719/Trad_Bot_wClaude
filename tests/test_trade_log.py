"""Section 16: Trade log tests — no IBKR connection needed."""

import gc
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from data.trade_log import TradeLog, _round_profit_factor
from models.order import OrderResult, OrderStatus


def _make_fill(symbol, action, qty, price, order_id=1):
    return OrderResult(
        order_id=order_id,
        symbol=symbol,
        action=action,
        quantity=qty,
        order_type="LMT",
        tif="GTC",
        status=OrderStatus.FILLED,
        filled=qty,
        remaining=0,
        avg_fill_price=price,
        limit_price=None,
        stop_price=None,
        submitted_at=datetime.now(timezone.utc),
    )


def _tmp_log():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return TradeLog(db_path=Path(tmp.name)), Path(tmp.name)


def _close_log(log, db_path):
    gc.collect()
    for ext in ("", "-wal", "-shm"):
        p = Path(str(db_path) + ext)
        if p.exists():
            try:
                p.unlink()
            except PermissionError:
                pass


def test_tl01_creates_db_and_records_fill():
    log, path = _tmp_log()
    try:
        assert log.count() == 0
        log.record(_make_fill("AAPL", "BUY", 10, 150.0), "TestStrategy")
        assert log.count() == 1
    finally:
        _close_log(log, path)


def test_tl02_get_history_returns_fills():
    log, path = _tmp_log()
    try:
        log.record(_make_fill("MSFT", "BUY", 5, 400.0, order_id=1), "S1")
        log.record(_make_fill("MSFT", "SELL", 5, 410.0, order_id=2), "S1")
        assert len(log.get_history(symbol="MSFT")) == 2
    finally:
        _close_log(log, path)


def test_tl03_get_history_filters_by_symbol():
    log, path = _tmp_log()
    try:
        log.record(_make_fill("AAPL", "BUY", 1, 150.0, order_id=1), "S1")
        log.record(_make_fill("MSFT", "BUY", 1, 400.0, order_id=2), "S1")
        assert len(log.get_history(symbol="AAPL")) == 1
        assert len(log.get_history(symbol="MSFT")) == 1
        assert len(log.get_history()) == 2
    finally:
        _close_log(log, path)


def test_tl04_daily_summary_correct_counts():
    log, path = _tmp_log()
    try:
        log.record(_make_fill("GE", "BUY", 10, 10.0, order_id=1), "S1")
        log.record(_make_fill("GE", "SELL", 10, 11.0, order_id=2), "S1")
        s = log.daily_summary()
        assert s["total_trades"] == 2
        assert s["buys"] == 1
        assert s["sells"] == 1
    finally:
        _close_log(log, path)


def test_tl05_unfilled_order_not_recorded():
    log, path = _tmp_log()
    try:
        unfilled = OrderResult(
            order_id=99,
            symbol="GE",
            action="BUY",
            quantity=1,
            order_type="LMT",
            tif="GTC",
            status=OrderStatus.SUBMITTED,
            filled=0,
            remaining=1,
            avg_fill_price=None,
            limit_price=10.0,
            stop_price=None,
            submitted_at=datetime.now(timezone.utc),
        )
        log.record(unfilled, "S1")
        assert log.count() == 0
    finally:
        _close_log(log, path)


# ── _round_profit_factor — sentinel contract (TL-PF-01..05) ─────────────────


def test_tl_pf_01_none_passthrough():
    assert _round_profit_factor(None) is None


def test_tl_pf_02_finite_float_rounded_to_3dp():
    assert _round_profit_factor(1.23456) == 1.235
    assert _round_profit_factor(0.0) == 0.0
    assert _round_profit_factor(-2.5) == -2.5


def test_tl_pf_03_positive_inf_to_string_sentinel():
    """+inf → "Infinity" — FastAPI's encoder would drop float('inf') to null."""
    assert _round_profit_factor(float("inf")) == "Infinity"


def test_tl_pf_04_negative_inf_to_string_sentinel():
    """-inf → "-Infinity" — forward-defensive; producer cannot reach today."""
    assert _round_profit_factor(float("-inf")) == "-Infinity"


def test_tl_pf_05_nan_to_none():
    """nan → None — forward-defensive; producer cannot reach today.

    nan as a JSON-wire value is also rejected by FastAPI's default encoder,
    and None renders cleanly as "—" in the dashboard.
    """
    assert _round_profit_factor(float("nan")) is None
