"""Section 13: ReconnectManager tests — requires IBKR TWS."""

import os
import threading
import time

import pytest

from broker.reconnect import ReconnectManager

IS_CI = bool(os.getenv("GITHUB_ACTIONS"))
pytestmark = pytest.mark.skipif(IS_CI, reason="requires IBKR TWS connection")


def test_rcn01_starts_and_reports_connected(live_client):
    c, o = live_client
    rcn = ReconnectManager(client=c, order_manager=o, max_attempts=3)
    rcn.start()
    assert rcn.is_connected is True
    assert rcn.is_halted is False
    rcn.stop()


def test_rcn02_wait_for_connection_returns_true(live_client):
    c, o = live_client
    rcn = ReconnectManager(client=c, order_manager=o)
    rcn.start()
    assert rcn.wait_for_connection(timeout=2.0) is True
    rcn.stop()


def test_rcn03_wait_times_out_when_disconnected(live_client):
    c, o = live_client
    rcn = ReconnectManager(client=c, order_manager=o, max_attempts=1)
    rcn.start()
    rcn._connected_event.clear()
    start = time.time()
    result = rcn.wait_for_connection(timeout=1.0)
    elapsed = time.time() - start
    assert result is False
    assert elapsed >= 0.9
    rcn._connected_event.set()
    rcn.stop()


def test_rcn04_stop_unblocks_waiting_threads(live_client):
    c, o = live_client
    rcn = ReconnectManager(client=c, order_manager=o)
    rcn.start()
    rcn._connected_event.clear()
    unblocked = []

    def waiter():
        rcn.wait_for_connection(timeout=10.0)
        unblocked.append(True)

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    time.sleep(0.2)
    rcn.stop()
    t.join(timeout=2.0)
    assert unblocked
