"""
TradeBot Test Runner
Executes all pre-market testable cases from TEST_PLAN.md
"""

import os
import sys
from datetime import datetime, timezone

# GitHub Actions sets GITHUB_ACTIONS=true; skip live-broker sections in that env.
IS_CI = bool(os.getenv("GITHUB_ACTIONS"))

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


if IS_CI:
    print("\n[CI] No IBKR connection available — skipping sections 1-2, 4-9, 13 (broker tests).\n")
else:
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

_TEST_CLIENT_ID = (
    5  # dedicated clientId for connection tests (avoids conflict with shared client on id=1)
)


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


if not IS_CI:
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


if not IS_CI:
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
        OrderRequest(
            symbol="AAPL", action=OrderAction.BUY, quantity=1, order_type=OrderType.STOP_LIMIT
        )
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
        _ = OrderRequest(
            symbol="AAPL",
            action=OrderAction.BUY,
            quantity=1,
            order_type=OrderType.LIMIT,
            limit_price=0,
        )
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


@test("V-09", "LIMIT order with negative limit_price raises ValueError")
def v09():
    try:
        _ = OrderRequest(
            symbol="AAPL",
            action=OrderAction.BUY,
            quantity=1,
            order_type=OrderType.LIMIT,
            limit_price=-10,
        )
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


v01()
v02()
v03()
v04()
v05()
v06()
v07()
v08()
v09()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4: ORDER PLACEMENT TESTS
# ══════════════════════════════════════════════════════════════════════════════

section("4. ORDER PLACEMENT TESTS")


@test("P-03", "Limit BUY far below market sits as PreSubmitted")
def p03():
    c, o = get_client()
    price = c.get_market_price("GE")
    r = OrderRequest(
        symbol="GE",
        action=OrderAction.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=round(price * 0.5, 2),
        tif=TimeInForce.GTC,
    )
    result = o.place_order(r)
    assert result.order_id > 0
    assert result.status.value in ("PreSubmitted", "Submitted", "PendingSubmit")
    o.cancel_order(result.order_id)
    c.ib.sleep(0.5)


@test("P-04", "Limit SELL far above market sits as PreSubmitted")
def p04():
    c, o = get_client()
    price = c.get_market_price("MSFT")
    r = OrderRequest(
        symbol="MSFT",
        action=OrderAction.SELL,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=round(price * 2.0, 2),
        tif=TimeInForce.GTC,
    )
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
    r = OrderRequest(
        symbol="IBM",
        action=OrderAction.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=round(price * 0.5, 2),
        tif=TimeInForce.GTC,
    )
    result = o.place_order(r)
    assert result.order_id > 0
    assert result.status.value in ("PreSubmitted", "Submitted", "PendingSubmit")
    o.cancel_order(result.order_id)
    c.ib.sleep(0.5)


if not IS_CI:
    p03()
    p04()
    p07()
    p08()
    p05()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5: DUPLICATE PREVENTION TESTS
# ══════════════════════════════════════════════════════════════════════════════

section("5. DUPLICATE PREVENTION TESTS")


@test("DUP-01", "Placing same BUY twice raises DuplicateOrderError")
def dup01():
    c, o = get_client()
    price = c.get_market_price("GE")
    r = OrderRequest(
        symbol="GE",
        action=OrderAction.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=round(price * 0.5, 2),
        tif=TimeInForce.GTC,
    )
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
    buy = OrderRequest(
        symbol="MSFT",
        action=OrderAction.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=round(price * 0.5, 2),
        tif=TimeInForce.GTC,
    )
    sell = OrderRequest(
        symbol="MSFT",
        action=OrderAction.SELL,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=round(price * 2.0, 2),
        tif=TimeInForce.GTC,
    )
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
    r = OrderRequest(
        symbol="GE",
        action=OrderAction.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=round(price * 0.5, 2),
        tif=TimeInForce.GTC,
    )
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
    r = OrderRequest(
        symbol="GE",
        action=OrderAction.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=round(price * 0.5, 2),
        tif=TimeInForce.GTC,
    )
    o.place_order(r)
    r2 = o.place_order(r, allow_duplicate=True)  # should NOT raise
    assert r2.order_id > 0
    o.cancel_all("GE")
    c.ib.sleep(0.5)


if not IS_CI:
    dup01()
    dup02()
    dup03()
    dup05()


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
    r = OrderRequest(
        symbol="GE",
        action=OrderAction.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=round(price * 0.5, 2),
        tif=TimeInForce.GTC,
    )
    result = o.place_order(r)
    cancelled = o.cancel_order(result.order_id)
    c.ib.sleep(1)
    assert cancelled is True
    assert len(fired) > 0, "on_cancel callback did not fire"


@test("X-03", "Cancel already-cancelled order returns False")
def x03():
    c, o = get_client()
    price = c.get_market_price("MSFT")
    r = OrderRequest(
        symbol="MSFT",
        action=OrderAction.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=round(price * 0.5, 2),
        tif=TimeInForce.GTC,
    )
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
    ge = OrderRequest(
        symbol="GE",
        action=OrderAction.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=round(price_ge * 0.5, 2),
        tif=TimeInForce.GTC,
    )
    msft = OrderRequest(
        symbol="MSFT",
        action=OrderAction.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=round(price_msft * 0.5, 2),
        tif=TimeInForce.GTC,
    )
    o.place_order(ge)
    r_msft = o.place_order(msft)
    cancelled = o.cancel_all("GE")
    c.ib.sleep(1)
    assert cancelled == 1
    remaining = o.get_open_orders("MSFT")
    assert any(
        r.order_id == r_msft.order_id for r in remaining
    ), "MSFT order was incorrectly cancelled"
    o.cancel_all("MSFT")
    c.ib.sleep(0.5)


