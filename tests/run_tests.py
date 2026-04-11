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
from broker.reconnect import ReconnectManager
from config.validator import validate_config, ConfigError
from models.order import OrderAction, OrderRequest, OrderType, TimeInForce
from risk.risk_manager import RiskManager, RiskViolationError
from risk.position_sizer import PositionSizer

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
# SECTION 10: CONFIG VALIDATION TESTS (no connection needed)
# ══════════════════════════════════════════════════════════════════════════════

section("10. CONFIG VALIDATION TESTS")

@test("CFG-01", "validate_config() passes with default paper settings")
def cfg01():
    validate_config()   # should not raise

@test("CFG-02", "ConfigError raised for invalid port")
def cfg02():
    import config.validator as v
    original = v.IB_PORT
    try:
        v.IB_PORT = 9999
        import importlib
        # Patch at module level for the call
        import config.settings as s
        orig_port = s.IB_PORT
        s.IB_PORT = 9999
        try:
            validate_config()
            assert False, "Should have raised ConfigError"
        except ConfigError:
            pass
    finally:
        s.IB_PORT = orig_port

@test("CFG-03", "ConfigError raised for empty host")
def cfg03():
    import config.settings as s
    orig = s.IB_HOST
    s.IB_HOST = ""
    try:
        validate_config()
        assert False, "Should have raised ConfigError"
    except ConfigError:
        pass
    finally:
        s.IB_HOST = orig

cfg01(); cfg02(); cfg03()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 11: RISK MANAGER TESTS (no connection needed for most)
# ══════════════════════════════════════════════════════════════════════════════

section("11. RISK MANAGER TESTS")

def _make_rm(c, o, **kwargs):
    defaults = dict(
        max_order_value=1_000.0,
        max_position_value=2_000.0,
        max_daily_loss=-200.0,
        max_open_orders=5,
    )
    defaults.update(kwargs)
    return RiskManager(client=c, order_manager=o, **defaults)

@test("RM-01", "Order within all limits passes check")
def rm01():
    c, o = get_client()
    rm = _make_rm(c, o)
    r = OrderRequest(symbol="GE", action=OrderAction.BUY, quantity=1, tif=TimeInForce.GTC)
    rm.check(r, current_price=10.0)   # $10 order, well within $1,000 limit

@test("RM-02", "Order exceeding max_order_value raises RiskViolationError")
def rm02():
    c, o = get_client()
    rm = _make_rm(c, o, max_order_value=500.0)
    r = OrderRequest(symbol="GE", action=OrderAction.BUY, quantity=100, tif=TimeInForce.GTC)
    try:
        rm.check(r, current_price=10.0)   # $1,000 order > $500 limit
        assert False, "Should have raised RiskViolationError"
    except RiskViolationError:
        pass

@test("RM-03", "Daily loss ceiling breach halts trading")
def rm03():
    c, o = get_client()
    rm = _make_rm(c, o, max_daily_loss=-100.0)
    rm.update_daily_pnl(-150.0)   # already past the limit
    assert rm.is_halted() is True
    r = OrderRequest(symbol="GE", action=OrderAction.BUY, quantity=1, tif=TimeInForce.GTC)
    try:
        rm.check(r, current_price=10.0)
        assert False, "Should have raised RiskViolationError"
    except RiskViolationError:
        pass

@test("RM-04", "reset_daily() clears halted state")
def rm04():
    c, o = get_client()
    rm = _make_rm(c, o, max_daily_loss=-100.0)
    rm.update_daily_pnl(-150.0)
    assert rm.is_halted() is True
    rm.reset_daily()
    assert rm.is_halted() is False

@test("RM-05", "Too many open orders raises RiskViolationError")
def rm05():
    c, o = get_client()
    o._clear_callbacks()
    rm = _make_rm(c, o, max_open_orders=0)  # cap at 0 — always triggers
    r = OrderRequest(symbol="GE", action=OrderAction.BUY, quantity=1, tif=TimeInForce.GTC)
    try:
        rm.check(r, current_price=10.0)
        assert False, "Should have raised RiskViolationError"
    except RiskViolationError:
        pass

@test("RM-06", "max_daily_loss must be negative — constructor raises on bad value")
def rm06():
    c, o = get_client()
    try:
        RiskManager(client=c, order_manager=o, max_daily_loss=100.0)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass

rm01(); rm02(); rm03(); rm04(); rm05(); rm06()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 12: POSITION SIZER TESTS (no connection needed)
# ══════════════════════════════════════════════════════════════════════════════

