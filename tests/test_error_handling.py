"""Section 9: Error handling tests — requires IBKR TWS."""

import math
import os

import pytest

from models.order import OrderAction, OrderRequest, OrderType, TimeInForce

IS_CI = bool(os.getenv("GITHUB_ACTIONS"))
pytestmark = pytest.mark.skipif(IS_CI, reason="requires IBKR TWS connection")


def test_e01_error_code_202_not_fired_to_on_error(live_client):
    c, o = live_client
    o._clear_callbacks()
    errors = []
    o.on_error(lambda rid, code, msg: errors.append(code))
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
    o.cancel_order(result.order_id)
    c.ib.sleep(1)
    assert 202 not in errors


def test_e04_ten_rapid_orders_no_cache_corruption(live_client):
    c, o = live_client
    price = c.get_market_price("GE")
    order_ids = []
    for i in range(10):
        r = OrderRequest(
            symbol="GE",
            action=OrderAction.BUY,
            quantity=1,
            order_type=OrderType.LIMIT,
            limit_price=round(price * 0.5 - i * 0.01, 2),
            tif=TimeInForce.GTC,
        )
        result = o.place_order(r, allow_duplicate=True)
        order_ids.append(result.order_id)
    assert len(set(order_ids)) == 10
    o.cancel_all("GE")
    c.ib.sleep(1)


def test_e05_market_price_never_nan(live_client):
    c, _ = live_client
    price = c.get_market_price("MSFT")
    assert not math.isnan(price)
    limit = round(price * 0.9, 2)
    assert not math.isnan(limit)
    assert limit > 0


def test_e08_logs_directory_created():
    from pathlib import Path
    from config import logging_config

    log_dir = Path(logging_config.__file__).parent.parent / "logs"
    if not log_dir.exists():
        from config.logging_config import setup_logging

        setup_logging()
        assert log_dir.exists()
