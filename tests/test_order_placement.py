"""Sections 4-6: Order placement, duplicate prevention, cancellation — requires IBKR TWS."""

import os

import pytest

from broker.ibkr_client import IBKRClient
from broker.order_manager import DuplicateOrderError, OrderManager
from models.order import OrderAction, OrderRequest, OrderType, TimeInForce

IS_CI = bool(os.getenv("GITHUB_ACTIONS"))
pytestmark = pytest.mark.skipif(IS_CI, reason="requires IBKR TWS connection")


# ── Section 4: Order placement ────────────────────────────────────────────────


def test_p03_limit_buy_below_market(live_client):
    c, o = live_client
    price = c.get_market_price("GE")
    r = OrderRequest(
        symbol="GE",
        action=OrderAction.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=round(price * 0.5, 2),
        tif=TimeInForce.GTC,
    )
    result = o.place_order(r)
    assert result.order_id > 0
    assert result.status.value in ("PreSubmitted", "Submitted", "PendingSubmit")
    o.cancel_order(result.order_id)
    c.ib.sleep(0.5)


def test_p04_limit_sell_above_market(live_client):
    c, o = live_client
    price = c.get_market_price("MSFT")
    r = OrderRequest(
        symbol="MSFT",
        action=OrderAction.SELL,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=round(price * 2.0, 2),
        tif=TimeInForce.GTC,
    )
    result = o.place_order(r)
    assert result.order_id > 0
    assert result.status.value in ("PreSubmitted", "Submitted", "PendingSubmit")
    o.cancel_order(result.order_id)
    c.ib.sleep(0.5)


def test_p05_gtc_limit_stays_open(live_client):
    c, o = live_client
    o.cancel_all("IBM")
    c.ib.sleep(0.5)
    price = c.get_market_price("IBM")
    r = OrderRequest(
        symbol="IBM",
        action=OrderAction.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=round(price * 0.5, 2),
        tif=TimeInForce.GTC,
    )
    result = o.place_order(r)
    assert result.order_id > 0
    assert result.status.value in ("PreSubmitted", "Submitted", "PendingSubmit")
    o.cancel_order(result.order_id)
    c.ib.sleep(0.5)


def test_p07_invalid_symbol_raises(live_client):
    _, o = live_client
    r = OrderRequest(symbol="XYZXYZ999", action=OrderAction.BUY, quantity=1)
    with pytest.raises(RuntimeError):
        o.place_order(r)


def test_p08_order_when_not_connected_raises():
    c = IBKRClient()  # fresh, not connected
    o = OrderManager(c)
    r = OrderRequest(symbol="AAPL", action=OrderAction.BUY, quantity=1)
    with pytest.raises(ConnectionError):
        o.place_order(r)


# ── Section 5: Duplicate prevention ──────────────────────────────────────────


def test_dup01_same_buy_twice_raises(live_client):
    c, o = live_client
    price = c.get_market_price("GE")
    r = OrderRequest(
        symbol="GE",
        action=OrderAction.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=round(price * 0.5, 2),
        tif=TimeInForce.GTC,
    )
    result = o.place_order(r)
    try:
        with pytest.raises(DuplicateOrderError):
            o.place_order(r)
    finally:
        o.cancel_order(result.order_id)
        c.ib.sleep(0.5)


def test_dup02_buy_then_sell_not_blocked(live_client):
    c, o = live_client
    price = c.get_market_price("MSFT")
    buy = OrderRequest(
        symbol="MSFT",
        action=OrderAction.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=round(price * 0.5, 2),
        tif=TimeInForce.GTC,
    )
    sell = OrderRequest(
        symbol="MSFT",
        action=OrderAction.SELL,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=round(price * 2.0, 2),
        tif=TimeInForce.GTC,
    )
    r1 = o.place_order(buy)
    r2 = o.place_order(sell)
    assert r2.order_id > 0
    o.cancel_order(r1.order_id)
    o.cancel_order(r2.order_id)
    c.ib.sleep(0.5)


def test_dup03_after_cancel_can_replace(live_client):
    c, o = live_client
    o.cancel_all("GE")
    c.ib.sleep(0.5)
    price = c.get_market_price("GE")
    r = OrderRequest(
        symbol="GE",
        action=OrderAction.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=round(price * 0.5, 2),
        tif=TimeInForce.GTC,
    )
    r1 = o.place_order(r)
    o.cancel_order(r1.order_id)
    c.ib.sleep(1)
    r2 = o.place_order(r)
    assert r2.order_id > 0
    o.cancel_order(r2.order_id)
    c.ib.sleep(0.5)


def test_dup05_allow_duplicate_bypasses_check(live_client):
    c, o = live_client
    price = c.get_market_price("GE")
    r = OrderRequest(
        symbol="GE",
        action=OrderAction.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=round(price * 0.5, 2),
        tif=TimeInForce.GTC,
    )
    o.place_order(r)
    r2 = o.place_order(r, allow_duplicate=True)
    assert r2.order_id > 0
    o.cancel_all("GE")
    c.ib.sleep(0.5)


# ── Section 6: Cancellation ───────────────────────────────────────────────────


def test_x01_cancel_open_order_fires_callback(live_client):
    c, o = live_client
    o._clear_callbacks()
    fired = []
    o.on_cancel(lambda r: fired.append(r))
    price = c.get_market_price("GE")
    r = OrderRequest(
        symbol="GE",
        action=OrderAction.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=round(price * 0.5, 2),
        tif=TimeInForce.GTC,
    )
    result = o.place_order(r)
    cancelled = o.cancel_order(result.order_id)
    c.ib.sleep(1)
    assert cancelled is True
    assert len(fired) > 0


def test_x03_cancel_already_cancelled_returns_false(live_client):
    c, o = live_client
    price = c.get_market_price("MSFT")
    r = OrderRequest(
        symbol="MSFT",
        action=OrderAction.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=round(price * 0.5, 2),
        tif=TimeInForce.GTC,
    )
    result = o.place_order(r)
    o.cancel_order(result.order_id)
    c.ib.sleep(1)
    assert o.cancel_order(result.order_id) is False


def test_x04_cancel_nonexistent_id_returns_false(live_client):
    _, o = live_client
    assert o.cancel_order(999999) is False


def test_x05_cancel_all_with_no_orders_returns_zero(live_client):
    c, o = live_client
    o.cancel_all()
    c.ib.sleep(1)
    assert o.cancel_all() == 0


def test_x06_cancel_all_symbol_only_cancels_that_symbol(live_client):
    c, o = live_client
    price_ge = c.get_market_price("GE")
    price_msft = c.get_market_price("MSFT")
    ge = OrderRequest(
        symbol="GE",
        action=OrderAction.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=round(price_ge * 0.5, 2),
        tif=TimeInForce.GTC,
    )
    msft = OrderRequest(
        symbol="MSFT",
        action=OrderAction.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=round(price_msft * 0.5, 2),
        tif=TimeInForce.GTC,
    )
    o.place_order(ge)
    r_msft = o.place_order(msft)
    cancelled = o.cancel_all("GE")
    c.ib.sleep(1)
    assert cancelled == 1
    remaining = o.get_open_orders("MSFT")
    assert any(r.order_id == r_msft.order_id for r in remaining)
    o.cancel_all("MSFT")
    c.ib.sleep(0.5)
