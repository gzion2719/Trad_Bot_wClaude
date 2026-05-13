"""
Pytest configuration and shared fixtures for TradeBot test suite.
"""

import logging
import os
import sys
from pathlib import Path

import pytest

# Ensure project root is on the path (covers both `pytest` from root and direct invocation)
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.logging_config import setup_logging

setup_logging()
logging.disable(logging.INFO)  # show WARNING+ only; suppress INFO/DEBUG noise

IS_CI = bool(os.getenv("GITHUB_ACTIONS"))


# ── Dashboard auth fixtures (DB-X5) ──────────────────────────────────────────
#
# Two distinct rate-limit state vars must be cleared between tests:
#   - dashboard_app._rate_state       (per-IP login rate limit + lockout)
#   - dashboard_app._SESSION_RATE_STATE (per-session /api/equity-history limit)
# Clearing only the first leaks state across tests that hit /api/equity-history.


def _reset_all_rate_state() -> None:
    """Clear both dashboard rate-limit state vars. Idempotent."""
    from dashboard import app as dashboard_app

    with dashboard_app._rate_lock:
        dashboard_app._rate_state.clear()
    with dashboard_app._session_rate_lock:
        dashboard_app._SESSION_RATE_STATE.clear()


@pytest.fixture
def dashboard_token(monkeypatch):
    """Set DASHBOARD_TOKEN via monkeypatch (auto-restored). Yields the token.

    Resets both rate-limit state vars at setup AND teardown so tests using
    this fixture cannot leak login-attempt counters or session-equity counters
    into neighbours.
    """
    token = "acct-test-secret"
    monkeypatch.setenv("DASHBOARD_TOKEN", token)
    _reset_all_rate_state()
    yield token
    _reset_all_rate_state()


@pytest.fixture
def dashboard_client_unauth():
    """Unauthenticated TestClient for dashboard. No dependency on dashboard_token.

    `_require_session` returns 401 on missing cookie before any token logic
    runs (dashboard/app.py:242-245), so 401 tests are independent of whether
    DASHBOARD_TOKEN is configured. Keeping these decoupled also lets a future
    'no token configured -> 503' test exist alongside.
    """
    from starlette.testclient import TestClient
    from dashboard import app as dashboard_app

    _reset_all_rate_state()
    tc = TestClient(dashboard_app.app, raise_server_exceptions=False)
    yield tc
    _reset_all_rate_state()


@pytest.fixture
def dashboard_client(dashboard_token):
    """Authenticated TestClient — POSTs /api/login, asserts 200, yields client.

    Yields the client only (not a tuple); the token value is available via
    the `dashboard_token` fixture if a test needs it. Rate-state teardown is
    inherited from `dashboard_token` (finalizers unwind in reverse order).
    If this fixture is ever decoupled from `dashboard_token`, add an explicit
    `_reset_all_rate_state()` call on teardown here.
    """
    from starlette.testclient import TestClient
    from dashboard import app as dashboard_app

    tc = TestClient(dashboard_app.app, raise_server_exceptions=False)
    login = tc.post("/api/login", json={"token": dashboard_token})
    assert login.status_code == 200, f"login failed: {login.status_code} {login.text}"
    yield tc


@pytest.fixture(scope="session")
def live_client():
    """Session-scoped connected IBKRClient + OrderManager, shared across all broker tests.

    Skipped automatically when GITHUB_ACTIONS=true.
    """
    if IS_CI:
        pytest.skip("requires IBKR TWS connection")

    from broker.ibkr_client import IBKRClient
    from broker.order_manager import OrderManager

    client = IBKRClient()
    client.connect()
    om = OrderManager(client)

    # Cancel leftover orders from previous sessions before starting
    om.cancel_all()
    client.ib.sleep(0.5)

    yield client, om

    # Teardown — cancel anything leftover and disconnect cleanly
    try:
        if client.is_connected:
            remaining = om.get_open_orders()
            if remaining:
                om.cancel_all()
                client.ib.sleep(1)
            client.disconnect()
    except Exception:
        pass