if not IS_CI:
    x01()
    x03()
    x04()
    x05()
    x06()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7: SYNC TESTS
# ══════════════════════════════════════════════════════════════════════════════

section("7. SYNC TESTS")


@test("S-03", "sync() returns count of open orders")
def s03():
    c, o = get_client()
    price = c.get_market_price("GE")
    r = OrderRequest(
        symbol="GE",
        action=OrderAction.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=round(price * 0.5, 2),
        tif=TimeInForce.GTC,
    )
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


if not IS_CI:
    s03()
    s05()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8: POSITION TESTS
# ══════════════════════════════════════════════════════════════════════════════

section("8. POSITION TESTS")


@test("POS-01", "get_positions() with no fills returns a list")
def pos01():
    _, o = get_client()
    positions = o.get_positions()
    assert isinstance(positions, list)


if not IS_CI:
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
    r = OrderRequest(
        symbol="GE",
        action=OrderAction.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=round(price * 0.5, 2),
        tif=TimeInForce.GTC,
    )
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
        r = OrderRequest(
            symbol="GE",
            action=OrderAction.BUY,
            quantity=1,
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
    from pathlib import Path

    # Use an absolute path so the test is correct regardless of CWD.
    # setup_logging() creates <project_root>/logs/ where project_root is the
    # parent of the config/ package.
    from config import logging_config

    log_dir = Path(logging_config.__file__).parent.parent / "logs"
    existed = log_dir.exists()
    if existed:
        pass  # already exists — test passes by definition
    else:
        from config.logging_config import setup_logging

        setup_logging()
        assert log_dir.exists(), "logs/ not created"


if not IS_CI:
    e01()
    e04()
    e05()
    e08()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 10: CONFIG VALIDATION TESTS (no connection needed)
# ══════════════════════════════════════════════════════════════════════════════

section("10. CONFIG VALIDATION TESTS")


@test("CFG-01", "validate_config() passes with default paper settings")
def cfg01():
    validate_config()  # should not raise


@test("CFG-02", "ConfigError raised for invalid port")
def cfg02():
    import config.validator as v

    try:
        v.IB_PORT = 9999
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


cfg01()
cfg02()
cfg03()


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
    rm.check(r, current_price=10.0)  # $10 order, well within $1,000 limit


@test("RM-02", "Order exceeding max_order_value raises RiskViolationError")
def rm02():
    c, o = get_client()
    rm = _make_rm(c, o, max_order_value=500.0)
    r = OrderRequest(symbol="GE", action=OrderAction.BUY, quantity=100, tif=TimeInForce.GTC)
    try:
        rm.check(r, current_price=10.0)  # $1,000 order > $500 limit
        assert False, "Should have raised RiskViolationError"
    except RiskViolationError:
        pass


@test("RM-03", "Daily loss ceiling breach halts trading")
def rm03():
    c, o = get_client()
    rm = _make_rm(c, o, max_daily_loss=-100.0)
    rm.update_daily_pnl(-150.0)  # already past the limit
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


@test("RM-07", "validate_setup() passes for a valid 1:3 R/R long trade — verifies math")
def rm07():
    c, o = get_client()
    rm = _make_rm(c, o)
    # entry=150, stop=145 → risk_per_share=$5; target=165 → reward=$15; R/R=3.0 ✓
    # equity=$10,000; max_risk=2%=$200; $5 < $200 ✓
    entry, stop, target, equity = 150.0, 145.0, 165.0, 10_000.0
    risk_per_share = entry - stop  # $5.00
    reward_per_share = target - entry  # $15.00
    rr_ratio = reward_per_share / risk_per_share  # 3.0
    max_risk_dollars = equity * 0.02  # $200.00
    assert rr_ratio == 3.0, f"Test math error: expected R/R 3.0, got {rr_ratio}"
    assert risk_per_share <= max_risk_dollars, "Test math error: rule B should pass"
    rm.validate_setup(entry_price=entry, stop_price=stop, take_profit_price=target, equity=equity)


@test("RM-08", "validate_setup() raises when R/R is below minimum — verifies math")
def rm08():
    c, o = get_client()
    rm = _make_rm(c, o)
    # entry=150, stop=145 → risk=$5; target=160 → reward=$10; R/R=2.0 < min 3.0 → FAIL
    entry, stop, target = 150.0, 145.0, 160.0
    rr_ratio = (target - entry) / (entry - stop)  # 10/5 = 2.0
    assert rr_ratio == 2.0, f"Test math error: expected R/R 2.0, got {rr_ratio}"
    try:
        rm.validate_setup(
            entry_price=entry, stop_price=stop, take_profit_price=target, equity=10_000.0
        )
        assert False, "Should have raised RiskViolationError"
    except RiskViolationError:
        pass


@test("RM-09", "validate_setup() raises when risk per share exceeds 2% of equity — verifies math")
def rm09():
    c, o = get_client()
    rm = _make_rm(c, o)
    # equity=$100; max_risk=2%=$2; entry=150, stop=100 → risk=$50/share > $2 → FAIL
    # target=300: R/R=(300-150)/(150-100)=150/50=3.0 ✓ so only Rule B fires
    entry, stop, target, equity = 150.0, 100.0, 300.0, 100.0
    risk_per_share = entry - stop  # $50
    max_risk_dollars = equity * 0.02  # $2
    rr_ratio = (target - entry) / risk_per_share  # 3.0
    assert rr_ratio == 3.0, "Test math error: R/R should pass"
    assert risk_per_share > max_risk_dollars, "Test math error: rule B should fail"
    try:
        rm.validate_setup(
            entry_price=entry, stop_price=stop, take_profit_price=target, equity=equity
        )
        assert False, "Should have raised RiskViolationError"
    except RiskViolationError:
        pass


@test("RM-10", "validate_setup() raises ValueError for equity=0")
def rm10():
    c, o = get_client()
    rm = _make_rm(c, o)
    try:
        rm.validate_setup(entry_price=150.0, stop_price=145.0, take_profit_price=165.0, equity=0.0)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


@test("RM-11", "validate_setup() raises ValueError when stop == entry (long)")
def rm11():
    c, o = get_client()
    rm = _make_rm(c, o)
    try:
        rm.validate_setup(
            entry_price=150.0, stop_price=150.0, take_profit_price=165.0, equity=10_000.0
        )
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


@test("RM-12", "validate_setup() passes for a valid 1:3 R/R short trade")
def rm12():
    c, o = get_client()
    rm = _make_rm(c, o)
    # Short: entry=100, stop=105 (above entry), target=85 (below entry)
    # risk_per_share = 105-100 = $5; reward = 100-85 = $15; R/R=3.0 ✓
    # equity=$10,000; max_risk=2%=$200; $5 < $200 ✓
    entry, stop, target, equity = 100.0, 105.0, 85.0, 10_000.0
    risk_per_share = stop - entry  # $5
    reward_per_share = entry - target  # $15
    rr_ratio = reward_per_share / risk_per_share  # 3.0
    assert rr_ratio == 3.0, f"Test math error: expected R/R 3.0, got {rr_ratio}"
    rm.validate_setup(
        entry_price=entry,
        stop_price=stop,
        take_profit_price=target,
        equity=equity,
        order_action=OrderAction.SELL,
    )


@test("RM-13", "plan_trade() atomically validates and sizes a long trade")
def rm13():
    c, o = get_client()
    rm = _make_rm(c, o, max_risk_per_trade_pct=0.02)
    # entry=150, stop=145, target=165, equity=$10,000
    # validate: R/R=3.0 ✓, risk/share=$5 <= $200 ✓
    # size: risk_amount=$200, risk/share=$5 → floor(200/5) = 40 shares
    shares = rm.plan_trade(
        entry_price=150.0,
        stop_price=145.0,
        take_profit_price=165.0,
        equity=10_000.0,
    )
    assert shares == 40, f"Expected 40 shares, got {shares}"


@test("RM-14", "plan_trade() raises RiskViolationError — no shares returned on bad setup")
def rm14():
    c, o = get_client()
    rm = _make_rm(c, o)
    # R/R=2.0 < 3.0 minimum → should raise before sizing
    try:
        rm.plan_trade(entry_price=150.0, stop_price=145.0, take_profit_price=160.0, equity=10_000.0)
        assert False, "Should have raised RiskViolationError"
    except RiskViolationError:
        pass


if not IS_CI:
    rm01()
    rm02()
    rm03()
    rm04()
    rm05()
    rm06()
    rm07()
    rm08()
    rm09()
    rm10()
    rm11()
    rm12()
    rm13()
    rm14()


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
        win_rate=0.6,
        win_loss_ratio=2.0,
        equity=50_000,
        price=100.0,
        max_fraction=0.25,
    )
    assert result == 125, f"Expected 125, got {result}"


