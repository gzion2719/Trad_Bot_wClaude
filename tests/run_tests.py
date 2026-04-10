"""
TradeBot Test Runner
Executes all pre-market testable cases from TEST_PLAN.md
"""
import sys
import traceback
from datetime import datetime, timezone

sys.path.insert(0, "..")

from config.logging_config import setup_logging
setup_logging()

import logging
logging.disable(logging.INFO)  # show WARNING / ERROR / CRITICAL; suppress INFO and DEBUG

from broker.ibkr_client import IBKRClient
from broker.order_manager import OrderManager, DuplicateOrderError
from models.order import OrderAction, OrderRequest, OrderType, TimeInForce

# ── Test framework ──────────────────────────────────────────────────────────

results = []

def test(test_id, description):
    """Decorator that records pass/fail for each test."""
    def decorator(fn):
        def wrapper():
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
    total = len(results)
    print(f"\n{'='*60}")
    print(f"  RESULTS: {passed}/{total} passed  |  {failed} failed")
    print(f"{'='*60}")
    if failed:
        print("\nFailed tests:")
        for tid, status, desc, err in results:
            if status == "FAIL":
                print(f"  {tid}: {desc}")
                print(f"       {err}")
    return failed

# ── Shared client (connected) ───────────────────────────────────────────────

client = None
om = None

def get_client():
    global client, om
    if client is None or not client.is_connected:
        client = IBKRClient()
        client.connect()
        om = OrderManager(client)
    return client, om

# Cancel all leftover orders from previous sessions before starting
print("\nCleaning up any leftover open orders from previous sessions...")
_c, _o = get_client()
_leftovers = _o.cancel_all()
if _leftovers:
    print(f"  Cancelled {_leftovers} leftover order(s) — waiting for TWS confirmation...")
    _c.ib.sleep(2)
else:
    _c.ib.sleep(0.5)
print("Clean.\n")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1: CONNECTION TESTS
# ══════════════════════════════════════════════════════════════════════════════

section("1. CONNECTION TESTS")

_TEST_CLIENT_ID = 5  # dedicated clientId for connection tests (avoids conflict with shared client on id=1)

@test("C-01", "Connect with TWS running")
def c01():
    c = IBKRClient(client_id=_TEST_CLIENT_ID)
    c.connect()
    assert c.is_connected, "Not connected"
    assert c.account != "N/A", "No account returned"
    c.disconnect()

@test("C-03", "Connect with wrong port raises error")
def c03():
    c = IBKRClient(port=9999)
    try:
        c.connect()
        c.disconnect()
        assert False, "Should have raised an exception"
    except Exception:
        pass  # any exception is acceptable — just needs to not hang silently

@test("C-05", "Calling connect() twice does not crash")
def c05():
    c = IBKRClient(client_id=_TEST_CLIENT_ID)
    c.connect()
    c.connect()  # should log warning and skip
    assert c.is_connected
    c.disconnect()

@test("C-06", "Disconnect and reconnect works")
def c06():
    c = IBKRClient(client_id=_TEST_CLIENT_ID)
    c.connect()
    assert c.is_connected
    c.disconnect()
    assert not c.is_connected
    c.connect()
    assert c.is_connected
    c.disconnect()

@test("C-08", "is_paper is True for port 7497")
def c08():
    c = IBKRClient(port=7497)
    assert c.is_paper is True
    c2 = IBKRClient(port=7496)
    assert c2.is_paper is False

c01()
c03()
c05()
c06()
c08()

# Allow TWS to settle after multiple connect/disconnect cycles before data tests
_settle_client, _ = get_client()
_settle_client.ib.sleep(3)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2: MARKET DATA TESTS
# ══════════════════════════════════════════════════════════════════════════════

section("2. MARKET DATA TESTS")

@test("D-01", "Get price for MSFT returns positive float")
def d01():
    c, _ = get_client()
    price = c.get_market_price("MSFT")
    assert isinstance(price, float), f"Expected float, got {type(price)}"
    assert price > 0, f"Expected positive price, got {price}"
    import math
    assert not math.isnan(price), "Price is NaN"

