"""
Tests for PingPongTest -- the test-only alternating BUY/SELL strategy.

No IBKR connection required: a fake client / order-manager / risk-manager /
reconnect stand in for the injected infrastructure.

Coverage (test_pp01 .. test_pp22):
  - tick places BUY when flat, SELL when in position, and alternates
  - tick is a no-op while an order is pending, market is closed, no price is
    available, the strategy is risk-halted, or it disabled itself
  - RiskViolationError / DuplicateOrderError are handled (no pending set)
  - on_fill maintains position state, stamps cost_basis on SELL, ignores
    non-FILLED results and fills for other symbols
  - on_start broker reconcile adopts an exactly-`qty` position, disables on
    any other holding (wrong size or short)
  - on_error / on_cancel clear the pending flag only for OUR order
  - the pending-timeout self-heal force-clears and re-reconciles
  - _is_market_open RTH/weekend logic
  - registry membership + lockstep + clean build via StrategyRunner
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from broker.order_manager import DuplicateOrderError
from models.order import OrderAction, OrderResult, OrderStatus
from models.order import Position
from risk.risk_manager import RiskViolationError
from strategies.test_pingpong import PingPongTest, _PENDING_TIMEOUT_SECONDS

_SYMBOL = "AAPL"


# ══════════════════════════════════════════════════════════════════════════════
# Fakes
# ══════════════════════════════════════════════════════════════════════════════


class _FakeClient:
    """Returns a fixed price, or raises a pre-set exception."""

    def __init__(self, price=250.0):
        self._price = price  # float, or an Exception instance to raise

    def get_market_price(self, symbol, **kwargs):
        if isinstance(self._price, Exception):
            raise self._price
        return self._price


class _FakeOM:
    """Order manager stub: records placed orders, fires fill/error/cancel events."""

    def __init__(self, positions=None):
        self._on_fill = []
        self._on_error = []
        self._on_cancel = []
        self._positions = list(positions or [])
        self.placed: list = []  # OrderRequest objects passed to place_order
        self.place_raises = None  # Exception to raise from place_order, or None
        self._next_order_id = 1000

    def on_fill(self, cb):
        self._on_fill.append(cb)

    def on_error(self, cb):
        self._on_error.append(cb)

    def on_cancel(self, cb):
        self._on_cancel.append(cb)

    def get_positions(self):
        return list(self._positions)

    def place_order(self, request, allow_duplicate=False):
        if self.place_raises is not None:
            raise self.place_raises
        self.placed.append(request)
        oid = self._next_order_id
        self._next_order_id += 1
        return OrderResult(
            order_id=oid,
            symbol=request.symbol,
            action=request.action.value,
            quantity=request.quantity,
            order_type=request.order_type.value,
            tif=request.tif.value,
            status=OrderStatus.SUBMITTED,
            filled=0,
            remaining=request.quantity,
            avg_fill_price=None,
            limit_price=None,
            stop_price=None,
            strategy_name=request.strategy_name,
        )

    # -- event firing helpers (simulate broker callbacks) --
    def fire_fill(self, result):
        for cb in list(self._on_fill):
            cb(result)

    def fire_error(self, req_id, code, msg):
        for cb in list(self._on_error):
            cb(req_id, code, msg)

    def fire_cancel(self, result):
        for cb in list(self._on_cancel):
            cb(result)


class _FakeRisk:
    def __init__(self, halted=False, check_raises=None):
        self._halted = halted
        self.check_raises = check_raises

    def is_halted(self):
        return self._halted

    def check(self, request, current_price):
        if self.check_raises is not None:
            raise self.check_raises


class _FakeReconnect:
    def __init__(self, connected=True):
        self._connected = connected

    def wait_for_connection(self, timeout=60):
        return self._connected


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════


def _position(symbol=_SYMBOL, qty=1, avg_cost=240.0):
    return Position(
        symbol=symbol,
        quantity=float(qty),
        avg_cost=float(avg_cost),
        market_price=None,
        market_value=None,
        unrealized_pnl=None,
        realized_pnl=None,
        account="DU0000000",
    )


def _filled(action, symbol=_SYMBOL, qty=1, price=250.0, order_id=1000, strategy_name=None):
    return OrderResult(
        order_id=order_id,
        symbol=symbol,
        action=action.value if isinstance(action, OrderAction) else action,
        quantity=qty,
        order_type="MKT",
        tif="DAY",
        status=OrderStatus.FILLED,
        filled=qty,
        remaining=0,
        avg_fill_price=price,
        limit_price=None,
        stop_price=None,
        strategy_name=strategy_name,
    )


def _make_strategy(
    *,
    price=250.0,
    halted=False,
    connected=True,
    positions=None,
    check_raises=None,
    place_raises=None,
    market_open=True,
    qty=1,
):
    """
    Build a PingPongTest wired to fakes.

    market_open: True/False sets a fixed _is_market_open override; None leaves
    the real method intact (for the _is_market_open unit tests).
    """
    client = _FakeClient(price=price)
    om = _FakeOM(positions=positions)
    om.place_raises = place_raises
    risk = _FakeRisk(halted=halted, check_raises=check_raises)
    reconnect = _FakeReconnect(connected=connected)
    strat = PingPongTest(
        client=client,
        order_manager=om,
        risk_manager=risk,
        reconnect=reconnect,
        feed=None,
        symbol=_SYMBOL,
        qty=qty,
    )
    if market_open is not None:
        strat._is_market_open = lambda: market_open  # type: ignore[method-assign]
    return strat, client, om, risk, reconnect


# ══════════════════════════════════════════════════════════════════════════════
# test_pp01 .. — tick order placement
# ══════════════════════════════════════════════════════════════════════════════


def test_pp01_flat_tick_places_buy():
    strat, _, om, _, _ = _make_strategy()
    strat.on_tick()
    assert len(om.placed) == 1
    req = om.placed[0]
    assert req.action == OrderAction.BUY
    assert req.quantity == 1
    assert req.tif.value == "DAY"
    assert req.order_type.value == "MKT"
    assert strat._order_pending is True
    assert strat._pending_order_id == 1000


def test_pp02_in_position_tick_places_sell():
    strat, _, om, _, _ = _make_strategy()
    strat._in_position = True
    strat._position_shares = 1
    strat.on_tick()
    assert len(om.placed) == 1
    assert om.placed[0].action == OrderAction.SELL
    assert om.placed[0].quantity == 1


def test_pp03_alternates_buy_sell_buy_across_fills():
    strat, _, om, _, _ = _make_strategy()

    strat.on_tick()  # BUY queued
    assert om.placed[-1].action == OrderAction.BUY
    om.fire_fill(_filled(OrderAction.BUY, order_id=1000))
    assert strat._in_position is True
    assert strat._order_pending is False

    strat.on_tick()  # SELL queued
    assert om.placed[-1].action == OrderAction.SELL
    om.fire_fill(_filled(OrderAction.SELL, order_id=1001))
    assert strat._in_position is False
    assert strat._order_pending is False

    strat.on_tick()  # BUY again
    assert om.placed[-1].action == OrderAction.BUY
    assert len(om.placed) == 3


def test_pp04_pending_order_blocks_next_tick():
    strat, _, om, _, _ = _make_strategy()
    strat.on_tick()
    assert len(om.placed) == 1
    strat.on_tick()  # still pending — must not place a second order
    assert len(om.placed) == 1


def test_pp05_market_closed_skips_tick():
    strat, _, om, _, _ = _make_strategy(market_open=False)
    strat.on_tick()
    assert om.placed == []
    assert strat._order_pending is False


def test_pp06_no_price_skips_tick():
    strat, _, om, _, _ = _make_strategy(price=ValueError("no data"))
    strat.on_tick()
    assert om.placed == []
    assert strat._order_pending is False


def test_pp06b_price_runtime_error_skips_tick():
    strat, _, om, _, _ = _make_strategy(price=RuntimeError("contract not qualified"))
    strat.on_tick()
    assert om.placed == []
    assert strat._order_pending is False


def test_pp07_risk_halted_skips_tick():
    strat, _, om, _, _ = _make_strategy(halted=True)
    strat.on_tick()
    assert om.placed == []
    assert strat._order_pending is False


def test_pp08_risk_violation_handled_no_pending():
    strat, _, om, _, _ = _make_strategy(check_raises=RiskViolationError("blocked"))
    strat.on_tick()
    assert om.placed == []
    assert strat._order_pending is False  # order never placed — pending stays clear


def test_pp08b_duplicate_order_handled_no_pending():
    strat, _, om, _, _ = _make_strategy(place_raises=DuplicateOrderError("dup"))
    strat.on_tick()
    assert om.placed == []
    assert strat._order_pending is False


def test_pp08c_disconnected_skips_tick():
    strat, _, om, _, _ = _make_strategy(connected=False)
    strat.on_tick()
    assert om.placed == []


# ══════════════════════════════════════════════════════════════════════════════
# on_fill behaviour
# ══════════════════════════════════════════════════════════════════════════════


def test_pp09_on_fill_buy_sets_position_state():
    strat, _, om, _, _ = _make_strategy()
    strat._order_pending = True
    strat._pending_order_id = 1000
    om.fire_fill(_filled(OrderAction.BUY, qty=1, price=251.5, order_id=1000))
    assert strat._in_position is True
    assert strat._position_shares == 1
    assert strat._entry_price == 251.5
    assert strat._order_pending is False
    assert strat._pending_order_id is None


def test_pp10_on_fill_sell_stamps_cost_basis_and_resets():
    strat, _, om, _, _ = _make_strategy()
    strat._in_position = True
    strat._position_shares = 1
    strat._entry_price = 240.0
    sell = _filled(OrderAction.SELL, qty=1, price=255.0, order_id=1001)
    om.fire_fill(sell)
    assert sell.cost_basis == 240.0  # stamped for the dashboard's realized P&L
    assert strat._in_position is False
    assert strat._position_shares == 0
    assert strat._entry_price == 0.0


def test_pp10b_on_fill_sell_without_entry_price_leaves_cost_basis_none():
    strat, _, om, _, _ = _make_strategy()
    strat._in_position = True
    strat._position_shares = 1
    strat._entry_price = 0.0  # adopted position with unknown avg_cost
    sell = _filled(OrderAction.SELL, qty=1, price=255.0, order_id=1001)
    om.fire_fill(sell)
    assert sell.cost_basis is None
    assert strat._in_position is False


def test_pp11_on_fill_ignores_non_filled_result():
    strat, _, om, _, _ = _make_strategy()
    pending = _filled(OrderAction.BUY, order_id=1000)
    pending.status = OrderStatus.SUBMITTED
    om.fire_fill(pending)
    assert strat._in_position is False
    assert strat._position_shares == 0


def test_pp12_on_fill_ignores_other_symbol():
    strat, _, om, _, _ = _make_strategy()
    om.fire_fill(_filled(OrderAction.BUY, symbol="MSFT", order_id=1000))
    assert strat._in_position is False


# ══════════════════════════════════════════════════════════════════════════════
# on_start broker reconcile
# ══════════════════════════════════════════════════════════════════════════════


def test_pp13_reconcile_adopts_exact_qty_position():
    strat, _, om, _, _ = _make_strategy(positions=[_position(qty=1, avg_cost=240.0)])
    strat.on_start()
    assert strat._in_position is True
    assert strat._position_shares == 1
    assert strat._entry_price == 240.0
    assert strat._disabled is False


def test_pp13b_reconcile_flat_when_no_position():
    strat, _, om, _, _ = _make_strategy(positions=[])
    strat.on_start()
    assert strat._in_position is False
    assert strat._disabled is False


def test_pp14_reconcile_disables_on_unexpected_quantity():
    strat, _, om, _, _ = _make_strategy(positions=[_position(qty=5, avg_cost=240.0)])
    strat.on_start()
    assert strat._disabled is True
    # disabled => tick is a complete no-op
    strat._is_market_open = lambda: True  # type: ignore[method-assign]
    strat.on_tick()
    assert om.placed == []


def test_pp15_reconcile_disables_on_short_position():
    strat, _, om, _, _ = _make_strategy(positions=[_position(qty=-1, avg_cost=240.0)])
    strat.on_start()
    assert strat._disabled is True


def test_pp15b_reconcile_survives_get_positions_error():
    strat, _, om, _, _ = _make_strategy()

    def _boom():
        raise ConnectionError("not connected")

    om.get_positions = _boom  # type: ignore[method-assign]
    strat.on_start()  # must not raise
    assert strat._in_position is False
    assert strat._disabled is False


# ══════════════════════════════════════════════════════════════════════════════
# pending-flag clearing: on_error / on_cancel / timeout
# ══════════════════════════════════════════════════════════════════════════════


def test_pp16_on_error_clears_pending_for_our_order_only():
    strat, _, om, _, _ = _make_strategy()
    strat.on_start()  # wires on_error
    strat.on_tick()  # places order 1000, sets pending
    assert strat._order_pending is True

    om.fire_error(9999, 201, "someone else's order")  # different id
    assert strat._order_pending is True

    om.fire_error(1000, 201, "order rejected")  # our id
    assert strat._order_pending is False


def test_pp17_on_cancel_clears_pending_for_our_order():
    strat, _, om, _, _ = _make_strategy()
    strat.on_start()
    strat.on_tick()
    assert strat._order_pending is True
    cancelled = _filled(OrderAction.BUY, order_id=1000)
    cancelled.status = OrderStatus.CANCELLED
    om.fire_cancel(cancelled)
    assert strat._order_pending is False


def test_pp18_pending_timeout_when_flat_clears_then_next_tick_replaces():
    strat, _, om, _, _ = _make_strategy()  # broker flat
    strat.on_tick()  # places order 1000
    assert strat._order_pending is True
    first_id = strat._pending_order_id

    # Simulate the order having been pending well past the timeout with no
    # fill/cancel/error event ever arriving.
    strat._pending_since = datetime.now(timezone.utc) - timedelta(
        seconds=_PENDING_TIMEOUT_SECONDS + 5
    )
    strat.on_tick()
    # Timeout tick force-clears + re-reconciles, but the broker shows flat --
    # the stuck order may have filled without the snapshot catching up, so it
    # does NOT place again this tick (post-impl CR H1).
    assert len(om.placed) == 1
    assert strat._order_pending is False
    assert strat._in_position is False

    strat.on_tick()  # next tick places from settled truth
    assert len(om.placed) == 2
    assert strat._order_pending is True
    assert strat._pending_order_id != first_id


def test_pp18b_pending_within_timeout_still_blocks():
    strat, _, om, _, _ = _make_strategy()
    strat.on_tick()
    strat._pending_since = datetime.now(timezone.utc) - timedelta(
        seconds=_PENDING_TIMEOUT_SECONDS - 10
    )
    strat.on_tick()
    assert len(om.placed) == 1  # still within timeout — no second order


def test_pp18c_pending_timeout_with_held_position_places_sell():
    # If the timeout reconcile positively confirms a held position, the tick
    # DOES fall through and place a clean SELL (post-impl CR H1: fall through
    # only on positively-adopted state, not on a flat snapshot).
    strat, _, om, _, _ = _make_strategy(positions=[_position(qty=1, avg_cost=240.0)])
    strat.on_tick()  # places BUY order 1000
    strat._pending_since = datetime.now(timezone.utc) - timedelta(
        seconds=_PENDING_TIMEOUT_SECONDS + 5
    )
    strat.on_tick()
    assert len(om.placed) == 2
    assert om.placed[-1].action == OrderAction.SELL
    assert strat._in_position is True
    assert strat._order_pending is True


# ══════════════════════════════════════════════════════════════════════════════
# _is_market_open
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "when,expected",
    [
        (datetime(2026, 5, 13, 14, 0), True),  # Wednesday 14:00 ET — open
        (datetime(2026, 5, 13, 9, 30), True),  # exactly 09:30 — open
        (datetime(2026, 5, 13, 9, 29), False),  # 09:29 — pre-open
        (datetime(2026, 5, 13, 16, 0), False),  # 16:00 — closed (half-open interval)
        (datetime(2026, 5, 16, 12, 0), False),  # Saturday — closed
        (datetime(2026, 5, 17, 12, 0), False),  # Sunday — closed
    ],
)
def test_pp19_is_market_open(monkeypatch, when, expected):
    strat, _, _, _, _ = _make_strategy(market_open=None)  # keep the real method
    import strategies.test_pingpong as mod

    class _FakeDT:
        @staticmethod
        def now(tz=None):
            return when.replace(tzinfo=tz)

    monkeypatch.setattr(mod, "datetime", _FakeDT)
    assert strat._is_market_open() is expected


# ══════════════════════════════════════════════════════════════════════════════
# registry wiring
# ══════════════════════════════════════════════════════════════════════════════


def test_pp20_registered_in_registry_with_unique_symbol():
    from config.strategies import REGISTRY, _STRATEGY_CLASSES

    by_name = {cfg.name: cfg for cfg in REGISTRY}
    assert "PingPongTest-AAPL" in by_name
    cfg = by_name["PingPongTest-AAPL"]
    assert cfg.symbol == "AAPL"
    assert cfg.strategy_class is PingPongTest
    assert _STRATEGY_CLASSES["PingPongTest-AAPL"] is PingPongTest
    # MS-D guard: symbol is not shared with the real strategies.
    symbols = [c.symbol.upper() for c in REGISTRY]
    assert symbols.count("AAPL") == 1


def test_pp21_real_registry_builds_pingpong_cleanly():
    """StrategyRunner.build() constructs PingPongTest without signature drift."""
    from unittest.mock import MagicMock

    from config.strategies import REGISTRY
    from runtime.strategy_runner import StrategyRunner

    runner = StrategyRunner(
        client=MagicMock(),
        order_manager=_FakeOM(),
        reconnect=MagicMock(),
        feed=MagicMock(),
        trade_log=MagicMock(),
        registry=list(REGISTRY),
    )
    runner.build()
    handle = next(h for h in runner.handles if h.config.name == "PingPongTest-AAPL")
    assert isinstance(handle.strategy, PingPongTest)
    assert handle.strategy._qty == 1
    assert handle.strategy.params == {"symbol": "AAPL", "qty": 1}


def test_pp22_dispatch_on_fill_filters_by_strategy_name():
    """A fill tagged for another strategy must not reach PingPong's on_fill."""
    strat, _, om, _, _ = _make_strategy()
    strat._strategy_name = "PingPongTest-AAPL"  # set by StrategyRunner in live mode

    om.fire_fill(_filled(OrderAction.BUY, order_id=1, strategy_name="RSI2MR-SPY"))
    assert strat._in_position is False  # filtered out

    om.fire_fill(_filled(OrderAction.BUY, order_id=2, strategy_name="PingPongTest-AAPL"))
    assert strat._in_position is True  # ours — delivered


