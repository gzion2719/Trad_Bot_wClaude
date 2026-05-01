"""
Integration test for OrderManager.
Requires TWS running on paper account (port 7497).
"""

from config.logging_config import setup_logging
from broker.ibkr_client import IBKRClient
from broker.order_manager import OrderManager, DuplicateOrderError
from models.order import OrderAction, OrderRequest, OrderType, TimeInForce

setup_logging()


def on_fill(result):
    print(
        f"\n*** FILL: {result.action} {result.quantity} {result.symbol} @ {result.avg_fill_price} ***\n"
    )


def on_cancel(result):
    print(f"\n*** CANCELLED: order {result.order_id} for {result.symbol} ***\n")


def main():
    client = IBKRClient()
    client.connect()

    om = OrderManager(client)
    om.on_fill(on_fill)
    om.on_cancel(on_cancel)

    # ── 1. Show current positions ──────────────────────────────────────
    positions = om.get_positions()
    print("=== Current Positions ===")
    if positions:
        for p in positions:
            print(f"  {p.symbol}: {p.quantity} shares @ avg cost {p.avg_cost:.2f}")
    else:
        print("  (none)")

    # ── 2. Show open orders ────────────────────────────────────────────
    open_orders = om.get_open_orders()
    print(f"\n=== Open Orders ({len(open_orders)}) ===")
    for o in open_orders:
        print(f"  [{o.order_id}] {o.action} {o.quantity} {o.symbol} | {o.order_type} | {o.status}")

    # ── 3. Place a limit buy for AAPL ──────────────────────────────────
    print("\n=== Placing limit BUY for MSFT ===")
    request = OrderRequest(
        symbol="MSFT",
        action=OrderAction.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=1.00,  # far below market so it won't fill immediately
        tif=TimeInForce.GTC,
    )
    result = om.place_order(request)
    print(f"  Placed: id={result.order_id} | status={result.status} | limit={result.limit_price}")

    # ── 4. Attempt duplicate (should raise) ───────────────────────────
    print("\n=== Testing duplicate prevention ===")
    try:
        om.place_order(request)
        print("  ERROR: duplicate was not blocked!")
    except DuplicateOrderError as e:
        print(f"  Duplicate correctly blocked: {e}")

    # ── 5. Cancel the order we just placed ────────────────────────────
    print(f"\n=== Cancelling order {result.order_id} ===")
    cancelled = om.cancel_order(result.order_id)
    print(f"  Cancelled: {cancelled}")

    client.disconnect()


if __name__ == "__main__":
    main()