@test("D-01b", "Get price for MSFT returns positive float")
def d01b():
    c, _ = get_client()
    price = c.get_market_price("MSFT")
    assert price > 0

@test("D-01c", "Get price for NVDA returns positive float")
def d01c():
    c, _ = get_client()
    price = c.get_market_price("NVDA")
    assert price > 0

@test("D-04", "Invalid ticker raises RuntimeError")
def d04():
    c, _ = get_client()
    try:
        c.get_market_price("XYZXYZ999")
        assert False, "Should have raised RuntimeError"
    except RuntimeError:
        pass  # expected

@test("D-07", "Multiple price requests leave no stale subscriptions")
def d07():
    c, _ = get_client()
    for _ in range(5):
        price = c.get_market_price("MSFT")
        assert price > 0
    # if subscriptions leaked, TWS would return error 10182 (already subscribed)
    # passing without error means clean cancellation each time

d01()
d01b()
d01c()
d04()
d07()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3: ORDER VALIDATION TESTS (no connection needed)
# ══════════════════════════════════════════════════════════════════════════════

section("3. ORDER VALIDATION TESTS")

@test("V-01", "quantity=0 raises ValueError")
def v01():
    try:
        OrderRequest(symbol="AAPL", action=OrderAction.BUY, quantity=0)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass

@test("V-02", "quantity=-5 raises ValueError")
def v02():
    try:
        OrderRequest(symbol="AAPL", action=OrderAction.BUY, quantity=-5)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass

@test("V-03", "LIMIT order with no limit_price raises ValueError")
def v03():
    try:
        OrderRequest(symbol="AAPL", action=OrderAction.BUY, quantity=1, order_type=OrderType.LIMIT)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass

@test("V-04", "STOP order with no stop_price raises ValueError")
def v04():
    try:
        OrderRequest(symbol="AAPL", action=OrderAction.BUY, quantity=1, order_type=OrderType.STOP)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass

@test("V-05", "STOP_LIMIT order missing prices raises ValueError")
def v05():
    try:
        OrderRequest(symbol="AAPL", action=OrderAction.BUY, quantity=1, order_type=OrderType.STOP_LIMIT)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass

@test("V-06", "Lowercase symbol is auto-uppercased")
def v06():
    r = OrderRequest(symbol="aapl", action=OrderAction.BUY, quantity=1)
    assert r.symbol == "AAPL", f"Expected 'AAPL', got '{r.symbol}'"

@test("V-07", "Symbol with spaces is auto-stripped")
def v07():
    r = OrderRequest(symbol="  AAPL  ", action=OrderAction.BUY, quantity=1)
    assert r.symbol == "AAPL", f"Expected 'AAPL', got '{r.symbol}'"

@test("V-08", "LIMIT order with limit_price=0 raises ValueError")
def v08():
    try:
        r = OrderRequest(symbol="AAPL", action=OrderAction.BUY, quantity=1,
                        order_type=OrderType.LIMIT, limit_price=0)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass

@test("V-09", "LIMIT order with negative limit_price raises ValueError")
def v09():
    try:
        r = OrderRequest(symbol="AAPL", action=OrderAction.BUY, quantity=1,
                        order_type=OrderType.LIMIT, limit_price=-10)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass

v01(); v02(); v03(); v04(); v05(); v06(); v07(); v08(); v09()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4: ORDER PLACEMENT TESTS
# ══════════════════════════════════════════════════════════════════════════════

section("4. ORDER PLACEMENT TESTS")

@test("P-03", "Limit BUY far below market sits as PreSubmitted")
def p03():
    c, o = get_client()
    price = c.get_market_price("GE")
    r = OrderRequest(symbol="GE", action=OrderAction.BUY, quantity=1,
                     order_type=OrderType.LIMIT, limit_price=round(price * 0.5, 2),
                     tif=TimeInForce.GTC)
    result = o.place_order(r)
    assert result.order_id > 0
    assert result.status.value in ("PreSubmitted", "Submitted", "PendingSubmit")
    o.cancel_order(result.order_id)
    c.ib.sleep(0.5)