def test_pp24_fast_fill_during_place_order_does_not_resurrect_pending():
    """Race regression: a fill that arrives during place_order's internal
    sleep(0.5) fires on_fill on the IB event-loop thread BEFORE place_order
    returns. on_fill calls _clear_pending(). The old code then unconditionally
    re-set _order_pending=True with the now-terminal order_id, locking the
    strategy out for one 90s-timeout cycle (and, combined with the strategy_name
    race in OrderManager, indefinitely).
    """
    strat, _, om, _, _ = _make_strategy()
    strat._strategy_name = "PingPongTest-AAPL"  # multi-strategy dispatch path

    # Replace place_order with one that synchronously fires on_fill before
    # returning -- exactly what _client.sleep(0.5) lets ib_insync do for a
    # fast-filling MKT order on a liquid symbol.
    original_place = om.place_order

    def _place_with_synchronous_fill(request, allow_duplicate=False):
        result = original_place(request, allow_duplicate=allow_duplicate)
        # Fire fill BEFORE returning -- the race window.
        om.fire_fill(
            _filled(
                OrderAction.BUY if request.action == OrderAction.BUY else OrderAction.SELL,
                qty=request.quantity,
                price=302.59,
                order_id=result.order_id,
                strategy_name="PingPongTest-AAPL",
            )
        )
        return result

    om.place_order = _place_with_synchronous_fill  # type: ignore[method-assign]
    strat.on_tick()
    assert len(om.placed) == 1
    assert strat._in_position is True
    assert strat._position_shares == 1
    assert strat._entry_price == 302.59
    # The critical assertion: pending must NOT be resurrected after on_fill cleared it.
    assert strat._order_pending is False
    assert strat._pending_order_id is None
    assert strat._pending_since is None


