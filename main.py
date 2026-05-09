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
from datetime import datetime, timedelta, timezone
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
        sys.exit(0)  # permanent failure — don't retry; fix config and restart manually

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
    # MS-A2: each RiskManager is fed its OWN per-strategy P&L from TradeLog
    # (no longer the account-level RealizedPnL aggregate). Cutoff is the most
    # recent 9:30 ET; ET trading-day boundary makes the query DST-safe.
    _MARKET_OPEN_HOUR_ET = 9
    _MARKET_OPEN_MINUTE_ET = 30
    _last_reset_date: list = [None]  # mutable container so inner fn can write
    _stop_pnl_poller = threading.Event()

    try:
        import zoneinfo as _zoneinfo

        _ET = _zoneinfo.ZoneInfo("America/New_York")
    except (ImportError, KeyError) as exc:
        raise RuntimeError(
            "tzdata package required for PnL poller timezone. Run: pip install tzdata"
        ) from exc

    def _et_trading_day_cutoff_iso() -> str:
        """Most recent 9:30 ET as UTC ISO-8601 string. If now is before today's
        9:30 ET, return yesterday's 9:30 ET — fills before today's open belong
        to the prior trading day."""
        now_et = datetime.now(_ET)
        today_open_et = now_et.replace(
            hour=_MARKET_OPEN_HOUR_ET,
            minute=_MARKET_OPEN_MINUTE_ET,
            second=0,
            microsecond=0,
        )
        if now_et < today_open_et:
            today_open_et -= timedelta(days=1)
        return today_open_et.astimezone(timezone.utc).isoformat()

    # ── Initial sync refresh: catch up on any fills already in TradeLog
    # for today's window before strategies start ticking. Without this, the
    # first 60s after start are blind to today's prior fills (e.g., bot crashed
    # mid-day, restarted by systemd — could open a new trade despite already
    # being over its cap). Fail-loud: if the query throws here, startup aborts.
    try:
        _initial_cutoff = _et_trading_day_cutoff_iso()
        runner.update_daily_pnl_per_strategy(_initial_cutoff)
        # MS-A2 amendment from second-pass CR: surface pre-A1 NULL fills inside
        # today's window so the operator knows attribution starts mid-stream.
        for _h in runner.handles:
            _null_count = trade_log.count_null_pnl_since(_h.config.name, _initial_cutoff)
            if _null_count > 0:
                logger.warning(
                    "MS-A2: %d SELL fill(s) for %s in today's window have "
                    "realized_pnl=NULL (pre-A1 cost_basis missing) — "
                    "per-strategy attribution under-counts these trades.",
                    _null_count,
                    _h.config.name,
                )
        logger.info("Initial per-strategy P&L refresh complete (cutoff=%s).", _initial_cutoff)
    except Exception as exc:
        logger.error(
            "Initial PnL refresh failed: %s — continuing, poller will retry.",
            exc,
            exc_info=True,
        )

    def _poll_pnl_and_reset() -> None:
        """Daemon: resets daily counters at market open, polls per-strategy
        P&L from TradeLog every 60s."""
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

                # MS-A2: per-strategy P&L from TradeLog (no IBKR call here).
                cutoff_iso = _et_trading_day_cutoff_iso()
                runner.update_daily_pnl_per_strategy(cutoff_iso)

            except Exception as exc:
                logger.warning("PnL poller error (non-fatal): %s", exc, exc_info=True)

            _stop_pnl_poller.wait(timeout=60)

    pnl_thread = threading.Thread(target=_poll_pnl_and_reset, name="PnLPoller", daemon=True)
    pnl_thread.start()
    logger.info("PnL poller started — per-strategy daily loss ceiling is now ACTIVE.")

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