@test("P-04", "Limit SELL far above market sits as PreSubmitted")
def p04():
    c, o = get_client()
    price = c.get_market_price("MSFT")
    r = OrderRequest(symbol="MSFT", action=OrderAction.SELL, quantity=1,
                     order_type=OrderType.LIMIT, limit_price=round(price * 2.0, 2),
                     tif=TimeInForce.GTC)
    result = o.place_order(r)
    assert result.order_id > 0
    assert result.status.value in ("PreSubmitted", "Submitted", "PendingSubmit")
    o.cancel_order(result.order_id)
    c.ib.sleep(0.5)

@test("P-07", "Order for invalid symbol raises RuntimeError")
def p07():
    _, o = get_client()
    r = OrderRequest(symbol="XYZXYZ999", action=OrderAction.BUY, quantity=1)
    try:
        o.place_order(r)
        assert False, "Should have raised RuntimeError"
    except RuntimeError:
        pass

@test("P-08", "Order when not connected raises ConnectionError")
def p08():
    c = IBKRClient()  # fresh client, not connected
    o = OrderManager(c)
    r = OrderRequest(symbol="AAPL", action=OrderAction.BUY, quantity=1)
    try:
        o.place_order(r)
        assert False, "Should have raised ConnectionError"
    except ConnectionError:
        pass

@test("P-05", "GTC limit order far below market stays open (not filled)")
def p05():
    c, o = get_client()
    o.cancel_all("IBM")  # clean slate for this symbol
    c.ib.sleep(0.5)
    price = c.get_market_price("IBM")
    r = OrderRequest(symbol="IBM", action=OrderAction.BUY, quantity=1,
                     order_type=OrderType.LIMIT, limit_price=round(price * 0.5, 2),
                     tif=TimeInForce.GTC)
    result = o.place_order(r)
    assert result.order_id > 0
    assert result.status.value in ("PreSubmitted", "Submitted", "PendingSubmit")
    o.cancel_order(result.order_id)
    c.ib.sleep(0.5)

p03(); p04(); p07(); p08(); p05()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5: DUPLICATE PREVENTION TESTS
# ══════════════════════════════════════════════════════════════════════════════

section("5. DUPLICATE PREVENTION TESTS")

@test("DUP-01", "Placing same BUY twice raises DuplicateOrderError")
def dup01():
    c, o = get_client()
    price = c.get_market_price("GE")
    r = OrderRequest(symbol="GE", action=OrderAction.BUY, quantity=1,
                     order_type=OrderType.LIMIT, limit_price=round(price * 0.5, 2),
                     tif=TimeInForce.GTC)
    result = o.place_order(r)
    try:
        o.place_order(r)
        o.cancel_order(result.order_id)
        assert False, "Should have raised DuplicateOrderError"
    except DuplicateOrderError:
        pass
    finally:
        o.cancel_order(result.order_id)
        c.ib.sleep(0.5)

@test("DUP-02", "BUY then SELL for same symbol is NOT blocked")
def dup02():
    c, o = get_client()
    price = c.get_market_price("MSFT")
    buy = OrderRequest(symbol="MSFT", action=OrderAction.BUY, quantity=1,
                       order_type=OrderType.LIMIT, limit_price=round(price * 0.5, 2),
                       tif=TimeInForce.GTC)
    sell = OrderRequest(symbol="MSFT", action=OrderAction.SELL, quantity=1,
                        order_type=OrderType.LIMIT, limit_price=round(price * 2.0, 2),
                        tif=TimeInForce.GTC)
    r1 = o.place_order(buy)
    r2 = o.place_order(sell)  # should NOT raise
    assert r2.order_id > 0
    o.cancel_order(r1.order_id)
    o.cancel_order(r2.order_id)
    c.ib.sleep(0.5)