def test_pp25_fast_fill_on_sell_leaves_strategy_flat_and_unblocked():
    """Same race, but starting in_position so the tick places a SELL. After
    the synchronous SELL fill, _in_position should be False AND _order_pending
    should be False -- the next tick must be free to place a new BUY.
    """
    strat, _, om, _, _ = _make_strategy()
    strat._strategy_name = "PingPongTest-AAPL"
    strat._in_position = True
    strat._position_shares = 1
    strat._entry_price = 300.0

    original_place = om.place_order

    def _place_with_synchronous_fill(request, allow_duplicate=False):
        result = original_place(request, allow_duplicate=allow_duplicate)
        om.fire_fill(
            _filled(
                OrderAction.SELL,
                qty=request.quantity,
                price=305.0,
                order_id=result.order_id,
                strategy_name="PingPongTest-AAPL",
            )
        )
        return result

    om.place_order = _place_with_synchronous_fill  # type: ignore[method-assign]
    strat.on_tick()
    assert om.placed[0].action == OrderAction.SELL
    assert strat._in_position is False
    assert strat._position_shares == 0
    assert strat._order_pending is False


def test_pp23_on_tick_from_daemon_thread_queues_order():
    """Regression tripwire: PingPong tick from a daemon thread must place an order.

    The 2026-05-15 production bug was: PingPong on_tick runs on the scheduler's
    daemon thread, called client.get_market_price, which internally hit the
    ib_insync sync wrapper from the wrong thread and crashed silently. Zero
    fills since deploy. This test pins the invariant: a tick from a non-main
    thread must complete and queue an order (using the test's MockClient that
    bypasses the real ib_insync path).
    """
    import threading as _threading

    strat, _, om, _, _ = _make_strategy()

    holder: dict = {}

    def _runner() -> None:
        try:
            strat.on_tick()
            holder["ok"] = True
        except BaseException as exc:  # noqa: BLE001
            holder["error"] = exc

    t = _threading.Thread(target=_runner, name="pp23-daemon", daemon=True)
    t.start()
    t.join(timeout=5.0)
    assert not t.is_alive()
    assert "error" not in holder, holder.get("error")
    assert holder.get("ok") is True
    assert len(om.placed) == 1
    assert om.placed[0].action == OrderAction.BUY
    assert strat._order_pending is True
