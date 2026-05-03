"""Sections 7-8: Sync and position tests — requires IBKR TWS."""

import os

import pytest

from models.order import OrderAction, OrderRequest, OrderType, TimeInForce

IS_CI = bool(os.getenv("GITHUB_ACTIONS"))
pytestmark = pytest.mark.skipif(IS_CI, reason="requires IBKR TWS connection")


def test_s03_sync_returns_open_order_count(live_client):
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
    count = o.sync()
    assert count >= 1
    o.cancel_all()
    c.ib.sleep(0.5)


def test_s05_two_clients_different_ids_no_interference(live_client):
    from broker.ibkr_client import IBKRClient

    client, _ = live_client
    c2 = IBKRClient(client_id=2)
    c2.connect()
    assert c2.is_connected
    assert c2.account == client.account
    c2.disconnect()


def test_pos01_get_positions_returns_list(live_client):
    _, o = live_client
    assert isinstance(o.get_positions(), list)
