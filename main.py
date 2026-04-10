"""
TradeBot — main entry point.

Usage:
    python main.py
"""
import logging

from config.logging_config import setup_logging
from broker.ibkr_client import IBKRClient
from broker.order_manager import OrderManager

setup_logging()
logger = logging.getLogger(__name__)


def main() -> None:
    client = IBKRClient()

    try:
        client.connect(retries=3)
    except ConnectionError as e:
        logger.critical("Could not connect to IBKR: %s — is TWS running?", e)
        return

    om = OrderManager(client)

    # ── Register global event handlers ──────────────────────────────────
    om.on_fill(lambda r: logger.info("FILL: %s %s %s @ %s", r.action, r.quantity, r.symbol, r.avg_fill_price))
    om.on_cancel(lambda r: logger.info("CANCEL: order %s %s", r.order_id, r.symbol))
    om.on_error(lambda rid, code, msg: logger.error("ERROR [%s]: %s", code, msg))

    # ── TODO: load and start strategy ───────────────────────────────────
    # from strategies.my_strategy import MyStrategy
    # strategy = MyStrategy(client, om)
    # strategy.on_start()

    logger.info("Bot running. Press Ctrl+C to stop.")
    try:
        client.ib.run()  # event loop — keeps the bot alive
    except KeyboardInterrupt:
        logger.info("Shutdown requested.")
    finally:
        # strategy.on_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
