"""
TradeBot — main entry point.

Startup order:
  1. Validate config (fail fast before touching IBKR)
  2. Connect to TWS
  3. Build OrderManager
  4. Build ReconnectManager (auto-reconnect on TWS drop)
  5. Build StrategyRunner from config/strategies.REGISTRY
     - Each strategy gets its own RiskManager (independent caps)
     - Each strategy runs in its own scheduler thread
     - Fills route back via OrderResult.strategy_name

Usage:
    python main.py
"""

import logging
import signal
import sys
import threading
from datetime import datetime
from pathlib import Path

from config.logging_config import setup_logging
from config.strategies import REGISTRY
from config.validator import validate_config, ConfigError
from broker.ibkr_client import IBKRClient
from broker.order_manager import OrderManager
from broker.reconnect import ReconnectManager
from runtime.strategy_runner import StrategyRunner

setup_logging()
logger = logging.getLogger(__name__)


def main() -> None:

    # ── Step 1: Validate config before doing anything else ──────────────
    try:
        validate_config()
    except ConfigError as e:
        logger.critical("Startup aborted — invalid configuration:\n%s", e)
        sys.exit(1)

    # ── Step 2: Connect to TWS ───────────────────────────────────────────
    client = IBKRClient()
    try:
        client.connect(retries=3)
    except ConnectionError as e:
        logger.critical("Could not connect to IBKR: %s — is TWS running?", e)
        sys.exit(1)

    # ── Step 3: Order management ─────────────────────────────────────────
    om = OrderManager(client)

    # ── Step 4: Auto-reconnect ───────────────────────────────────────────
    reconnect = ReconnectManager(
        client=client,
        order_manager=om,
        on_reconnected=lambda: logger.info("Reconnected — strategies resuming."),
        max_attempts=10,
    )
    reconnect.start()

    # ── Step 5: Global event handlers ────────────────────────────────────
    om.on_fill(
        lambda r: logger.info(
            "FILL: %s %s x%s @ %s | strategy=%s",
            r.action,
            r.quantity,
            r.symbol,
            r.avg_fill_price,
            r.strategy_name or "-",
        )
    )
    om.on_cancel(lambda r: logger.info("CANCEL: order %s %s", r.order_id, r.symbol))
    om.on_error(lambda rid, code, msg: logger.error("ERROR [%s]: %s", code, msg))

    # ── Step 6: Build the multi-strategy runner ──────────────────────────
    # Each StrategyConfig in REGISTRY produces:
    #   - one strategy instance (with its own RiskManager and scheduler)
    #   - on_fill hooks that filter by strategy_name → independent bookkeeping
    from data.feed import IBKRFeed
    from data.trade_log import TradeLog

    feed = IBKRFeed(client)
    trade_log = TradeLog(db_path=Path("data/paper_trades.db"))
    runner = StrategyRunner(
        client=client,
        order_manager=om,
        reconnect=reconnect,
        feed=feed,
        trade_log=trade_log,
        registry=REGISTRY,
    )
    runner.build()

    # ── Step 7: Daily P&L poller + market-open reset ─────────────────────
    # Single account-level realized P&L poll feeds every strategy's RiskManager
    # via runner.update_daily_pnl_all (see config/strategies.py for the
    # multi-strategy attribution caveat).
    _MARKET_OPEN_HOUR_ET = 9
    _MARKET_OPEN_MINUTE_ET = 30
    _last_reset_date: list = [None]  # mutable container so inner fn can write
    _stop_pnl_poller = threading.Event()

    def _poll_pnl_and_reset() -> None:
        """Daemon: resets daily counters at market open, polls P&L every 60s."""
        try:
            import zoneinfo

            _ET = zoneinfo.ZoneInfo("America/New_York")
        except (ImportError, KeyError) as exc:
            raise RuntimeError(
                "tzdata package required for PnL poller timezone. Run: pip install tzdata"
            ) from exc

        while not _stop_pnl_poller.is_set():
            try:
                now_et = datetime.now(_ET)
                today = now_et.date()

                at_or_after_open = now_et.hour > _MARKET_OPEN_HOUR_ET or (
                    now_et.hour == _MARKET_OPEN_HOUR_ET and now_et.minute >= _MARKET_OPEN_MINUTE_ET
                )
                if at_or_after_open and _last_reset_date[0] != today:
                    runner.reset_all_daily()
                    _last_reset_date[0] = today
                    logger.info("Daily risk counters reset for %s (all strategies)", today)

                if client.is_alive():
                    summary = {s.tag: s.value for s in client.ib.accountSummary()}
                    raw_pnl = summary.get("RealizedPnL", None)
                    if raw_pnl is not None:
                        runner.update_daily_pnl_all(float(raw_pnl))

            except Exception as exc:
                logger.warning("PnL poller error (non-fatal): %s", exc, exc_info=True)

            _stop_pnl_poller.wait(timeout=60)

    pnl_thread = threading.Thread(target=_poll_pnl_and_reset, name="PnLPoller", daemon=True)
    pnl_thread.start()
    logger.info("PnL poller started — daily loss ceiling is now ACTIVE for all strategies.")

    # ── Step 8: Account snapshot poller ───────────────────────────────────
    from data.account_snapshot import AccountSnapshotPoller

    _snapshot_poller = AccountSnapshotPoller(client, Path("data"))
    _snapshot_poller.start()
    logger.info("AccountSnapshotPoller started — writing data/account_snapshot.json every 30s.")

    # ── Step 9: SIGTERM handler (required for clean VPS/systemd shutdown) ──
    def _sigterm_handler(signum, frame):
        logger.info("SIGTERM received — initiating clean shutdown.")
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _sigterm_handler)

    # ── Step 10: Start all strategies ────────────────────────────────────
    runner.start_all()

    logger.info("Bot running. Press Ctrl+C to stop.")
    try:
        client.ib.run()  # ib_insync event loop — keeps the process alive
    except KeyboardInterrupt:
        logger.info("Shutdown requested.")
    finally:
        runner.stop_all()
        _stop_pnl_poller.set()
        _snapshot_poller.stop()
        reconnect.stop()
        client.disconnect()


if __name__ == "__main__":
    main()