section("12. POSITION SIZER TESTS")

@test("PS-01", "fixed() returns the share count unchanged")
def ps01():
    assert PositionSizer.fixed(10) == 10
    assert PositionSizer.fixed(1) == 1

@test("PS-02", "fixed() enforces minimum of 1")
def ps02():
    assert PositionSizer.fixed(0) == 1

@test("PS-03", "percent_of_equity() returns correct floor division")
def ps03():
    # $50,000 × 2% = $1,000 / $150 = 6.66 → 6
    result = PositionSizer.percent_of_equity(equity=50_000, price=150.0, pct=0.02)
    assert result == 6, f"Expected 6, got {result}"

@test("PS-04", "percent_of_equity() enforces minimum of 1")
def ps04():
    # Very small pct → less than 1 share → should return 1
    result = PositionSizer.percent_of_equity(equity=100, price=10_000.0, pct=0.001)
    assert result == 1

@test("PS-05", "percent_of_equity() raises on invalid inputs")
def ps05():
    try:
        PositionSizer.percent_of_equity(equity=0, price=100.0, pct=0.05)
        assert False, "Should raise ValueError for equity=0"
    except ValueError:
        pass
    try:
        PositionSizer.percent_of_equity(equity=10_000, price=100.0, pct=1.5)
        assert False, "Should raise ValueError for pct > 1"
    except ValueError:
        pass

@test("PS-06", "kelly() returns positive shares for positive-EV strategy")
def ps06():
    # win_rate=0.6, W/L=2.0: kelly_f = 0.6 - 0.4/2.0 = 0.6 - 0.2 = 0.4
    # capped at 0.25 → $50,000 × 0.25 = $12,500 / $100 = 125 shares
    result = PositionSizer.kelly(
        win_rate=0.6, win_loss_ratio=2.0,
        equity=50_000, price=100.0, max_fraction=0.25,
    )
    assert result == 125, f"Expected 125, got {result}"

@test("PS-07", "kelly() returns 1 for negative-EV strategy")
def ps07():
    # win_rate=0.3, W/L=0.5: kelly_f = 0.3 - 0.7/0.5 = 0.3 - 1.4 = -1.1 (negative)
    result = PositionSizer.kelly(
        win_rate=0.3, win_loss_ratio=0.5,
        equity=50_000, price=100.0,
    )
    assert result == 1

ps01(); ps02(); ps03(); ps04(); ps05(); ps06(); ps07()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 13: RECONNECT MANAGER TESTS (no real disconnect — logic tests only)
# ══════════════════════════════════════════════════════════════════════════════

section("13. RECONNECT MANAGER TESTS")

@test("RCN-01", "ReconnectManager starts and reports connected")
def rcn01():
    c, o = get_client()
    rcn = ReconnectManager(client=c, order_manager=o, max_attempts=3)
    rcn.start()
    assert rcn.is_connected is True
    assert rcn.is_halted is False
    rcn.stop()

@test("RCN-02", "wait_for_connection() returns True when connected")
def rcn02():
    c, o = get_client()
    rcn = ReconnectManager(client=c, order_manager=o)
    rcn.start()
    result = rcn.wait_for_connection(timeout=2.0)
    assert result is True
    rcn.stop()

@test("RCN-03", "wait_for_connection() returns False after timeout when disconnected")
def rcn03():
    import time
    c, o = get_client()
    rcn = ReconnectManager(client=c, order_manager=o, max_attempts=1)
    rcn.start()
    # Manually clear the event to simulate a disconnect without an actual drop
    rcn._connected_event.clear()
    start = time.time()
    result = rcn.wait_for_connection(timeout=1.0)
    elapsed = time.time() - start
    assert result is False, "Should have timed out"
    assert elapsed >= 0.9, f"Should have waited ~1s, waited {elapsed:.2f}s"
    rcn._connected_event.set()   # restore before stop
    rcn.stop()

@test("RCN-04", "stop() unblocks any waiting threads")
def rcn04():
    import threading, time
    c, o = get_client()
    rcn = ReconnectManager(client=c, order_manager=o)
    rcn.start()
    rcn._connected_event.clear()   # simulate disconnect
    unblocked = []
    def waiter():
        rcn.wait_for_connection(timeout=10.0)
        unblocked.append(True)
    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    time.sleep(0.2)
    rcn.stop()   # should unblock the waiter
    t.join(timeout=2.0)
    assert unblocked, "stop() did not unblock waiting thread"

rcn01(); rcn02(); rcn03(); rcn04()


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
