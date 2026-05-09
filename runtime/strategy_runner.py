"""
StrategyRunner — multi-strategy supervisor.

Builds N strategies from a registry and runs each with:
  - its own `RiskManager` (independent caps; Decision B 2026-05-06)
  - its own scheduler thread (DailyAt or Interval)
  - fills routed back to the source strategy via `OrderResult.strategy_name`

Lifecycle:
    runner = StrategyRunner(client, om, reconnect, feed, trade_log, REGISTRY)
    runner.build()       # constructs strategies + RiskManagers, registers callbacks
    runner.start_all()   # calls strategy.on_start() then starts its scheduler thread
    ...
    runner.stop_all()    # signals all schedulers, calls strategy.on_stop()

Risk-bookkeeping integration:
    runner.reset_all_daily()                       # call from PnLPoller at 9:30 ET
    runner.update_daily_pnl_per_strategy(cutoff)   # MS-A2: call every 60s; per-
                                                   # strategy P&L from TradeLog
                                                   # (replaces deprecated
                                                   # update_daily_pnl_all)
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

from broker.ibkr_client import IBKRClient
from broker.order_manager import OrderManager
from broker.reconnect import ReconnectManager
from config.strategies import DailyAt, Interval, StrategyConfig
from data.feed import DataFeed
from data.trade_log import TradeLog
from models.order import OrderResult
from risk.risk_manager import RiskManager
from strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class StrategyHandle:
    """One running strategy: instance + risk manager + scheduler thread + stop event."""

    def __init__(
        self,
        config: StrategyConfig,
        strategy: BaseStrategy,
        risk_manager: RiskManager,
    ) -> None:
        self.config = config
        self.strategy = strategy
        self.risk_manager = risk_manager
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None


class StrategyRunner:
    """Supervises N strategies as defined in a registry of StrategyConfig."""

    # Schedulers stop after this many consecutive on_tick exceptions.
    _ERROR_BUDGET = 5

    def __init__(
        self,
        client: IBKRClient,
        order_manager: OrderManager,
        reconnect: ReconnectManager,
        feed: DataFeed,
        trade_log: TradeLog,
        registry: List[StrategyConfig],
    ) -> None:
        self.client = client
        self.om = order_manager
        self.reconnect = reconnect
        self.feed = feed
        self.trade_log = trade_log
        self.registry = registry
        self.handles: List[StrategyHandle] = []
        self._validate_registry()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _validate_registry(self) -> None:
        if not self.registry:
            raise ValueError("StrategyRunner: registry is empty.")
        seen: set[str] = set()
        for cfg in self.registry:
            if not cfg.name:
                raise ValueError("StrategyRunner: every StrategyConfig needs a non-empty name.")
            if cfg.name in seen:
                raise ValueError(f"StrategyRunner: duplicate strategy name {cfg.name!r}.")
            seen.add(cfg.name)

    def build(self) -> None:
        """Construct each strategy + per-strategy RiskManager and register callbacks."""
        for cfg in self.registry:
            rm = RiskManager(
                client=self.client,
                order_manager=self.om,
                max_order_value=cfg.risk_caps.max_order_value,
                max_position_value=cfg.risk_caps.max_position_value,
                max_daily_loss=cfg.risk_caps.max_daily_loss,
                max_open_orders=cfg.risk_caps.max_open_orders,
                max_risk_per_trade_pct=cfg.risk_caps.max_risk_per_trade_pct,
                min_reward_risk_ratio=cfg.risk_caps.min_reward_risk_ratio,
                strategy_name=cfg.name,  # MS-A2: per-strategy halt log lines
            )

            strategy = cfg.strategy_class(
                client=self.client,
                order_manager=self.om,
                risk_manager=rm,
                reconnect=self.reconnect,
                feed=self.feed,
                symbol=cfg.symbol,
                **cfg.params,
            )
            # Tag the strategy so safe_place_order() stamps every OrderRequest
            # with this name → OrderManager → OrderResult.strategy_name.
            strategy._strategy_name = cfg.name

            # Per-strategy fill hooks. Both filter on strategy_name so a fill
            # from strategy A never bumps strategy B's bookkeeping.
            #
            # CALLBACK ORDERING CONTRACT (MS-A1) — DO NOT REORDER without updating
            # both this comment and the `test_a1_07_callback_order_contract` test.
            #
            #   1. BaseStrategy.__init__ (already ran above at strategy_class(...))
            #      registered `_dispatch_on_fill` → strategy.on_fill on om.on_fill.
            #   2. _make_risk_fill_hook is registered next.
            #   3. _make_trade_log_hook is registered last.
            #
            # OrderManager._on_fill_callbacks is a list iterated in registration
            # order. The strategy's on_fill MUST run BEFORE the trade_log hook so
            # the strategy can mutate `OrderResult.cost_basis` (and other fields
            # such as `real_r_multiple`) before TradeLog.record() reads them.
            # Reversing this order silently corrupts per-strategy P&L attribution
            # because cost_basis would still be None when the row is persisted.
            self.om.on_fill(self._make_risk_fill_hook(cfg.name, rm))
            self.om.on_fill(self._make_trade_log_hook(cfg.name, strategy))

            handle = StrategyHandle(cfg, strategy, rm)
            handle.thread = self._build_scheduler_thread(handle)
            self.handles.append(handle)

    @staticmethod
    def _make_risk_fill_hook(strategy_name: str, rm: RiskManager) -> Callable[[OrderResult], None]:
        def _hook(result: OrderResult) -> None:
            if result.strategy_name == strategy_name:
                rm.record_fill(result)

        return _hook

    def _make_trade_log_hook(
        self, strategy_name: str, strategy: "BaseStrategy"
    ) -> Callable[[OrderResult], None]:
        log = self.trade_log

        def _hook(result: OrderResult) -> None:
            if result.strategy_name == strategy_name:
                params = strategy.params if hasattr(strategy, "params") else None
                log.record(result, strategy_name=strategy_name, strategy_params=params)

        return _hook

    # ------------------------------------------------------------------
    # Schedulers
    # ------------------------------------------------------------------

    def _build_scheduler_thread(self, handle: StrategyHandle) -> threading.Thread:
        sched = handle.config.schedule
        if isinstance(sched, DailyAt):
            target = self._daily_at_loop(handle, sched)
        elif isinstance(sched, Interval):
            target = self._interval_loop(handle, sched)
        else:
            raise TypeError(f"StrategyRunner: unsupported schedule type {type(sched).__name__}")
        return threading.Thread(target=target, name=f"Sched-{handle.config.name}", daemon=True)

    def _daily_at_loop(self, handle: StrategyHandle, schedule: DailyAt) -> Callable[[], None]:
        def _loop() -> None:
            try:
                import zoneinfo

                tz = zoneinfo.ZoneInfo(schedule.tz)
            except Exception:
                tz = timezone(timedelta(hours=-5))  # type: ignore[assignment]

            errors = 0
            while not handle.stop_event.is_set():
                now = datetime.now(tz)
                target = now.replace(
                    hour=schedule.hour, minute=schedule.minute, second=0, microsecond=0
                )
                if now >= target:
                    target = target + timedelta(days=1)
                wait_secs = (target - now).total_seconds()
                if handle.stop_event.wait(timeout=wait_secs):
                    break
                if handle.stop_event.is_set():
                    break
                try:
                    handle.strategy.on_tick()
                    errors = 0
                except Exception as exc:
                    errors += 1
                    logger.error(
                        "Strategy %s on_tick error (%d/%d): %s",
                        handle.config.name,
                        errors,
                        self._ERROR_BUDGET,
                        exc,
                    )
                    if errors >= self._ERROR_BUDGET:
                        logger.error(
                            "Strategy %s exceeded error budget — stopping scheduler.",
                            handle.config.name,
                        )
                        break

        return _loop

    def _interval_loop(self, handle: StrategyHandle, schedule: Interval) -> Callable[[], None]:
        def _loop() -> None:
            errors = 0
            while not handle.stop_event.is_set():
                if handle.stop_event.wait(timeout=schedule.seconds):
                    break
                try:
                    handle.strategy.on_tick()
                    errors = 0
                except Exception as exc:
                    errors += 1
                    logger.error(
                        "Strategy %s on_tick error (%d/%d): %s",
                        handle.config.name,
                        errors,
                        self._ERROR_BUDGET,
                        exc,
                    )
                    if errors >= self._ERROR_BUDGET:
                        logger.error(
                            "Strategy %s exceeded error budget — stopping scheduler.",
                            handle.config.name,
                        )
                        break

        return _loop

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_all(self) -> None:
        for h in self.handles:
            try:
                h.strategy.on_start()
            except Exception:
                logger.exception("on_start failed for strategy %s", h.config.name)
                continue
            if h.thread is None:
                raise RuntimeError(
                    f"StrategyRunner: scheduler thread not built for {h.config.name}; "
                    "did you call build() before start_all()?"
                )
            h.thread.start()
            logger.info(
                "Strategy started: %s (symbol=%s, schedule=%s)",
                h.config.name,
                h.config.symbol,
                type(h.config.schedule).__name__,
            )

    def stop_all(self) -> None:
        for h in self.handles:
            h.stop_event.set()
        for h in self.handles:
            try:
                h.strategy.on_stop()
            except Exception:
                logger.exception("on_stop failed for strategy %s", h.config.name)
        logger.info("All %d strategies stopped.", len(self.handles))

    # ------------------------------------------------------------------
    # PnL hooks (called from main.py's PnLPoller daemon)
    # ------------------------------------------------------------------

    def reset_all_daily(self) -> None:
        for h in self.handles:
            h.risk_manager.reset_daily()

    def update_daily_pnl_all(self, pnl: float) -> None:
        # DEPRECATED (MS-A2): feeds the SAME pnl to every RM. Kept for tests
        # and backwards compat with single-strategy paths. New code should call
        # update_daily_pnl_per_strategy() which queries TradeLog per strategy.
        for h in self.handles:
            h.risk_manager.update_daily_pnl(pnl)

    def update_daily_pnl_per_strategy(self, cutoff_iso: str) -> None:
        """
        MS-A2: feed each RiskManager its OWN realized P&L since `cutoff_iso`,
        sourced from TradeLog. Replaces the IBKR-account-level feed that made
        every strategy halt when account total breached any single cap.

        Args:
            cutoff_iso: ISO-8601 UTC cutoff (typically the most recent
                        9:30 ET). Computed in PnLPoller (`main.py`) where
                        the wall clock lives.
        """
        for h in self.handles:
            pnl = self.trade_log.realized_pnl_since(h.config.name, cutoff_iso)
            h.risk_manager.update_daily_pnl(pnl)
