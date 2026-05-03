"""Fill reconciliation tests — no IBKR connection needed.

Tests cover:
  FR01  no fills → 0 replayed, no callback
  FR02  one unseen fill → callback fires, OrderResult fields correct
  FR03  same fill reconciled twice → callback fires only once (dedup)
  FR04  live fill tracked via _handle_exec_details → reconcile skips it
  FR05  ib.fills() raises → reconcile returns 0, no crash
  FR06  callback raises → remaining callbacks still fire, count correct
  FR07  SLD fill maps to action="SELL"
  FR08  ReconnectManager calls reconcile_fills after successful reconnect
"""

from unittest.mock import MagicMock

from broker.order_manager import OrderManager
from models.order import OrderStatus

# ── helpers ───────────────────────────────────────────────────────────────────


class _Event:
    """Minimal ib_insync Event stub — accepts += without doing anything."""

    def __iadd__(self, handler):
        return self


def _make_fake_fill(
    exec_id: str,
    order_id: int = 100,
    symbol: str = "QQQ",
    side: str = "BOT",
    shares: float = 10.0,
    avg_price: float = 450.0,
):
    execution = MagicMock()
    execution.execId = exec_id
    execution.orderId = order_id
    execution.side = side
    execution.shares = shares
    execution.avgPrice = avg_price

    contract = MagicMock()
    contract.symbol = symbol

    fill = MagicMock()
    fill.execution = execution
    fill.contract = contract
    return fill


def _make_om(fills=None):
    """Return (OrderManager, fake_ib) with ib.fills() pre-loaded."""
    fills = fills or []

    ib = MagicMock()
    ib.orderStatusEvent = _Event()
    ib.openOrderEvent = _Event()
    ib.newOrderEvent = _Event()
    ib.cancelOrderEvent = _Event()
    ib.errorEvent = _Event()
    ib.execDetailsEvent = _Event()
    ib.fills.return_value = fills
    ib.reqAllOpenOrders.return_value = None
    ib.sleep.return_value = None
    ib.openTrades.return_value = []

    client = MagicMock()
    client.ib = ib
    client.is_connected = True

    return OrderManager(client), ib


# ── tests ─────────────────────────────────────────────────────────────────────


def test_fr01_no_fills_no_callbacks():
    om, _ = _make_om(fills=[])
    fired = []
    om.on_fill(fired.append)
    assert om.reconcile_fills() == 0
    assert fired == []


def test_fr02_missed_fill_fires_callback_with_correct_fields():
    fill = _make_fake_fill(
        "EXEC001", order_id=42, symbol="QQQ", side="BOT", shares=10, avg_price=450.0
    )
    om, _ = _make_om(fills=[fill])
    results = []
    om.on_fill(results.append)

    count = om.reconcile_fills()

    assert count == 1
    assert len(results) == 1
    r = results[0]
    assert r.symbol == "QQQ"
    assert r.action == "BUY"
    assert r.filled == 10.0
    assert r.avg_fill_price == 450.0
    assert r.status == OrderStatus.FILLED
    assert r.remaining == 0.0


def test_fr03_duplicate_reconcile_fires_callback_once():
    fill = _make_fake_fill("EXEC002")
    om, ib = _make_om(fills=[fill])
    results = []
    om.on_fill(results.append)

    om.reconcile_fills()
    om.reconcile_fills()  # second call with same fill still in ib.fills()

    assert len(results) == 1


def test_fr04_live_fill_tracked_via_exec_details_not_replayed():
    fill = _make_fake_fill("EXEC003")
    om, _ = _make_om(fills=[fill])
    results = []
    om.on_fill(results.append)

    # Simulate live execDetailsEvent arriving before reconcile
    om._handle_exec_details(MagicMock(), fill)

    count = om.reconcile_fills()

    assert count == 0
    assert results == []


def test_fr05_ib_fills_raises_returns_zero():
    om, ib = _make_om()
    ib.fills.side_effect = RuntimeError("socket closed")
    fired = []
    om.on_fill(fired.append)

    count = om.reconcile_fills()

    assert count == 0
    assert fired == []


def test_fr06_callback_exception_does_not_stop_remaining_callbacks():
    fill = _make_fake_fill("EXEC004")
    om, _ = _make_om(fills=[fill])

    good = []

    def bad_cb(r):
        raise ValueError("boom")

    om.on_fill(bad_cb)
    om.on_fill(good.append)

    count = om.reconcile_fills()

    assert count == 1
    assert len(good) == 1


def test_fr07_sell_fill_maps_to_sell_action():
    fill = _make_fake_fill("EXEC005", side="SLD")
    om, _ = _make_om(fills=[fill])
    results = []
    om.on_fill(results.append)

    om.reconcile_fills()

    assert results[0].action == "SELL"


def test_fr08_reconnect_manager_calls_reconcile_fills_after_reconnect():
    from broker.reconnect import ReconnectManager

    client = MagicMock()
    client.connect.return_value = None

    om = MagicMock()
    om.sync.return_value = 0
    om.reconcile_fills.return_value = 0

    rcn = ReconnectManager(client=client, order_manager=om, max_attempts=1)
    rcn._attempt_reconnect()

    om.reconcile_fills.assert_called_once()
