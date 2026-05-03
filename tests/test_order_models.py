"""Section 3: Order validation tests — no IBKR connection needed."""

import pytest

from models.order import OrderAction, OrderRequest, OrderType


def test_v01_quantity_zero_raises():
    with pytest.raises(ValueError):
        OrderRequest(symbol="AAPL", action=OrderAction.BUY, quantity=0)


def test_v02_quantity_negative_raises():
    with pytest.raises(ValueError):
        OrderRequest(symbol="AAPL", action=OrderAction.BUY, quantity=-5)


def test_v03_limit_order_no_price_raises():
    with pytest.raises(ValueError):
        OrderRequest(symbol="AAPL", action=OrderAction.BUY, quantity=1, order_type=OrderType.LIMIT)


def test_v04_stop_order_no_price_raises():
    with pytest.raises(ValueError):
        OrderRequest(symbol="AAPL", action=OrderAction.BUY, quantity=1, order_type=OrderType.STOP)


def test_v05_stop_limit_missing_prices_raises():
    with pytest.raises(ValueError):
        OrderRequest(
            symbol="AAPL", action=OrderAction.BUY, quantity=1, order_type=OrderType.STOP_LIMIT
        )


def test_v06_lowercase_symbol_uppercased():
    r = OrderRequest(symbol="aapl", action=OrderAction.BUY, quantity=1)
    assert r.symbol == "AAPL"


def test_v07_symbol_whitespace_stripped():
    r = OrderRequest(symbol="  AAPL  ", action=OrderAction.BUY, quantity=1)
    assert r.symbol == "AAPL"


def test_v08_limit_price_zero_raises():
    with pytest.raises(ValueError):
        OrderRequest(
            symbol="AAPL",
            action=OrderAction.BUY,
            quantity=1,
            order_type=OrderType.LIMIT,
            limit_price=0,
        )


def test_v09_limit_price_negative_raises():
    with pytest.raises(ValueError):
        OrderRequest(
            symbol="AAPL",
            action=OrderAction.BUY,
            quantity=1,
            order_type=OrderType.LIMIT,
            limit_price=-10,
        )
