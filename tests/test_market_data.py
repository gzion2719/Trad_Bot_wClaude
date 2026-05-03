"""Section 2: Market data tests — requires IBKR TWS."""

import math
import os

import pytest

IS_CI = bool(os.getenv("GITHUB_ACTIONS"))
pytestmark = pytest.mark.skipif(IS_CI, reason="requires IBKR TWS connection")


def test_d01_msft_price_positive(live_client):
    c, _ = live_client
    price = c.get_market_price("MSFT")
    assert isinstance(price, float)
    assert price > 0
    assert not math.isnan(price)


def test_d01b_msft_price_positive_b(live_client):
    c, _ = live_client
    assert c.get_market_price("MSFT") > 0


def test_d01c_nvda_price_positive(live_client):
    c, _ = live_client
    assert c.get_market_price("NVDA") > 0


def test_d04_invalid_ticker_raises(live_client):
    c, _ = live_client
    with pytest.raises(RuntimeError):
        c.get_market_price("XYZXYZ999")


def test_d07_multiple_requests_no_stale_subscriptions(live_client):
    c, _ = live_client
    for _ in range(5):
        assert c.get_market_price("MSFT") > 0
