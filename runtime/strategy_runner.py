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
from config.strategies import (
    DailyAt,
    Interval,
    StrategyConfig,
    validate_registry,
)
from data.feed import DataFeed
from data.trade_log import TradeLog
from models.order import OrderResult
from risk.risk_manager import RiskManager
from strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class StartupError(RuntimeError):
    """Raised by StrategyRunner.start_all when any strategy's on_start fails.

    Fail-fast (F-RT-01): the runner refuses to bring the bot up partially —
    successfully-started strategies are rolled back before this is raised so the
    caller (main.py) can exit cleanly. The original on_start exception is the
    `__cause__`. See `_rollback_started` for the rollback contract.
    """


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
        # F-RT-01: callbacks registered on OrderManager during build(), tracked
        # here so _rollback_started can unregister them and never leak fill
        # routing into a dead RiskManager / dead TradeLog hook.
        self.fill_callbacks: List[Callable[[OrderResult], None]] = []


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
        # Single source of truth: config.strategies.validate_registry. Raises
        # ConfigError on empty registry, blank/duplicate names, or shared
        # symbols (case-insensitive). Second pass is intentional — defends
        # against custom registries passed directly (not the global REGISTRY
        # which is already validated at module load).
        validate_registry(self.registry)

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
            # MS-B: hand the strategy a reference to TradeLog so it can compute
            # its own attributed equity (initial_capital + own realized P&L)
            # instead of account-wide NetLiquidation.
            strategy._trade_log = self.trade_log

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
            risk_cb = self._make_risk_fill_hook(cfg.name, rm)
            log_cb = self._make_trade_log_hook(cfg.name, strategy)
            self.om.on_fill(risk_cb)
            self.om.on_fill(log_cb)

            handle = StrategyHandle(cfg, strategy, rm)
            # _dispatch_on_fill is registered by BaseStrategy.__init__ on om;
            # track it here so rollback can remove all 3 callbacks belonging
            # to this strategy (F-RT-01).
            handle.fill_callbacks = [strategy._dispatch_on_fill, risk_cb, log_cb]
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
        """Start every built strategy; fail-fast on any error (F-RT-01).

        If any strategy's on_start raises (or its scheduler thread is missing),
        already-started strategies are rolled back AND the failing strategy is
        rolled back defensively (its on_start may have partially mutated state —
        subscribed to a feed, opened a recovery order, etc.). Then `StartupError`
        is raised, chained from the original exception. main.py invokes this
        outside its try/finally so the process exits non-zero.

        Race note: a strategy whose thread.start() succeeded may fire one on_tick
        between thread.start() and the rollback's stop_event.set(). Accepted on
        a fatal-error code path.
        """
        total = len(self.handles)
        started: List[StrategyHandle] = []
        for idx, h in enumerate(self.handles):
            try:
                h.strategy.on_start()
            except Exception as exc:
                logger.exception(
                    "on_start failed for strategy %s (%d of %d) — rolling back %d already-started + failing",
                    h.config.name,
                    idx + 1,
                    total,
                    len(started),
                )
                # Roll back the failing strategy too — its on_start may have
                # partially mutated external state before raising (B1).
                self._rollback_started(started + [h])
                raise StartupError(f"Strategy {h.config.name} on_start failed") from exc
            if h.thread is None:
                logger.error(
                    "Scheduler thread missing for %s — rolling back %d already-started",
                    h.config.name,
                    len(started),
                )
                # h.strategy.on_start() already ran above — roll back this
                # handle defensively too.
                self._rollback_started(started + [h])
                raise RuntimeError(
                    f"StrategyRunner: scheduler thread not built for {h.config.name}; "
                    "did you call build() before start_all()?"
                )
            h.thread.start()
            started.append(h)
            logger.info(
                "Strategy started: %s (symbol=%s, schedule=%s)",
                h.config.name,
                h.config.symbol,
                type(h.config.schedule).__name__,
            )

    def _rollback_started(self, handles: List[StrategyHandle]) -> None:
        """Stop schedulers, call on_stop, unregister fill callbacks, join threads.

        Used by start_all on a fail-fast path. Best-effort: on_stop failures are
        logged but do not interrupt the rollback (other handles still get cleaned
        up). After this returns, every passed handle has its callbacks removed
        from OrderManager — so even if `StartupError` is caught and the process
        continues, subsequent fills will not route into rolled-back strategies.
        """
        for h in handles:
            h.stop_event.set()
        for h in handles:
            logger.debug("Rolling back %s: calling on_stop", h.config.name)
            try:
                h.strategy.on_stop()
            except Exception:
                logger.exception("on_stop failed during rollback for %s", h.config.name)
        for h in handles:
            for cb in h.fill_callbacks:
                self.om.remove_on_fill(cb)
            h.fill_callbacks = []
        for h in handles:
            if h.thread is not None and h.thread.is_alive():
                h.thread.join(timeout=2.0)

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
