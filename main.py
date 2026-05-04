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
import signal
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

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
    # Caps sized for QQQ on a ~$100k paper account (C3 — see sma_crossover.py).
    rm = RiskManager(
        client=client,
        order_manager=om,
        max_order_value=120_000.0,  # QQQ position can exceed $100k
        max_position_value=100_000.0,  # one full QQQ position at a time
        max_daily_loss=-2_000.0,  # halt if down $2,000 on the day
        max_open_orders=10,
        max_risk_per_trade_pct=0.02,  # risk ≤ 2% of equity per trade
        min_reward_risk_ratio=3.0,  # minimum 1:3 R/R required
    )
    om.on_fill(rm.record_fill)  # keep risk manager's fill log in sync

    # ── Step 5: Auto-reconnect ───────────────────────────────────────────
    reconnect = ReconnectManager(
        client=client,
        order_manager=om,
        on_reconnected=lambda: logger.info("Reconnected — strategies resuming."),
        max_attempts=10,
    )
    reconnect.start()

    # ── Step 6: Global event handlers ────────────────────────────────────
    om.on_fill(
        lambda r: logger.info(
            "FILL: %s %s x%s @ %s", r.action, r.quantity, r.symbol, r.avg_fill_price
        )
    )
    om.on_cancel(lambda r: logger.info("CANCEL: order %s %s", r.order_id, r.symbol))
    om.on_error(lambda rid, code, msg: logger.error("ERROR [%s]: %s", code, msg))

    # ── Step 7: Wire daily P&L updates and market-open reset ─────────────
    # This daemon thread does two things every 60 seconds:
    #   a) Resets the daily loss counter at market open (9:30 AM US/Eastern)
    #   b) Polls IBKR account summary to update the daily realized P&L tracker
    #
    # Without this, RiskManager.max_daily_loss will NEVER trigger because
    # record_fill() is intentionally a no-op (accurate P&L requires the account API).
    #
    # NOTE: This block is ACTIVE but only runs when client is connected.
    # Leave it running — it is safe. The P&L poll is read-only.

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

                # Reset daily counters once per day at/after market open
                at_or_after_open = now_et.hour > _MARKET_OPEN_HOUR_ET or (
                    now_et.hour == _MARKET_OPEN_HOUR_ET and now_et.minute >= _MARKET_OPEN_MINUTE_ET
                )
                if at_or_after_open and _last_reset_date[0] != today:
                    rm.reset_daily()
                    _last_reset_date[0] = today
                    logger.info("Daily risk counters reset for %s", today)

                # Poll realized P&L from IBKR account summary
                if client.is_alive():
                    summary = {s.tag: s.value for s in client.ib.accountSummary()}
                    raw_pnl = summary.get("RealizedPnL", None)
                    if raw_pnl is not None:
                        rm.update_daily_pnl(float(raw_pnl))

            except Exception as exc:
                logger.warning("PnL poller error (non-fatal): %s", exc, exc_info=True)

            _stop_pnl_poller.wait(timeout=60)  # sleep 60s, wakes early on shutdown

    pnl_thread = threading.Thread(target=_poll_pnl_and_reset, name="PnLPoller", daemon=True)
    pnl_thread.start()
    logger.info("PnL poller started — daily loss ceiling is now ACTIVE.")

    # ── Step 7b: Account snapshot poller ────────────────────────────────────
    from data.account_snapshot import AccountSnapshotPoller

    _snapshot_poller = AccountSnapshotPoller(client, Path("data"))
    _snapshot_poller.start()
    logger.info("AccountSnapshotPoller started — writing data/account_snapshot.json every 30s.")

    # ── Step 8: SIGTERM handler (required for clean VPS/systemd shutdown) ──
    # systemctl stop tradebot sends SIGTERM, not SIGINT (Ctrl+C).
    # Without this, the process is killed uncleanly: open orders left open,
    # connection not disconnected, reconnect thread not joined.
    def _sigterm_handler(signum, frame):
        logger.info("SIGTERM received — initiating clean shutdown.")
        raise KeyboardInterrupt  # reuse the existing try/finally cleanup path

    signal.signal(signal.SIGTERM, _sigterm_handler)

    # ── Step 9: Strategy wiring (Sprint 4.4) ─────────────────────────────
    from strategies.sma_crossover import SMACrossover
    from data.feed import IBKRFeed
    from data.trade_log import TradeLog

    # L7 — persistent paper-trade audit trail (one record per fill).
    trade_log = TradeLog(db_path=Path("data/paper_trades.db"))
    om.on_fill(lambda result: trade_log.record(result, strategy_name="SMACrossover"))

    feed = IBKRFeed(client)

    strategy = SMACrossover(
        client=client,
        order_manager=om,
        risk_manager=rm,
        reconnect=reconnect,
        feed=feed,
        symbol="QQQ",
        sma_fast=10,
        sma_slow=30,
    )
    strategy.on_start()

    # Daily bar scheduler — fires at 16:10 ET so _refresh_closes() always
    # sees finalized daily closes.  Using a fixed wall-clock target rather
    # than interval_seconds avoids firing mid-session when the bot restarts.
    _stop_scheduler = threading.Event()

    def _daily_scheduler() -> None:
        try:
            import zoneinfo

            _ET = zoneinfo.ZoneInfo("America/New_York")
        except Exception:
            _ET = timezone(timedelta(hours=-5))  # type: ignore[assignment]

        while not _stop_scheduler.is_set():
            now_et = datetime.now(_ET)
            target = now_et.replace(hour=16, minute=10, second=0, microsecond=0)
            if now_et >= target:
                target = target + timedelta(days=1)
            wait_secs = (target - now_et).total_seconds()
            if _stop_scheduler.wait(timeout=wait_secs):
                break
            if not _stop_scheduler.is_set():
                try:
                    strategy.on_tick()
                except Exception as exc:
                    logger.error("Daily scheduler: on_tick error: %s", exc)

    scheduler_thread = threading.Thread(target=_daily_scheduler, name="DailyScheduler", daemon=True)
    scheduler_thread.start()
    logger.info("Daily scheduler started — on_tick fires at 16:10 ET each trading day.")

    logger.info("Bot running. Press Ctrl+C to stop.")
    try:
        client.ib.run()  # ib_insync event loop — keeps the process alive
    except KeyboardInterrupt:
        logger.info("Shutdown requested.")
    finally:
        _stop_scheduler.set()
        strategy.on_stop()
        _stop_pnl_poller.set()  # wake the poller so it exits cleanly
        _snapshot_poller.stop()
        reconnect.stop()
        client.disconnect()


if __name__ == "__main__":
    main()