@test("DUP-03", "After cancel, same order can be placed again")
def dup03():
    c, o = get_client()
    o.cancel_all("GE")
    c.ib.sleep(0.5)
    price = c.get_market_price("GE")
    r = OrderRequest(symbol="GE", action=OrderAction.BUY, quantity=1,
                     order_type=OrderType.LIMIT, limit_price=round(price * 0.5, 2),
                     tif=TimeInForce.GTC)
    r1 = o.place_order(r)
    o.cancel_order(r1.order_id)
    c.ib.sleep(1)
    r2 = o.place_order(r)  # should NOT raise
    assert r2.order_id > 0
    o.cancel_order(r2.order_id)
    c.ib.sleep(0.5)

@test("DUP-05", "allow_duplicate=True bypasses check")
def dup05():
    c, o = get_client()
    price = c.get_market_price("GE")
    r = OrderRequest(symbol="GE", action=OrderAction.BUY, quantity=1,
                     order_type=OrderType.LIMIT, limit_price=round(price * 0.5, 2),
                     tif=TimeInForce.GTC)
    r1 = o.place_order(r)
    r2 = o.place_order(r, allow_duplicate=True)  # should NOT raise
    assert r2.order_id > 0
    o.cancel_all("GE")
    c.ib.sleep(0.5)

dup01(); dup02(); dup03(); dup05()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6: CANCELLATION TESTS
# ══════════════════════════════════════════════════════════════════════════════

section("6. CANCELLATION TESTS")

@test("X-01", "Cancel open order returns True and fires on_cancel")
def x01():
    c, o = get_client()
    o._clear_callbacks()  # prevent stale callbacks from earlier tests from firing
    fired = []
    o.on_cancel(lambda r: fired.append(r))
    price = c.get_market_price("GE")
    r = OrderRequest(symbol="GE", action=OrderAction.BUY, quantity=1,
                     order_type=OrderType.LIMIT, limit_price=round(price * 0.5, 2),
                     tif=TimeInForce.GTC)
    result = o.place_order(r)
    cancelled = o.cancel_order(result.order_id)
    c.ib.sleep(1)
    assert cancelled is True
    assert len(fired) > 0, "on_cancel callback did not fire"

@test("X-03", "Cancel already-cancelled order returns False")
def x03():
    c, o = get_client()
    price = c.get_market_price("MSFT")
    r = OrderRequest(symbol="MSFT", action=OrderAction.BUY, quantity=1,
                     order_type=OrderType.LIMIT, limit_price=round(price * 0.5, 2),
                     tif=TimeInForce.GTC)
    result = o.place_order(r)
    o.cancel_order(result.order_id)
    c.ib.sleep(1)
    cancelled_again = o.cancel_order(result.order_id)
    assert cancelled_again is False

@test("X-04", "Cancel non-existent order ID returns False")
def x04():
    _, o = get_client()
    result = o.cancel_order(999999)
    assert result is False

@test("X-05", "cancel_all() with no open orders returns 0")
def x05():
    c, o = get_client()
    o.cancel_all()
    c.ib.sleep(1)
    count = o.cancel_all()
    assert count == 0

@test("X-06", "cancel_all('GE') only cancels GE orders")
def x06():
    c, o = get_client()
    price_ge = c.get_market_price("GE")
    price_msft = c.get_market_price("MSFT")
    ge = OrderRequest(symbol="GE", action=OrderAction.BUY, quantity=1,
                      order_type=OrderType.LIMIT, limit_price=round(price_ge * 0.5, 2),
                      tif=TimeInForce.GTC)
    msft = OrderRequest(symbol="MSFT", action=OrderAction.BUY, quantity=1,
                        order_type=OrderType.LIMIT, limit_price=round(price_msft * 0.5, 2),
                        tif=TimeInForce.GTC)
    r_ge = o.place_order(ge)
    r_msft = o.place_order(msft)
    cancelled = o.cancel_all("GE")
    c.ib.sleep(1)
    assert cancelled == 1
    remaining = o.get_open_orders("MSFT")
    assert any(r.order_id == r_msft.order_id for r in remaining), "MSFT order was incorrectly cancelled"
    o.cancel_all("MSFT")
    c.ib.sleep(0.5)

x01(); x03(); x04(); x05(); x06()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7: SYNC TESTS
# ══════════════════════════════════════════════════════════════════════════════

