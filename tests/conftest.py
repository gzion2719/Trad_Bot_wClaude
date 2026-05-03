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
