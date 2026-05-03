"""Market-hours tests — require live fills. Run manually: pytest -m market

These tests place real orders on the paper account and wait for fills.
Only run during US market hours (9:30 AM – 4:00 PM ET).
"""

import pytest

from models.order import OrderAction, OrderRequest, TimeInForce

pytestmark = [
    pytest.mark.market,
    pytest.mark.skipif(True, reason="manual only — run with pytest -m market"),
]


@pytest.fixture(scope="module")
def market_client():
    from broker.ibkr_client import IBKRClient
    from broker.order_manager import OrderManager

    client = IBKRClient()
    client.connect()
    om = OrderManager(client)
    om.cancel_all()
    client.ib.sleep(1)
    yield client, om
    try:
        om.cancel_all()
        client.ib.sleep(1)
        client.disconnect()
    except Exception:
        pass


def test_p01_market_buy_fills(market_client):
    c, o = market_client
    fills = []
    o.on_fill(lambda r: fills.append(r))
    r = OrderRequest(symbol="AAPL", action=OrderAction.BUY, quantity=1, tif=TimeInForce.GTC)
    result = o.place_order(r)
    assert result.order_id > 0
    for _ in range(20):
        c.ib.sleep(0.5)
        if fills:
            break
    assert len(fills) > 0
    assert fills[0].filled == 1.0
    assert fills[0].avg_fill_price > 0


def test_p12_on_fill_callback_correct_result(market_client):
    c, o = market_client
    fills = []
    o.on_fill(lambda r: fills.append(r))
    r = OrderRequest(symbol="MSFT", action=OrderAction.BUY, quantity=1, tif=TimeInForce.GTC)
    o.place_order(r)
    for _ in range(20):
        c.ib.sleep(0.5)
        if fills:
            break
    assert len(fills) > 0
    fill = fills[-1]
    assert fill.symbol == "MSFT"
    assert fill.action == "BUY"
    assert fill.quantity == 1.0
    assert fill.avg_fill_price > 0


def test_pos02_position_appears_after_fill(market_client):
    c, o = market_client
    c.ib.sleep(1)
    positions = o.get_positions()
    symbols = [p.symbol for p in positions]
    assert "AAPL" in symbols or "MSFT" in symbols
    for p in positions:
        if p.symbol in ("AAPL", "MSFT"):
            assert p.quantity > 0
            assert p.avg_cost > 0


def test_s06_fill_event_fires_without_polling(market_client):
    c, o = market_client
    fills = []
    o.on_fill(lambda r: fills.append(r))
    r = OrderRequest(symbol="IBM", action=OrderAction.BUY, quantity=1, tif=TimeInForce.GTC)
    o.place_order(r)
    c.ib.sleep(10)
    assert len(fills) > 0


def test_p02_market_sell_reduces_position(market_client):
    c, o = market_client
    positions_before = {p.symbol: p.quantity for p in o.get_positions()}
    assert "AAPL" in positions_before, "No AAPL position — test_p01 may have failed"
    fills = []
    o.on_fill(lambda r: fills.append(r))
    r = OrderRequest(symbol="AAPL", action=OrderAction.SELL, quantity=1, tif=TimeInForce.GTC)
    o.place_order(r)
    for _ in range(20):
        c.ib.sleep(0.5)
        if fills:
            break
    assert len(fills) > 0
    assert fills[-1].action == "SELL"
    assert fills[-1].filled == 1.0