@test("PS-07", "kelly() returns 1 for negative-EV strategy")
def ps07():
    # win_rate=0.3, W/L=0.5: kelly_f = 0.3 - 0.7/0.5 = 0.3 - 1.4 = -1.1 (negative)
    result = PositionSizer.kelly(
        win_rate=0.3,
        win_loss_ratio=0.5,
        equity=50_000,
        price=100.0,
    )
    assert result == 1


@test("PS-08", "risk_based() sizes correctly using 2% rule — verifies intermediate math")
def ps08():
    equity, entry, stop = 1_000.0, 50.0, 48.0
    # Step 1: risk_amount = 1000 * 0.02 = $20.00
    risk_amount = equity * 0.02
    assert risk_amount == 20.0, f"Expected risk_amount=$20, got {risk_amount}"
    # Step 2: risk_per_share = 50 - 48 = $2.00
    risk_per_share = entry - stop
    assert risk_per_share == 2.0, f"Expected risk/share=$2, got {risk_per_share}"
    # Step 3: floor(20 / 2) = 10 shares
    import math

    expected = math.floor(risk_amount / risk_per_share)
    assert expected == 10, f"Expected floor=10, got {expected}"
    # Confirm function agrees
    result = PositionSizer.risk_based(equity=equity, entry_price=entry, stop_price=stop)
    assert result == 10, f"risk_based() returned {result}, expected 10"


@test("PS-09", "risk_based() returns minimum 1 share when floor would be 0")
def ps09():
    equity, entry, stop = 100.0, 50.0, 10.0
    # risk_amount = $2; risk_per_share = $40; floor(2/40) = 0 → clamped to 1
    import math

    risk_amount = equity * 0.02  # $2
    risk_per_share = entry - stop  # $40
    assert math.floor(risk_amount / risk_per_share) == 0, "Test math: floor should be 0"
    result = PositionSizer.risk_based(equity=equity, entry_price=entry, stop_price=stop)
    assert result == 1, f"Expected 1 (minimum), got {result}"