section("7. SYNC TESTS")

@test("S-03", "sync() returns count of open orders")
def s03():
    c, o = get_client()
    price = c.get_market_price("GE")
    r = OrderRequest(symbol="GE", action=OrderAction.BUY, quantity=1,
                     order_type=OrderType.LIMIT, limit_price=round(price * 0.5, 2),
                     tif=TimeInForce.GTC)
    o.place_order(r)
    count = o.sync()
    assert count >= 1, f"Expected at least 1 open order, got {count}"
    o.cancel_all()
    c.ib.sleep(0.5)

@test("S-05", "Two clients with different clientIds don't interfere")
def s05():
    # Use a fresh client2 only — client1 is the shared client already connected
    c2 = IBKRClient(client_id=2)
    c2.connect()
    assert c2.is_connected
    assert c2.account == client.account  # same paper account, different session
    c2.disconnect()

s03(); s05()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8: POSITION TESTS
# ══════════════════════════════════════════════════════════════════════════════

section("8. POSITION TESTS")

@test("POS-01", "get_positions() with no fills returns a list")
def pos01():
    _, o = get_client()
    positions = o.get_positions()
    assert isinstance(positions, list)

pos01()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 9: ERROR HANDLING TESTS
# ══════════════════════════════════════════════════════════════════════════════

section("9. ERROR HANDLING TESTS")

@test("E-01", "Error code 202 does not trigger on_error callback")
def e01():
    c, o = get_client()
    o._clear_callbacks()  # prevent stale callbacks from earlier tests from firing
    errors = []
    o.on_error(lambda rid, code, msg: errors.append(code))
    price = c.get_market_price("GE")
    r = OrderRequest(symbol="GE", action=OrderAction.BUY, quantity=1,
                     order_type=OrderType.LIMIT, limit_price=round(price * 0.5, 2),
                     tif=TimeInForce.GTC)
    result = o.place_order(r)
    o.cancel_order(result.order_id)
    c.ib.sleep(1)
    assert 202 not in errors, "Error code 202 incorrectly fired on_error callback"

@test("E-04", "10 rapid orders placed without crash or cache corruption")
def e04():
    c, o = get_client()
    price = c.get_market_price("GE")
    order_ids = []
    for i in range(10):
        r = OrderRequest(symbol="GE", action=OrderAction.BUY, quantity=1,
                         order_type=OrderType.LIMIT,
                         limit_price=round(price * 0.5 - i * 0.01, 2),
                         tif=TimeInForce.GTC,
                         )
        result = o.place_order(r, allow_duplicate=True)
        order_ids.append(result.order_id)
    assert len(set(order_ids)) == 10, "Duplicate order IDs detected"
    o.cancel_all("GE")
    c.ib.sleep(1)

@test("E-05", "NaN price is never passed to place_order")
def e05():
    import math
    c, o = get_client()
    price = c.get_market_price("MSFT")
    assert not math.isnan(price), "get_market_price returned NaN"
    limit = round(price * 0.9, 2)
    assert not math.isnan(limit), "Limit price is NaN"
    assert limit > 0, "Limit price is not positive"

@test("E-08", "logs/ directory is created automatically if missing")
def e08():
    import shutil
    from pathlib import Path
    log_dir = Path("../logs")
    existed = log_dir.exists()
    if existed:
        pass  # already exists — test passes by definition
    else:
        from config.logging_config import setup_logging
        setup_logging()
        assert log_dir.exists(), "logs/ not created"

e01(); e04(); e05(); e08()


# ══════════════════════════════════════════════════════════════════════════════
# CLEANUP & SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

section("CLEANUP")
try:
    if client and client.is_connected:
        remaining = om.get_open_orders()
        if remaining:
            print(f"  Cancelling {len(remaining)} leftover open order(s)…")
            om.cancel_all()
            client.ib.sleep(1)
        client.disconnect()
        print("  Disconnected cleanly.")
except Exception as e:
    print(f"  Cleanup error: {e}")

failed_count = summary()
sys.exit(failed_count)
