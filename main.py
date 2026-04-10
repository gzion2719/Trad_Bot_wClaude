"""
TradeBot — main entry point.

Usage:
    python main.py
"""
from config.logging_config import setup_logging
from broker.ibkr_client import IBKRClient
from broker.order_manager import OrderManager

setup_logging()


def main() -> None:
    client = IBKRClient()
    client.connect()

    om = OrderManager(client)

    # ── Register global event handlers ──────────────────────────────────
    om.on_fill(lambda r: print(f"FILL: {r.action} {r.quantity} {r.symbol} @ {r.avg_fill_price}"))
    om.on_cancel(lambda r: print(f"CANCEL: order {r.order_id} {r.symbol}"))
    om.on_error(lambda rid, code, msg: print(f"ERROR [{code}]: {msg}"))

    # ── TODO: load and start strategy ───────────────────────────────────
    # from strategies.my_strategy import MyStrategy
    # strategy = MyStrategy(client, om)
    # strategy.on_start()

    try:
        client.ib.run()  # event loop — keeps the bot alive
    except KeyboardInterrupt:
        pass
    finally:
        # strategy.on_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
