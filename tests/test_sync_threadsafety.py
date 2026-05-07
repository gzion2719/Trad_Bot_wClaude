"""B-08 part 2 regression: OrderManager.sync() must not crash from a non-main thread.

Python 3.12 raises RuntimeError("There is no current event loop in thread X")
when ib_insync calls asyncio.get_event_loop() from a non-main thread.
The fix routes ib calls through asyncio.run_coroutine_threadsafe on the saved
main loop. These tests verify both code-paths without a live TWS connection.
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch


def _make_mock_client(main_loop=None):
    """Return a minimal IBKRClient mock with optional _main_loop."""
    client = MagicMock()
    client.is_connected = False  # skip auto-sync in __init__
    client._main_loop = main_loop
    return client


def _make_mock_ib(open_trades=None):
    """Return a mock ib object whose calls are synchronous no-ops."""
    ib = MagicMock()
    ib.reqAllOpenOrders.return_value = None
    # Async variant used by the threadsafe (non-main-thread) path.
    ib.reqAllOpenOrdersAsync = AsyncMock(return_value=None)
    ib.sleep.return_value = None
    ib.openTrades.return_value = open_trades or []
    return ib


def _build_order_manager(client, ib):
    """Construct OrderManager with patched ib_insync imports."""
    with patch("broker.order_manager.IBKRClient"):
        from broker.order_manager import OrderManager

        om = OrderManager.__new__(OrderManager)
        om._client = client
        om._ib = ib
        om._lock = threading.Lock()
        om._orders = {}
        om._seen_exec_ids = set()
        om._on_fill_callbacks = []
        om._on_cancel_callbacks = []
        om._on_error_callbacks = []
        return om


# ── main-thread path ──────────────────────────────────────────────────────────


def test_om_sync01_main_thread_uses_direct_path():
    """sync() from the main thread must call ib.reqAllOpenOrders() directly."""
    client = _make_mock_client()
    ib = _make_mock_ib()
    om = _build_order_manager(client, ib)

    assert threading.current_thread() is threading.main_thread()
    count = om.sync()

    ib.reqAllOpenOrders.assert_called_once()
    ib.sleep.assert_called_once_with(0.5)
    ib.openTrades.assert_called_once()
    assert count == 0


# ── non-main-thread path ──────────────────────────────────────────────────────


def test_om_sync02_non_main_thread_no_loop_falls_back_to_direct():
    """If _main_loop is None, non-main thread falls back to direct path (no crash).

    Coverage note: the mock ib never calls asyncio.get_event_loop(), so this test
    only verifies the branching logic (guard routes to else-branch), not that the
    direct path is safe under real ib_insync from a non-main thread. The actual
    production guard against that crash is the _main_loop is not None condition —
    if _main_loop was never captured the bot would have already failed at connect().
    """
    client = _make_mock_client(main_loop=None)
    ib = _make_mock_ib()
    om = _build_order_manager(client, ib)

    errors: list[Exception] = []

    def _run():
        try:
            om.sync()
        except Exception as exc:
            errors.append(exc)

    t = threading.Thread(target=_run, name="TestThread-NoLoop", daemon=True)
    t.start()
    t.join(timeout=5)
    assert t.is_alive() is False, "sync() hung — thread did not finish in time"
    assert not errors, f"sync() raised from non-main thread: {errors}"
    ib.reqAllOpenOrders.assert_called_once()


def test_om_sync03_non_main_thread_routes_through_main_loop():
    """sync() from a non-main thread uses run_coroutine_threadsafe on the main loop."""
    # Run a real event loop in a background thread to act as the "main loop".
    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
    loop_thread.start()

    try:
        client = _make_mock_client(main_loop=loop)
        ib = _make_mock_ib()
        om = _build_order_manager(client, ib)

        errors: list[Exception] = []
        result: list[int] = []

        def _run():
            try:
                count = om.sync()
                result.append(count)
            except Exception as exc:
                errors.append(exc)

        t = threading.Thread(target=_run, name="ReconnectManager", daemon=True)
        t.start()
        t.join(timeout=10)
        assert t.is_alive() is False, "sync() hung — thread did not finish in time"
        assert not errors, f"sync() raised from non-main thread: {errors}"
        assert result == [0]
        # The threadsafe path MUST use the *Async variant — the sync wrapper
        # would call loop.run_until_complete() inside an already-running loop
        # and raise "This event loop is already running" (see 2026-05-07 incident).
        ib.reqAllOpenOrdersAsync.assert_awaited_once()
        ib.reqAllOpenOrders.assert_not_called()
        ib.openTrades.assert_called_once()
        # ib.sleep must NOT have been called (async path uses asyncio.sleep instead)
        ib.sleep.assert_not_called()

    finally:
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=3)
        loop.close()
