"""Section 1: Connection tests — requires IBKR TWS."""

import os

import pytest

IS_CI = bool(os.getenv("GITHUB_ACTIONS"))
pytestmark = pytest.mark.skipif(IS_CI, reason="requires IBKR TWS connection")

_TEST_CLIENT_ID = 5  # dedicated client ID for connection tests


def test_c01_connect_with_tws():
    from broker.ibkr_client import IBKRClient

    c = IBKRClient(client_id=_TEST_CLIENT_ID)
    c.connect()
    assert c.is_connected
    assert c.account != "N/A"
    c.disconnect()


def test_c03_wrong_port_raises():
    from broker.ibkr_client import IBKRClient

    c = IBKRClient(port=9999)
    try:
        c.connect()
        c.disconnect()
        assert False, "Should have raised"
    except Exception:
        pass


def test_c05_connect_twice_no_crash():
    from broker.ibkr_client import IBKRClient

    c = IBKRClient(client_id=_TEST_CLIENT_ID)
    c.connect()
    c.connect()
    assert c.is_connected
    c.disconnect()


def test_c06_disconnect_and_reconnect():
    from broker.ibkr_client import IBKRClient

    c = IBKRClient(client_id=_TEST_CLIENT_ID)
    c.connect()
    assert c.is_connected
    c.disconnect()
    assert not c.is_connected
    c.connect()
    assert c.is_connected
    c.disconnect()


def test_c08_is_paper_flag():
    from broker.ibkr_client import IBKRClient

    assert IBKRClient(port=7497).is_paper is True
    assert IBKRClient(port=7496).is_paper is False
