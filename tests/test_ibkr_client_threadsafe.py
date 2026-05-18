"""IBKRClient thread-safety regression tests.

These exercise the auto-routing behavior of qualify_contract, get_market_price,
get_account_summary, get_positions, is_alive, and sleep. The bug being prevented:
ib_insync's sync wrappers call loop.run_until_complete() internally. When the
main asyncio loop is already running on the main thread, any sync wrapper
invoked from a non-main thread (i.e. every strategy's on_tick) raises
"This event loop is already running" or "no current event loop in thread X",
and PingPong's broad except swallows it -- zero fills.

Test grid:
- TS-01..03 qualify_contract: main path uses sync; daemon path uses Async via
  run_coroutine_threadsafe; empty result -> RuntimeError.
- TS-04..06 get_market_price: main path uses existing sync body; daemon path
  routes the entire poll loop onto the main loop; timeout -> ValueError.
- TS-07 grep tripwire: no callers outside broker/ibkr_client.py reach for
  ib.sleep / ib.qualifyContracts / ib.reqCurrentTime directly. Regression
  shield against the exact omission that hid this bug.
- TS-08..10 sleep helper, is_alive routing, cold-start raise.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from broker.ibkr_client import IBKRClient

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _make_client(ib_mock, main_loop=None):
    """Construct an IBKRClient bypassing __init__ network side-effects."""
    client = IBKRClient.__new__(IBKRClient)
    client._host = "127.0.0.1"
    client._port = 7497
    client._client_id = 1
    client.ib = ib_mock
    client._on_disconnect_cb = None
    client._main_loop = main_loop
    return client


def _run_on_thread(target, *args, **kwargs):
    """Run target on a non-main thread, propagate its return / exception."""
    holder: dict = {}

    def _runner() -> None:
        try:
            holder["value"] = target(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - propagate to main thread
            holder["error"] = exc

    t = threading.Thread(target=_runner, name="ts-test-worker")
    t.start()
    t.join(timeout=10.0)
    assert not t.is_alive(), "worker thread did not finish"
    if "error" in holder:
        raise holder["error"]
    return holder.get("value")


# ──────────────────────────────────────────────────────────────────────────────
# TS-01..03 — qualify_contract
# ──────────────────────────────────────────────────────────────────────────────


def test_ts01_qualify_contract_main_thread_uses_sync():
    """Main-thread caller uses the sync ib.qualifyContracts wrapper."""
    ib = MagicMock()
    ib.qualifyContracts.return_value = [SimpleNamespace(primaryExchange="NASDAQ")]
    ib.qualifyContractsAsync = AsyncMock(return_value=[])
    client = _make_client(ib)  # no _main_loop -> main path stays sync

    result = client.qualify_contract(SimpleNamespace())

    ib.qualifyContracts.assert_called_once()
    ib.qualifyContractsAsync.assert_not_called()
    assert result.primaryExchange == "NASDAQ"


def test_ts02_qualify_contract_daemon_thread_routes_async(bg_event_loop):
    """Non-main caller with a running main loop routes via qualifyContractsAsync."""
    ib = MagicMock()
    ib.qualifyContracts.return_value = []  # sync path would fail
    ib.qualifyContractsAsync = AsyncMock(return_value=[SimpleNamespace(primaryExchange="NASDAQ")])
    client = _make_client(ib, main_loop=bg_event_loop)

    result = _run_on_thread(client.qualify_contract, SimpleNamespace())

    ib.qualifyContractsAsync.assert_awaited_once()
    ib.qualifyContracts.assert_not_called()
    assert result.primaryExchange == "NASDAQ"


def test_ts03_qualify_contract_daemon_empty_raises(bg_event_loop):
    """Non-main caller: empty qualifyContractsAsync result -> RuntimeError."""
    ib = MagicMock()
    ib.qualifyContractsAsync = AsyncMock(return_value=[])
    client = _make_client(ib, main_loop=bg_event_loop)

    with pytest.raises(RuntimeError, match="Could not qualify contract"):
        _run_on_thread(client.qualify_contract, SimpleNamespace())


# ──────────────────────────────────────────────────────────────────────────────
# TS-04..06 — get_market_price
# ──────────────────────────────────────────────────────────────────────────────


def _make_ticker(last=None, close=None, bid=None, ask=None):
    return SimpleNamespace(last=last, close=close, bid=bid, ask=ask)


def test_ts04_get_market_price_main_thread_uses_sync_body():
    """Main-thread caller uses the existing ib.sleep / sync body unchanged."""
    ib = MagicMock()
    ib.qualifyContracts.return_value = [SimpleNamespace(primaryExchange="NASDAQ")]
    ib.qualifyContractsAsync = AsyncMock()
    ib.reqMktData.return_value = _make_ticker(last=212.5)
    ib.sleep.return_value = None
    ib.cancelMktData.return_value = None
    client = _make_client(ib)

    price = client.get_market_price("AAPL", is_delayed=False)

    assert price == 212.5
    ib.qualifyContracts.assert_called_once()
    ib.qualifyContractsAsync.assert_not_called()
    ib.reqMktData.assert_called_once()
    ib.cancelMktData.assert_called_once()


def test_ts05_get_market_price_daemon_routes_async(bg_event_loop):
    """Non-main caller routes the whole price-poll coroutine on the main loop."""
    ib = MagicMock()
    ib.qualifyContractsAsync = AsyncMock(return_value=[SimpleNamespace(primaryExchange="NASDAQ")])
    ib.qualifyContracts.return_value = []  # sync would fail
    ib.reqMktData.return_value = _make_ticker(last=212.5)
    ib.cancelMktData.return_value = None
    client = _make_client(ib, main_loop=bg_event_loop)

    price = _run_on_thread(client.get_market_price, "AAPL", "SMART", "USD", False)

    assert price == 212.5
    ib.qualifyContractsAsync.assert_awaited_once()
    ib.qualifyContracts.assert_not_called()
    ib.reqMktData.assert_called_once()
    ib.cancelMktData.assert_called_once()


def test_ts06_get_market_price_daemon_timeout_raises_value_error(monkeypatch, bg_event_loop):
    """If no valid price ever arrives, the routed coroutine raises ValueError."""
    monkeypatch.setattr("broker.ibkr_client._PRICE_TIMEOUT", 0.2)
    monkeypatch.setattr("broker.ibkr_client._PRICE_POLL", 0.05)

    ib = MagicMock()
    ib.qualifyContractsAsync = AsyncMock(return_value=[SimpleNamespace(primaryExchange="NASDAQ")])
    # Ticker has no valid fields -> _best_price returns None forever
    ib.reqMktData.return_value = _make_ticker()
    ib.cancelMktData.return_value = None
    client = _make_client(ib, main_loop=bg_event_loop)

    with pytest.raises(ValueError, match="Could not obtain a valid price"):
        _run_on_thread(client.get_market_price, "AAPL", "SMART", "USD", False)

    # Subscription was cancelled even though the poll failed
    ib.cancelMktData.assert_called_once()


# ──────────────────────────────────────────────────────────────────────────────
# TS-07 — grep tripwire (regression shield)
# ──────────────────────────────────────────────────────────────────────────────


def test_ts07_no_direct_ib_sync_calls_outside_client():
    """No file outside broker/ibkr_client.py may call the dangerous sync wrappers.

    This is the regression that hid the bug: OrderManager called self._ib.sleep(0.5)
    from a daemon thread (via strategy on_tick -> place_order) and the symptom
    only surfaced once a strategy actually started ticking.

    Allowlist: ibkr_client.py itself is the only place these may appear -- and
    only inside the main-thread branch of an auto-detect.
    """
    project_root = Path(__file__).resolve().parent.parent
    dangerous_pattern = re.compile(
        r"\.ib\.(sleep|qualifyContracts|reqCurrentTime|placeOrder|cancelOrder|reqMarketDataType)\("
    )
    # Match self._ib.sleep, self.ib.sleep, client.ib.sleep, etc.
    forbidden_paths = [
        project_root / "broker" / "order_manager.py",
        project_root / "broker" / "reconnect.py",
        project_root / "data" / "feed.py",
        project_root / "data" / "historical.py",
        project_root / "data" / "account_snapshot.py",
        project_root / "risk" / "risk_manager.py",
        project_root / "runtime" / "strategy_runner.py",
        project_root / "tests" / "conftest.py",
    ]
    # All strategies/
    for strat in (project_root / "strategies").glob("*.py"):
        forbidden_paths.append(strat)

    offenders = []
    for path in forbidden_paths:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if dangerous_pattern.search(line):
                offenders.append(f"{path.relative_to(project_root)}:{lineno}: {line.strip()}")

    assert not offenders, (
        "Direct ib_insync sync calls outside IBKRClient -- these are the same bug "
        "class that hid the PingPong zero-fills regression. Route via "
        "IBKRClient (qualify_contract / sleep / is_alive) instead. Offenders:\n  "
        + "\n  ".join(offenders)
    )


# ──────────────────────────────────────────────────────────────────────────────
# TS-08..10 — sleep, is_alive, cold-start raise
# ──────────────────────────────────────────────────────────────────────────────


def test_ts08_sleep_main_thread_uses_ib_sleep():
    """Main-thread client.sleep defers to ib.sleep (drives the event loop)."""
    ib = MagicMock()
    ib.sleep.return_value = None
    client = _make_client(ib)

    client.sleep(0.1)

    ib.sleep.assert_called_once_with(0.1)


def test_ts09_is_alive_daemon_routes_async(bg_event_loop):
    """is_alive from a daemon thread routes via reqCurrentTimeAsync."""
    import datetime

    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.reqCurrentTime.side_effect = RuntimeError("sync path must not run")
    ib.reqCurrentTimeAsync = AsyncMock(return_value=datetime.datetime.now())
    client = _make_client(ib, main_loop=bg_event_loop)

    result = _run_on_thread(client.is_alive)

    assert result is True
    ib.reqCurrentTimeAsync.assert_awaited_once()


def test_ts10_daemon_call_without_main_loop_raises():
    """Cold-start race: daemon caller before connect() captured the loop -> raise.

    The previous design silently returned False from the route-needed check,
    which dropped daemon callers onto the broken sync path -- exactly the
    failure mode this fix exists to close.
    """
    ib = MagicMock()
    client = _make_client(ib, main_loop=None)  # never connected

    with pytest.raises(RuntimeError, match="non-main thread before connect"):
        _run_on_thread(client.qualify_contract, SimpleNamespace())


def test_ts11_daemon_call_with_stopped_loop_raises(bg_event_loop):
    """If the main loop has stopped, daemon callers must raise, not retry sync."""
    import threading
    import time

    ib = MagicMock()
    client = _make_client(ib, main_loop=bg_event_loop)

    stopped = threading.Event()
    bg_event_loop.call_soon_threadsafe(lambda: (bg_event_loop.stop(), stopped.set()))
    assert stopped.wait(timeout=2.0), "loop did not stop within 2s"
    # Give the loop thread one more tick to exit run_forever
    time.sleep(0.05)

    with pytest.raises(RuntimeError, match="main loop is not running"):
        _run_on_thread(client.qualify_contract, SimpleNamespace())


# ──────────────────────────────────────────────────────────────────────────────
# TS-12..13 — ib_place_order / ib_cancel_order
#
# These cover the root cause of the SECOND breakage layer:
# Client.sendMsg() calls getLoop() → asyncio.get_event_loop_policy().get_event_loop()
# which raises "There is no current event loop in thread X" from any daemon thread.
# ──────────────────────────────────────────────────────────────────────────────


def test_ts12_ib_place_order_daemon_routes_to_main_loop(bg_event_loop):
    """ib_place_order from a daemon thread executes ib.placeOrder on the main loop."""
    from types import SimpleNamespace

    fake_trade = SimpleNamespace(order=SimpleNamespace(orderId=42))

    ib = MagicMock()
    ib.placeOrder.return_value = fake_trade
    client = _make_client(ib, main_loop=bg_event_loop)

    result = _run_on_thread(client.ib_place_order, SimpleNamespace(), SimpleNamespace())

    ib.placeOrder.assert_called_once()
    assert result is fake_trade


def test_ts13_ib_cancel_order_daemon_routes_to_main_loop(bg_event_loop):
    """ib_cancel_order from a daemon thread executes ib.cancelOrder on the main loop."""
    ib = MagicMock()
    ib.cancelOrder.return_value = None
    client = _make_client(ib, main_loop=bg_event_loop)

    _run_on_thread(client.ib_cancel_order, SimpleNamespace())

    ib.cancelOrder.assert_called_once()


# ──────────────────────────────────────────────────────────────────────────────
# TS-14..15 — _set_market_data_type
#
# Bug being prevented: ib.reqMarketDataType -> Client.send -> Client.sendMsg
# calls getLoop() from the calling thread. From ReconnectManager's daemon
# thread this raises "There is no current event loop in thread 'ReconnectManager'".
# connect()'s post-handshake step then fails, ReconnectManager retries, the next
# connect() short-circuits on `if ib.isConnected(): return`, and the data mode
# is never re-applied. TWS resets the mode to REALTIME on every fresh session,
# so reqMktData on a paper account returns error 10089 forever. Symptom: any
# strategy that pulls live prices (PingPong AAPL) stops trading after the
# nightly gateway auto-restart.
# ──────────────────────────────────────────────────────────────────────────────


def test_ts14_set_market_data_type_daemon_routes_to_main_loop(bg_event_loop):
    """_set_market_data_type from a daemon thread routes ib.reqMarketDataType
    onto the main loop -- the only place Client.sendMsg's getLoop() can run.

    The previous incarnation of this test only asserted that
    reqMarketDataType was called; a MagicMock satisfies that even if the call
    runs on the daemon thread (the very bug being fixed). Lock the regression
    by recording the thread the call actually ran on and asserting it is NOT
    the daemon worker.
    """
    ib = MagicMock()
    call_threads: list = []

    def _record(mode: int) -> None:
        call_threads.append(threading.current_thread())

    ib.reqMarketDataType.side_effect = _record
    client = _make_client(ib, main_loop=bg_event_loop)

    _run_on_thread(client._set_market_data_type, 3)

    ib.reqMarketDataType.assert_called_once_with(3)
    assert len(call_threads) == 1
    assert (
        call_threads[0].name != "ts-test-worker"
    ), "reqMarketDataType ran on the daemon thread -- routing did not happen"


def test_ts15_set_market_data_type_main_thread_uses_sync(monkeypatch):
    """Main-thread caller uses the direct ib.reqMarketDataType wrapper.

    No routing required: verify run_coroutine_threadsafe was not invoked.
    Without this guard the test passes even if the code accidentally takes
    the daemon-routing path on the main thread.
    """
    import asyncio

    routed: list = []
    original = asyncio.run_coroutine_threadsafe

    def _spy(coro, loop):
        routed.append(coro)
        return original(coro, loop)

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", _spy)

    ib = MagicMock()
    ib.reqMarketDataType.return_value = None
    client = _make_client(ib)  # no _main_loop -> main path stays sync

    client._set_market_data_type(3)

    ib.reqMarketDataType.assert_called_once_with(3)
    assert routed == [], "Main-thread caller should not schedule via run_coroutine_threadsafe"