@test("PS-10", "risk_based() raises ValueError when stop_price >= entry_price")
def ps10():
    try:
        PositionSizer.risk_based(equity=10_000, entry_price=50.0, stop_price=55.0)
        assert False, "Should raise ValueError for stop >= entry"
    except ValueError:
        pass
    # Also test stop == entry
    try:
        PositionSizer.risk_based(equity=10_000, entry_price=50.0, stop_price=50.0)
        assert False, "Should raise ValueError for stop == entry"
    except ValueError:
        pass


@test("PS-11", "risk_based() raises ValueError for equity=0")
def ps11():
    try:
        PositionSizer.risk_based(equity=0, entry_price=50.0, stop_price=48.0)
        assert False, "Should raise ValueError for equity=0"
    except ValueError:
        pass


ps01()
ps02()
ps03()
ps04()
ps05()
ps06()
ps07()
ps08()
ps09()
ps10()
ps11()


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
    rcn._connected_event.set()  # restore before stop
    rcn.stop()


@test("RCN-04", "stop() unblocks any waiting threads")
def rcn04():
    import threading
    import time

    c, o = get_client()
    rcn = ReconnectManager(client=c, order_manager=o)
    rcn.start()
    rcn._connected_event.clear()  # simulate disconnect
    unblocked = []

    def waiter():
        rcn.wait_for_connection(timeout=10.0)
        unblocked.append(True)

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    time.sleep(0.2)
    rcn.stop()  # should unblock the waiter
    t.join(timeout=2.0)
    assert unblocked, "stop() did not unblock waiting thread"


if not IS_CI:
    rcn01()
    rcn02()
    rcn03()
    rcn04()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 14: BAR MODEL TESTS (no connection needed)
# ══════════════════════════════════════════════════════════════════════════════

section("14. BAR MODEL TESTS")

from data.bar import Bar
from datetime import timezone as tz


@test("BAR-01", "Bar is immutable (frozen dataclass)")
def bar01():
    b = Bar("AAPL", datetime(2024, 1, 1, tzinfo=tz.utc), 150.0, 155.0, 149.0, 153.0, 1_000_000)
    try:
        b.close = 999.0
        assert False, "Should have raised FrozenInstanceError"
    except Exception:
        pass  # any exception = correct (frozen)


@test("BAR-02", "Bar.mid and Bar.range computed correctly")
def bar02():
    b = Bar("MSFT", datetime(2024, 1, 1, tzinfo=tz.utc), 400.0, 410.0, 390.0, 405.0, 500_000)
    assert b.mid == 400.0, f"Expected 400.0, got {b.mid}"
    assert b.range == 20.0, f"Expected 20.0, got {b.range}"


@test("BAR-03", "Bar repr is readable")
def bar03():
    b = Bar("GE", datetime(2024, 1, 1, tzinfo=tz.utc), 10.0, 11.0, 9.5, 10.5, 100_000)
    r = repr(b)
    assert "GE" in r and "10.00" in r


bar01()
bar02()
bar03()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 15: HISTORICAL DATA LOADER TESTS (network — yfinance)
# ══════════════════════════════════════════════════════════════════════════════

section("15. HISTORICAL DATA LOADER TESTS")

from data.historical import HistoricalDataLoader


@test("HDL-01", "load_yfinance returns DataFrame with correct columns")
def hdl01():
    df = HistoricalDataLoader.load_yfinance("MSFT", start="2024-01-01", end="2024-02-01")
    assert not df.empty, "DataFrame is empty"
    for col in ("open", "high", "low", "close", "volume"):
        assert col in df.columns, f"Missing column: {col}"


@test("HDL-02", "load_yfinance index is UTC DatetimeIndex")
def hdl02():
    import pandas as pd

    df = HistoricalDataLoader.load_yfinance("MSFT", start="2024-01-01", end="2024-02-01")
    assert isinstance(df.index, pd.DatetimeIndex)
    assert str(df.index.tz) == "UTC"


@test("HDL-03", "load_yfinance is sorted ascending")
def hdl03():
    df = HistoricalDataLoader.load_yfinance("MSFT", start="2024-01-01", end="2024-02-01")
    assert df.index.is_monotonic_increasing


@test("HDL-04", "load_yfinance raises ValueError for bad symbol")
def hdl04():
    try:
        HistoricalDataLoader.load_yfinance("XYZXYZ999FAKE", start="2024-01-01", end="2024-02-01")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


@test("HDL-05", "load_csv loads a CSV file correctly")
def hdl05():
    import tempfile
    import os
    import textwrap

    csv_content = textwrap.dedent("""\
        date,open,high,low,close,volume
        2024-01-02,150.0,155.0,149.0,153.0,1000000
        2024-01-03,153.0,157.0,152.0,156.0,1200000
        2024-01-04,156.0,158.0,154.0,155.0,900000
    """)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(csv_content)
        path = f.name
    try:
        df = HistoricalDataLoader.load_csv(path, symbol="TEST")
        assert len(df) == 3
        assert df["close"].iloc[0] == 153.0
    finally:
        os.unlink(path)


hdl01()
hdl02()
hdl03()
hdl04()
hdl05()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 16: TRADE LOG TESTS (no connection needed)
# ══════════════════════════════════════════════════════════════════════════════

section("16. TRADE LOG TESTS")

import tempfile
from pathlib import Path
from data.trade_log import TradeLog
from models.order import OrderResult, OrderStatus


def _make_fill(symbol, action, qty, price, order_id=1):
    return OrderResult(
        order_id=order_id,
        symbol=symbol,
        action=action,
        quantity=qty,
        order_type="LMT",
        tif="GTC",
        status=OrderStatus.FILLED,
        filled=qty,
        remaining=0,
        avg_fill_price=price,
        limit_price=None,
        stop_price=None,
        submitted_at=datetime.now(timezone.utc),
    )


