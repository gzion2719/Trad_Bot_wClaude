"""
TradeBot — main entry point.

Startup order:
  1. Validate config (fail fast before touching IBKR)
  2. Connect to TWS
  3. Build OrderManager
  4. Build RiskManager (wired to fill events)
  5. Build ReconnectManager (auto-reconnect on TWS drop)
  6. Load and start strategy

Usage:
    python main.py
"""
import logging

from config.logging_config import setup_logging
from config.validator import validate_config, ConfigError
from broker.ibkr_client import IBKRClient
from broker.order_manager import OrderManager
from broker.reconnect import ReconnectManager
from risk.risk_manager import RiskManager

setup_logging()
logger = logging.getLogger(__name__)


def main() -> None:

    # ── Step 1: Validate config before doing anything else ──────────────
    try:
        validate_config()
    except ConfigError as e:
        logger.critical("Startup aborted — invalid configuration:\n%s", e)
        return

    # ── Step 2: Connect to TWS ───────────────────────────────────────────
    client = IBKRClient()
    try:
        client.connect(retries=3)
    except ConnectionError as e:
        logger.critical("Could not connect to IBKR: %s — is TWS running?", e)
        return

    # ── Step 3: Order management ─────────────────────────────────────────
    om = OrderManager(client)

    # ── Step 4: Risk management ──────────────────────────────────────────
    rm = RiskManager(
        client=client,
        order_manager=om,
        max_order_value=5_000.0,     # no single order > $5,000
        max_position_value=10_000.0, # no position > $10,000 per symbol
        max_daily_loss=-500.0,       # halt if down $500 on the day
        max_open_orders=10,
    )
    om.on_fill(rm.record_fill)       # keep risk manager's P&L tracker in sync

    # ── Step 5: Auto-reconnect ───────────────────────────────────────────
    reconnect = ReconnectManager(
        client=client,
        order_manager=om,
        on_reconnected=lambda: logger.info("Reconnected — strategies resuming."),
        max_attempts=10,
    )
    reconnect.start()

    # ── Step 6: Global event handlers ────────────────────────────────────
    om.on_fill(lambda r: logger.info(
        "FILL: %s %s x%s @ %s", r.action, r.quantity, r.symbol, r.avg_fill_price
    ))
    om.on_cancel(lambda r: logger.info(
        "CANCEL: order %s %s", r.order_id, r.symbol
    ))
    om.on_error(lambda rid, code, msg: logger.error(
        "ERROR [%s]: %s", code, msg
    ))

    # ── Step 7: Load and start strategy ──────────────────────────────────
    # Uncomment and replace with your strategy once Sprint 4 begins:
    #
    # from strategies.my_strategy import MyStrategy
    # strategy = MyStrategy(
    #     client=client,
    #     order_manager=om,
    #     risk_manager=rm,
    #     reconnect=reconnect,
    # )
    # strategy.on_start()

    logger.info("Bot running. Press Ctrl+C to stop.")
    try:
        client.ib.run()     # ib_insync event loop — keeps the process alive
    except KeyboardInterrupt:
        logger.info("Shutdown requested.")
    finally:
        # strategy.on_stop()
        reconnect.stop()
        client.disconnect()


if __name__ == "__main__":
    main()
