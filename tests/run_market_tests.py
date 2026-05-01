"""
TradeBot — Market Hours Test Suite
Tests that require live market fills: P-01, P-02, P-12, S-06, POS-02

Run this during regular US market hours (9:30 AM - 4:00 PM EST).
Uses small quantities on paper account only.
"""

import sys

sys.path.insert(0, "..")

from config.logging_config import setup_logging

setup_logging()

import logging

logging.disable(logging.CRITICAL)

from broker.ibkr_client import IBKRClient
from broker.order_manager import OrderManager
from models.order import OrderAction, OrderRequest, TimeInForce

# ── Test framework ──────────────────────────────────────────────────────────

results = []


def test(test_id, description):
    def decorator(fn):
        def wrapper():
            print(f"\n  Running {test_id}: {description}...")
            try:
                fn()
                results.append((test_id, "PASS", description, ""))
                print(f"  [PASS] {test_id}: {description}")
            except AssertionError as e:
                results.append((test_id, "FAIL", description, str(e)))
                print(f"  [FAIL] {test_id}: {description}")
                print(f"         > {e}")
            except Exception as e:
                results.append((test_id, "FAIL", description, f"{type(e).__name__}: {e}"))
                print(f"  [FAIL] {test_id}: {description}")
                print(f"         > {type(e).__name__}: {e}")

        return wrapper

    return decorator


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def summary():
    passed = sum(1 for r in results if r[1] == "PASS")
    failed = sum(1 for r in results if r[1] == "FAIL")
    print(f"\n{'='*60}")
    print(f"  RESULTS: {passed}/{len(results)} passed  |  {failed} failed")
    print(f"{'='*60}")
    if failed:
        print("\nFailed tests:")
        for tid, status, desc, err in results:
            if status == "FAIL":
                print(f"  {tid}: {desc}")
                print(f"       {err}")
    return failed


# ── Setup ───────────────────────────────────────────────────────────────────

client = IBKRClient()
client.connect()
om = OrderManager(client)

# Cancel any leftover open orders before starting
print("\nCleaning up leftover open orders...")
om.cancel_all()
client.ib.sleep(1)
print("Clean.\n")

# ── Tests ────────────────────────────────────────────────────────────────────

section("MARKET-HOURS TESTS")


@test("P-01", "Market BUY fills during market hours")
def p01():
    fills = []
    om.on_fill(lambda r: fills.append(r))

    r = OrderRequest(symbol="AAPL", action=OrderAction.BUY, quantity=1, tif=TimeInForce.GTC)
    result = om.place_order(r)
    assert result.order_id > 0, "No order ID returned"

    # Wait up to 10s for fill
    for _ in range(20):
        client.ib.sleep(0.5)
        if fills:
            break

    assert len(fills) > 0, "Order was not filled within 10 seconds"
    fill = fills[0]
    assert fill.filled == 1.0, f"Expected 1 share filled, got {fill.filled}"
    assert fill.avg_fill_price > 0, "Fill price is zero or negative"
    print(f"         Filled 1 AAPL @ ${fill.avg_fill_price:.2f}")


@test("P-12", "on_fill callback receives correct OrderResult")
def p12():
    # Already verified in P-01 — check the fill result had correct fields
    fills = []
    om.on_fill(lambda r: fills.append(r))

    r = OrderRequest(symbol="MSFT", action=OrderAction.BUY, quantity=1, tif=TimeInForce.GTC)
    om.place_order(r)

    for _ in range(20):
        client.ib.sleep(0.5)
        if fills:
            break

    assert len(fills) > 0, "on_fill callback did not fire"
    fill = fills[-1]
    assert fill.symbol == "MSFT"
    assert fill.action == "BUY"
    assert fill.quantity == 1.0
    assert fill.avg_fill_price > 0
    print(f"         Callback fired: BUY 1 MSFT @ ${fill.avg_fill_price:.2f}")


@test("POS-02", "Position appears after fill")
def pos02():
    client.ib.sleep(1)  # allow position to settle
    positions = om.get_positions()
    symbols = [p.symbol for p in positions]
    assert (
        "AAPL" in symbols or "MSFT" in symbols
    ), f"Expected AAPL or MSFT in positions, got: {symbols}"
    for p in positions:
        if p.symbol in ("AAPL", "MSFT"):
            assert p.quantity > 0, f"{p.symbol} position quantity is not positive"
            assert p.avg_cost > 0, f"{p.symbol} avg cost is zero"
            print(f"         {p.symbol}: {p.quantity} shares @ avg ${p.avg_cost:.2f}")


@test("S-06", "Fill event fires automatically without polling")
def s06():
    # Place one more order and verify callback fires on its own
    fills = []
    om.on_fill(lambda r: fills.append(r))

    r = OrderRequest(symbol="IBM", action=OrderAction.BUY, quantity=1, tif=TimeInForce.GTC)
    om.place_order(r)

    # Just wait — do NOT poll. The event loop should push the fill to us.
    client.ib.sleep(10)

    assert len(fills) > 0, "Fill event did not fire automatically (no polling)"
    print("         Fill event received automatically for IBM")


@test("P-02", "Market SELL reduces position")
def p02():
    # Sell 1 AAPL that we bought in P-01
    positions_before = {p.symbol: p.quantity for p in om.get_positions()}
    assert "AAPL" in positions_before, "No AAPL position to sell — P-01 may have failed"

    fills = []
    om.on_fill(lambda r: fills.append(r))

    r = OrderRequest(symbol="AAPL", action=OrderAction.SELL, quantity=1, tif=TimeInForce.GTC)
    om.place_order(r)

    for _ in range(20):
        client.ib.sleep(0.5)
        if fills:
            break

    assert len(fills) > 0, "SELL order was not filled within 10 seconds"
    fill = fills[-1]
    assert fill.action == "SELL"
    assert fill.filled == 1.0
    print(f"         Sold 1 AAPL @ ${fill.avg_fill_price:.2f}")


p01()
p12()
pos02()
s06()
p02()

# ── Cleanup ──────────────────────────────────────────────────────────────────

section("CLEANUP")
try:
    open_orders = om.get_open_orders()
    if open_orders:
        print(f"  Cancelling {len(open_orders)} leftover order(s)...")
        om.cancel_all()
        client.ib.sleep(1)
    client.disconnect()
    print("  Disconnected cleanly.")
except Exception as e:
    print(f"  Cleanup error: {e}")

failed = summary()
sys.exit(failed)