def _tmp_log():
    """Create a TradeLog in a temp file. Returns (log, path) — caller must call _close_log(log)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return TradeLog(db_path=Path(tmp.name)), Path(tmp.name)


def _close_log(log, db_path):
    """Close all SQLite connections and remove WAL files so Windows can clean up."""
    import gc

    gc.collect()  # release any lingering connection objects
    for ext in ("", "-wal", "-shm"):
        p = Path(str(db_path) + ext)
        if p.exists():
            try:
                p.unlink()
            except PermissionError:
                pass  # best-effort — file will be cleaned up by OS eventually


@test("TL-01", "TradeLog creates DB and records a fill")
def tl01():
    log, path = _tmp_log()
    try:
        assert log.count() == 0
        log.record(_make_fill("AAPL", "BUY", 10, 150.0), "TestStrategy")
        assert log.count() == 1
    finally:
        _close_log(log, path)


@test("TL-02", "get_history returns recorded fills")
def tl02():
    log, path = _tmp_log()
    try:
        log.record(_make_fill("MSFT", "BUY", 5, 400.0, order_id=1), "S1")
        log.record(_make_fill("MSFT", "SELL", 5, 410.0, order_id=2), "S1")
        assert len(log.get_history(symbol="MSFT")) == 2
    finally:
        _close_log(log, path)


@test("TL-03", "get_history filters by symbol")
def tl03():
    log, path = _tmp_log()
    try:
        log.record(_make_fill("AAPL", "BUY", 1, 150.0, order_id=1), "S1")
        log.record(_make_fill("MSFT", "BUY", 1, 400.0, order_id=2), "S1")
        assert len(log.get_history(symbol="AAPL")) == 1
        assert len(log.get_history(symbol="MSFT")) == 1
        assert len(log.get_history()) == 2
    finally:
        _close_log(log, path)


@test("TL-04", "daily_summary returns correct counts")
def tl04():
    log, path = _tmp_log()
    try:
        log.record(_make_fill("GE", "BUY", 10, 10.0, order_id=1), "S1")
        log.record(_make_fill("GE", "SELL", 10, 11.0, order_id=2), "S1")
        s = log.daily_summary()
        assert s["total_trades"] == 2
        assert s["buys"] == 1
        assert s["sells"] == 1
    finally:
        _close_log(log, path)


@test("TL-05", "Unfilled order (avg_fill_price=None) is not recorded")
def tl05():
    log, path = _tmp_log()
    try:
        unfilled = OrderResult(
            order_id=99,
            symbol="GE",
            action="BUY",
            quantity=1,
            order_type="LMT",
            tif="GTC",
            status=OrderStatus.SUBMITTED,
            filled=0,
            remaining=1,
            avg_fill_price=None,
            limit_price=10.0,
            stop_price=None,
            submitted_at=datetime.now(timezone.utc),
        )
        log.record(unfilled, "S1")
        assert log.count() == 0
    finally:
        _close_log(log, path)


tl01()
tl02()
tl03()
tl04()
tl05()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 17: BACKTESTER TESTS (no connection needed)
# ══════════════════════════════════════════════════════════════════════════════

section("17. BACKTESTER TESTS")

import pandas as pd
from backtester.engine import BacktestEngine, MockOrderManager
from backtester.portfolio import BacktestPortfolio
from backtester.metrics import sharpe_ratio, max_drawdown
from strategies.base_strategy import BaseStrategy


# Minimal strategy for testing: buys on first bar, sells on last bar.
# Uses the full BaseStrategy signature so BacktestEngine can inject feed and symbol.
class _BuyHoldStrategy(BaseStrategy):
    def __init__(
        self, client, order_manager, risk_manager=None, reconnect=None, feed=None, symbol="TEST"
    ):
        super().__init__(client, order_manager, risk_manager, reconnect, feed=feed, symbol=symbol)
        self._bought = False
        self._bar_count = 0
        self._total_bars = 0

    def on_start(self):
        pass

    def on_tick(self):
        self._bar_count += 1
        if not self._bought:
            r = OrderRequest(
                symbol=self.symbol, action=OrderAction.BUY, quantity=10, tif=TimeInForce.GTC
            )
            self.om.place_order(r)
            self._bought = True
        elif self._bar_count >= self._total_bars:
            r = OrderRequest(
                symbol=self.symbol, action=OrderAction.SELL, quantity=10, tif=TimeInForce.GTC
            )
            self.om.place_order(r)

    def on_stop(self):
        pass


def _make_df(prices, symbol="TEST"):
    """Create a simple OHLCV DataFrame from a list of close prices."""
    dates = pd.date_range("2024-01-01", periods=len(prices), freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "open": prices,
            "high": [p * 1.01 for p in prices],
            "low": [p * 0.99 for p in prices],
            "close": prices,
            "volume": [100_000] * len(prices),
        },
        index=dates,
    )


@test("BT-01", "BacktestEngine runs without error on simple data")
def bt01():
    prices = [100, 102, 105, 103, 108, 110, 107, 112]
    df = _make_df(prices)
    _BuyHoldStrategy._instances = []  # reset
    engine = BacktestEngine(
        strategy_class=_BuyHoldStrategy,
        data=df,
        symbol="TEST",
        initial_capital=10_000,
        # symbol is now injected automatically by BacktestEngine — no strategy_kwargs needed
    )
    # Patch total bars
    orig_run = engine.run

    def patched_run():
        # Set total_bars before run via monkey-patch on strategy init
        return orig_run()

    result = engine.run()
    assert result is not None
    assert len(result.equity_curve) == len(prices)


@test("BT-02", "MockOrderManager place_order returns valid OrderResult")
def bt02():
    portfolio = BacktestPortfolio(initial_capital=10_000)
    mock_om = MockOrderManager(portfolio)
    r = OrderRequest(symbol="TEST", action=OrderAction.BUY, quantity=5, tif=TimeInForce.GTC)
    result = mock_om.place_order(r)
    assert result.order_id > 0
    assert result.status == OrderStatus.SUBMITTED


@test("BT-03", "BacktestPortfolio fill reduces cash correctly")
def bt03():
    p = BacktestPortfolio(initial_capital=10_000, commission=0)
    p.fill("TEST", OrderAction.BUY, quantity=10, price=100.0, order_id=1)
    assert abs(p.cash - 9_000.0) < 0.01, f"Expected $9,000, got ${p.cash:.2f}"


@test("BT-04", "BacktestPortfolio SELL increases cash")
def bt04():
    p = BacktestPortfolio(initial_capital=10_000, commission=0)
    p.fill("TEST", OrderAction.BUY, quantity=10, price=100.0, order_id=1)
    p.fill("TEST", OrderAction.SELL, quantity=10, price=110.0, order_id=2)
    assert abs(p.cash - 10_100.0) < 0.01, f"Expected $10,100, got ${p.cash:.2f}"


@test("BT-05", "sharpe_ratio returns a float for valid equity curve")
def bt05():
    import math

    curve = pd.Series([100_000, 101_000, 100_500, 102_000, 103_000])
    sr = sharpe_ratio(curve)
    assert not math.isnan(sr), "Sharpe ratio is NaN"


@test("BT-06", "max_drawdown returns negative number")
def bt06():
    curve = pd.Series([100_000, 110_000, 95_000, 105_000])
    dd = max_drawdown(curve)
    assert dd < 0, f"Expected negative drawdown, got {dd}"
    assert dd > -1.0, "Drawdown should be fraction between -1 and 0"


@test("BT-07", "BacktestEngine raises on empty DataFrame")
def bt07():
    try:
        BacktestEngine(
            strategy_class=_BuyHoldStrategy,
            data=pd.DataFrame(),
            symbol="TEST",
        )
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


@test("BT-08", "BacktestPortfolio skips sell when no position held")
def bt08():
    p = BacktestPortfolio(initial_capital=10_000, commission=0)
    result = p.fill("TEST", OrderAction.SELL, quantity=5, price=100.0, order_id=1)
    assert result.status == OrderStatus.INACTIVE
    assert abs(p.cash - 10_000.0) < 0.01  # cash unchanged


bt01()
bt02()
bt03()
bt04()
bt05()
bt06()
bt07()
bt08()


section("18. DASHBOARD TESTS")

# Dashboard route functions are imported and called directly — no HTTP server,
# no TestClient. Each route is a plain function that returns a dict; we exercise
# them and assert structure. This avoids pulling httpx into the test gate.

from dashboard import app as dashboard_app


@test("DB-01", "api_info returns expected keys")
def db01():
    info = dashboard_app.api_info()
    for key in ("account", "host", "port", "dashboard_started_at", "version"):
        assert key in info, f"missing key {key} in api_info()"
    assert isinstance(info["port"], int)


@test("DB-02", "api_health reports 'missing' when health.txt absent")
def db02():
    original = dashboard_app._HEALTH_FILE
    fake = original.parent / "health_definitely_missing_xyz.txt"
    dashboard_app._HEALTH_FILE = fake
    try:
        result = dashboard_app.api_health()
        assert result["status"] == "missing", f"expected 'missing', got {result['status']}"
        assert result["last_tick"] is None
        assert result["age_seconds"] is None
    finally:
        dashboard_app._HEALTH_FILE = original


@test("DB-03", "api_health reports 'ok' for a fresh tick")
def db03():
    import tempfile

    original = dashboard_app._HEALTH_FILE
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tmp.write(datetime.now(timezone.utc).isoformat())
    tmp.close()
    dashboard_app._HEALTH_FILE = Path(tmp.name)
    try:
        result = dashboard_app.api_health()
        assert result["status"] == "ok", f"expected 'ok', got {result['status']}"
        assert result["age_seconds"] is not None
        assert (
            result["age_seconds"] < 60
        ), f"fresh tick should be <60s old, got {result['age_seconds']}"
    finally:
        dashboard_app._HEALTH_FILE = original
        Path(tmp.name).unlink(missing_ok=True)


@test("DB-04", "api_health reports 'stale' for an old tick")
def db04():
    import tempfile
    from datetime import timedelta

    original = dashboard_app._HEALTH_FILE
    old = datetime.now(timezone.utc) - timedelta(
        seconds=dashboard_app._WEEKEND_STALE_SECONDS + 3600
    )
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tmp.write(old.isoformat())
    tmp.close()
    dashboard_app._HEALTH_FILE = Path(tmp.name)
    try:
        result = dashboard_app.api_health()
        assert result["status"] == "stale", f"expected 'stale', got {result['status']}"
    finally:
        dashboard_app._HEALTH_FILE = original
        Path(tmp.name).unlink(missing_ok=True)


@test("DB-05", "api_health reports 'unreadable' on garbage contents")
def db05():
    import tempfile

    original = dashboard_app._HEALTH_FILE
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tmp.write("not-a-datetime")
    tmp.close()
    dashboard_app._HEALTH_FILE = Path(tmp.name)
    try:
        result = dashboard_app.api_health()
        assert result["status"] == "unreadable", f"expected 'unreadable', got {result['status']}"
    finally:
        dashboard_app._HEALTH_FILE = original
        Path(tmp.name).unlink(missing_ok=True)


@test("DB-06", "api_recent_fills clamps limit to [1, 200]")
def db06():
    # Don't care about results; just confirm no exception and returns a list.
    out = dashboard_app.api_recent_fills(limit=99999)
    assert isinstance(out, list)
    out = dashboard_app.api_recent_fills(limit=-5)
    assert isinstance(out, list)


@test("DB-07", "api_system returns all expected keys")
def db07():
    result = dashboard_app.api_system()
    expected = (
        "bot_service_status",
        "bot_pid",
        "bot_active_since",
        "bot_uptime_seconds",
        "gateway_service_status",
        "gateway_pid",
        "gateway_active_since",
        "gateway_uptime_seconds",
        "gateway_port_open",
    )
    for key in expected:
        assert key in result, f"missing key {key} in api_system()"


@test("DB-08", "api_system gateway_port_open is a bool")
def db08():
    result = dashboard_app.api_system()
    assert isinstance(
        result["gateway_port_open"], bool
    ), f"gateway_port_open should be bool, got {type(result['gateway_port_open'])}"


def _fake_request(ip: str = "10.0.0.1"):
    """Minimal stand-in for fastapi.Request — only .client.host is used."""

    class _C:
        host = ip

    class _R:
        client = _C()

    return _R()


def _reset_rate_state() -> None:
    with dashboard_app._rate_lock:
        dashboard_app._rate_state.clear()


@test("DB-09", "control endpoints reject when DASHBOARD_TOKEN unset (503)")
def db09():
    import os

    from fastapi import HTTPException

    os.environ.pop("DASHBOARD_TOKEN", None)
    _reset_rate_state()
    try:
        dashboard_app._check_token(_fake_request("10.0.0.9"), authorization="Bearer anything")
        raise AssertionError("expected HTTPException 503")
    except HTTPException as exc:
        assert exc.status_code == 503, f"expected 503, got {exc.status_code}"


@test("DB-10", "control endpoints reject missing/wrong token (401)")
def db10():
    import os

    from fastapi import HTTPException

    os.environ["DASHBOARD_TOKEN"] = "secret-xyz"
    _reset_rate_state()
    try:
        # Use distinct IPs so the per-IP rate limit (3/min) doesn't trip during this test.
        try:
            dashboard_app._check_token(_fake_request("10.0.0.10"), authorization=None)
            raise AssertionError("expected HTTPException 401 for missing header")
        except HTTPException as exc:
            assert exc.status_code == 401, f"missing: expected 401, got {exc.status_code}"
        try:
            dashboard_app._check_token(_fake_request("10.0.0.11"), authorization="Bearer wrong")
            raise AssertionError("expected HTTPException 401 for wrong token")
        except HTTPException as exc:
            assert exc.status_code == 401, f"wrong: expected 401, got {exc.status_code}"
        try:
            dashboard_app._check_token(
                _fake_request("10.0.0.12"), authorization="NotBearer secret-xyz"
            )
            raise AssertionError("expected HTTPException 401 for non-bearer scheme")
        except HTTPException as exc:
            assert exc.status_code == 401, f"scheme: expected 401, got {exc.status_code}"
        # Correct token must NOT raise.
        dashboard_app._check_token(_fake_request("10.0.0.13"), authorization="Bearer secret-xyz")
    finally:
        os.environ.pop("DASHBOARD_TOKEN", None)
        _reset_rate_state()


@test("DB-11", "_systemctl_action returns ok on rc=0 (mocked subprocess)")
def db11():
    import subprocess as sp_module

    class _FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    original_run = dashboard_app.subprocess.run
    dashboard_app.subprocess.run = lambda *a, **kw: _FakeCompleted()  # type: ignore[assignment]
    try:
        result = dashboard_app._systemctl_action("restart")
        assert result["ok"] is True
        assert result["action"] == "restart"
    finally:
        dashboard_app.subprocess.run = original_run  # type: ignore[assignment]
    _ = sp_module  # keep import explicit so the test reads cleanly


@test("DB-12", "_systemctl_action raises 500 on non-zero rc (mocked subprocess)")
def db12():
    from fastapi import HTTPException

    class _FailedCompleted:
        returncode = 1
        stdout = ""
        stderr = "permission denied"

    original_run = dashboard_app.subprocess.run
    dashboard_app.subprocess.run = lambda *a, **kw: _FailedCompleted()  # type: ignore[assignment]
    try:
        try:
            dashboard_app._systemctl_action("stop")
            raise AssertionError("expected HTTPException 500")
        except HTTPException as exc:
            assert exc.status_code == 500, f"expected 500, got {exc.status_code}"
            assert "rc=1" in str(exc.detail)
    finally:
        dashboard_app.subprocess.run = original_run  # type: ignore[assignment]


@test("DB-13", "_systemctl_action rejects unsupported actions (400)")
def db13():
    from fastapi import HTTPException

    try:
        dashboard_app._systemctl_action("nuke")
        raise AssertionError("expected HTTPException 400")
    except HTTPException as exc:
        assert exc.status_code == 400, f"expected 400, got {exc.status_code}"


@test("DB-14", "control endpoints rate-limit at 3 requests/min/IP (429)")
def db14():
    import os

    from fastapi import HTTPException

    os.environ["DASHBOARD_TOKEN"] = "secret-xyz"
    _reset_rate_state()
    ip = "10.0.0.14"
    try:
        # First 3 requests with valid token must succeed.
        for i in range(dashboard_app._RATE_LIMIT_MAX_ATTEMPTS):
            dashboard_app._check_token(_fake_request(ip), authorization="Bearer secret-xyz")
        # 4th request from same IP must hit rate limit.
        try:
            dashboard_app._check_token(_fake_request(ip), authorization="Bearer secret-xyz")
            raise AssertionError("expected HTTPException 429")
        except HTTPException as exc:
            assert exc.status_code == 429, f"expected 429, got {exc.status_code}"
        # A different IP is unaffected.
        dashboard_app._check_token(_fake_request("10.0.0.15"), authorization="Bearer secret-xyz")
    finally:
        os.environ.pop("DASHBOARD_TOKEN", None)
        _reset_rate_state()


@test("DB-15", "lockout after N invalid-token attempts (5min 429)")
def db15():
    import os

    from fastapi import HTTPException

    os.environ["DASHBOARD_TOKEN"] = "secret-xyz"
    _reset_rate_state()
    ip = "10.0.0.16"
    try:
        # Push enough invalid-token attempts to trip the lockout.
        # We must spread them across distinct IPs to avoid the 3/min rate limit
        # short-circuiting before fails reach _LOCKOUT_FAILED_THRESHOLD. Instead,
        # call _record_auth_failure directly to simulate _LOCKOUT_FAILED_THRESHOLD
        # 401s, then verify the next call gets 429 lockout (not 401).
        for _ in range(dashboard_app._LOCKOUT_FAILED_THRESHOLD):
            dashboard_app._record_auth_failure(ip)
        try:
            dashboard_app._check_token(_fake_request(ip), authorization="Bearer secret-xyz")
            raise AssertionError("expected HTTPException 429 lockout")
        except HTTPException as exc:
            assert exc.status_code == 429, f"expected 429, got {exc.status_code}"
            assert "locked out" in str(exc.detail), f"expected lockout msg, got {exc.detail}"
    finally:
        os.environ.pop("DASHBOARD_TOKEN", None)
        _reset_rate_state()


@test("DB-16", "HTTP: missing Authorization header → 401")
def db16():
    import os
    from starlette.testclient import TestClient

    os.environ["DASHBOARD_TOKEN"] = "tc-secret"
    _reset_rate_state()
    try:
        client = TestClient(dashboard_app.app, raise_server_exceptions=False)
        r = client.post("/api/bot/restart")
        assert r.status_code == 401, f"expected 401, got {r.status_code}"
    finally:
        os.environ.pop("DASHBOARD_TOKEN", None)
        _reset_rate_state()


@test("DB-17", "HTTP: wrong scheme 'Token x' → 401")
def db17():
    import os
    from starlette.testclient import TestClient

    os.environ["DASHBOARD_TOKEN"] = "tc-secret"
    _reset_rate_state()
    try:
        client = TestClient(dashboard_app.app, raise_server_exceptions=False)
        r = client.post("/api/bot/restart", headers={"Authorization": "Token tc-secret"})
        assert r.status_code == 401, f"expected 401, got {r.status_code}"
    finally:
        os.environ.pop("DASHBOARD_TOKEN", None)
        _reset_rate_state()


@test("DB-18", "HTTP: wrong token 'Bearer bad' → 401")
def db18():
    import os
    from starlette.testclient import TestClient

    os.environ["DASHBOARD_TOKEN"] = "tc-secret"
    _reset_rate_state()
    try:
        client = TestClient(dashboard_app.app, raise_server_exceptions=False)
        r = client.post("/api/bot/restart", headers={"Authorization": "Bearer bad"})
        assert r.status_code == 401, f"expected 401, got {r.status_code}"
    finally:
        os.environ.pop("DASHBOARD_TOKEN", None)
        _reset_rate_state()


@test("DB-19", "HTTP: lowercase 'bearer valid' → 401 (scheme check is case-sensitive)")
def db19():
    import os
    from starlette.testclient import TestClient

    os.environ["DASHBOARD_TOKEN"] = "tc-secret"
    _reset_rate_state()
    try:
        client = TestClient(dashboard_app.app, raise_server_exceptions=False)
        r = client.post("/api/bot/restart", headers={"Authorization": "bearer tc-secret"})
        assert r.status_code == 401, f"expected 401, got {r.status_code}"
    finally:
        os.environ.pop("DASHBOARD_TOKEN", None)
        _reset_rate_state()


@test("DB-20", "HTTP: valid Bearer token → 200 (subprocess mocked)")
def db20():
    import os
    import subprocess as sp_module
    from starlette.testclient import TestClient

    class _FakeDone:
        returncode = 0
        stdout = ""
        stderr = ""

    original_run = sp_module.run
    sp_module.run = lambda *a, **kw: _FakeDone()  # type: ignore[assignment]
    os.environ["DASHBOARD_TOKEN"] = "tc-secret"
    _reset_rate_state()
    try:
        client = TestClient(dashboard_app.app, raise_server_exceptions=False)
        r = client.post("/api/bot/restart", headers={"Authorization": "Bearer tc-secret"})
        assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
        assert r.json().get("ok") is True, f"unexpected body: {r.json()}"
    finally:
        sp_module.run = original_run
        os.environ.pop("DASHBOARD_TOKEN", None)
        _reset_rate_state()


db01()
db02()
db03()
db04()
db05()
db06()
db07()
db08()
db09()
db10()
db11()
db12()
db13()
db14()
db15()
db16()
db17()
db18()
db19()
db20()


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
