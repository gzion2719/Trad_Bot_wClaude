from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

import logging

from broker.ibkr_client import IBKRClient
from broker.order_manager import OrderManager
from models.order import OrderRequest, OrderResult, OrderType

logger = logging.getLogger(__name__)


class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies.

    Every strategy must implement:
      - on_start()  — called once when the strategy is activated
      - on_tick()   — called on each data update (bar close, scheduler tick, etc.)
      - on_stop()   — called once when the strategy is deactivated

    Strategies receive all infrastructure components as constructor arguments.
    They should NOT import or instantiate IBKRClient/OrderManager/RiskManager
    themselves — those are injected by main.py (dependency injection).

    Key helpers provided by this base class:
      - safe_place_order(request, price) — runs risk check then places order.
        Always use this instead of self.om.place_order() directly.
      - on_fill(result) — override to react to fill events (update state,
        log trades, re-evaluate position). Auto-wired in __init__.
      - params — override to expose strategy config as a dict. Used by
        TradeLog to record which parameters produced each trade.

    Recommended on_tick() pattern:
        def on_tick(self):
            # 1. Pause cleanly if TWS is reconnecting
            if self.reconnect and not self.reconnect.wait_for_connection(timeout=60):
                return
            # 2. Stop trading if daily loss ceiling hit
            if self.risk_manager and self.risk_manager.is_halted():
                return
            # 3. Get current bar from feed
            bar = self.feed.get_latest(self.symbol)
            if bar is None:
                return
            # 4. Your signal logic here
            ...
            # 5. Place order via safe_place_order (risk check is automatic)
            request = OrderRequest(...)
            self.safe_place_order(request, current_price)
    """

    def __init__(
        self,
        client: IBKRClient,
        order_manager: OrderManager,
        risk_manager=None,  # RiskManager — Optional to avoid circular import at class level
        reconnect=None,  # ReconnectManager — Optional for same reason
        feed=None,  # DataFeed — injected by engine in backtest, by main.py in live
        symbol: str = "",  # Primary symbol this strategy trades
    ) -> None:
        self.client = client
        self.om = order_manager
        self.risk_manager = risk_manager
        self.reconnect = reconnect
        self.feed = feed
        self.symbol = symbol.upper() if symbol else ""

        # Set by StrategyRunner.build() in multi-strategy mode. None for
        # single-strategy / backtest paths — fills then carry strategy_name=None.
        self._strategy_name: str | None = None

        # MS-B: set by StrategyRunner.build() so strategies can query their
        # own attributed realized P&L (TradeLog.realized_pnl_since) instead
        # of the account-wide NetLiquidation. None in backtest paths — the
        # backtest is single-strategy so account-equity == strategy-equity.
        # Typed as Any to avoid a circular import with data.trade_log.
        self._trade_log: Optional[Any] = None

        # Auto-wire fill events to this strategy's on_fill() method.
        # In multi-strategy mode the OrderManager broadcasts every fill to
        # every registered callback, so we filter by strategy_name here so
        # strategy A's on_fill never fires for strategy B's fills (which
        # would corrupt position state, place spurious stop orders, etc.).
        # When _strategy_name is None (single-strategy / backtest path) the
        # filter is a no-op — strategy sees every fill, matching old behavior.
        if self.om is not None:
            self.om.on_fill(self._dispatch_on_fill)

    def _dispatch_on_fill(self, result: OrderResult) -> None:
        """Filter by strategy_name then dispatch to user-defined on_fill."""
        if self._strategy_name is not None and result.strategy_name != self._strategy_name:
            return
        self.on_fill(result)

    # ------------------------------------------------------------------
    # Abstract lifecycle methods — must be implemented by every strategy
    # ------------------------------------------------------------------

    @abstractmethod
    def on_start(self) -> None:
        """Initialize state, subscribe to data, set up indicators, etc."""

    @abstractmethod
    def on_tick(self) -> None:
        """Evaluate conditions and act on each data update."""

    @abstractmethod
    def on_stop(self) -> None:
        """Clean up — cancel open orders, unsubscribe data, flush state, etc."""

    # ------------------------------------------------------------------
    # Fill lifecycle hook — override to react to fills
    # ------------------------------------------------------------------

    def on_fill(self, result: OrderResult) -> None:
        """
        Called automatically on every fill event.

        Override this to update strategy state after a fill — for example:
          - Record that a position is now open/closed
          - Set a stop-loss order after a BUY fills
          - Log the trade with realized P&L

        This method is wired automatically in __init__ via om.on_fill().
        Do NOT call om.on_fill(self.on_fill) again in on_start() — it would
        register a duplicate callback.

        Default: no-op. Safe to leave unimplemented if fill tracking is not needed.
        """

    # ------------------------------------------------------------------
    # Safe order placement — always use this instead of om.place_order()
    # ------------------------------------------------------------------

    def safe_place_order(
        self,
        request: OrderRequest,
        current_price: float,
        allow_duplicate: bool = False,
    ) -> OrderResult:
        """
        Run the risk check and place an order in one call.

        This is the correct way to place orders from within a strategy.
        Calling self.om.place_order() directly bypasses the risk check.

        Args:
            request:       The order to place.
            current_price: Current market price of the symbol (used by
                           RiskManager to compute exposure).
            allow_duplicate: Passed through to order_manager.place_order().

        Returns:
            OrderResult — check result.status for SUBMITTED/FILLED/INACTIVE.

        Raises:
            RiskViolationError: If the order breaches any risk rule.
                                Catch this in on_tick() if you want to handle
                                it gracefully rather than crashing the tick.
        """
        if self.risk_manager is not None:
            self.risk_manager.check(request, current_price)
        self._stamp_strategy_name(request)
        return self.om.place_order(request, allow_duplicate=allow_duplicate)

    def safe_place_protective_order(
        self,
        request: OrderRequest,
        allow_duplicate: bool = True,
    ) -> OrderResult:
        """
        Place a bracket-leg / protective order with the slim risk check (F-BR-01a).

        Use this — never `self.om.place_order` directly — for every STP, LMT,
        or STP LMT protective leg (entry stops, take-profits, trailing stops,
        re-place after cancel). A CI grep tripwire enforces this rule.

        Effective price (used for the value cap):
          - STOP / STOP_LIMIT → request.stop_price (trigger / worst-case bound)
          - LIMIT             → request.limit_price

        See `RiskManager.check_protective` for the rule set: sane-price + value
        cap + conditional halt (skipped for reduce-only protective legs so a
        halt doesn't leave an open position naked).
        """
        if request.order_type in (OrderType.STOP, OrderType.STOP_LIMIT):
            effective_price = request.stop_price
        elif request.order_type == OrderType.LIMIT:
            effective_price = request.limit_price
        else:
            raise ValueError(
                f"safe_place_protective_order requires STOP / LIMIT / STOP_LIMIT, "
                f"got {request.order_type.value}"
            )
        if effective_price is None:
            raise ValueError(f"Protective order missing price field for {request.order_type.value}")
        if self.risk_manager is not None:
            self.risk_manager.check_protective(request, effective_price)
        self._stamp_strategy_name(request)
        logger.info(
            "Protective order: %s %s x%d %s @ %.4f (strategy=%s)",
            request.action.value,
            request.symbol,
            request.quantity,
            request.order_type.value,
            effective_price,
            request.strategy_name,
        )
        return self.om.place_order(request, allow_duplicate=allow_duplicate)

    def _stamp_strategy_name(self, request: OrderRequest) -> None:
        """Stamp `request.strategy_name` so OrderManager routes fills back here.

        `_strategy_name` is set by StrategyRunner.build() in multi-strategy mode;
        None in single-strategy / backtest paths (fills carry strategy_name=None).
        """
        if request.strategy_name is None and self._strategy_name is not None:
            request.strategy_name = self._strategy_name

    # ------------------------------------------------------------------
    # Strategy metadata
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        """Strategy class name. Used in logging, TradeLog, and BarScheduler."""
        return self.__class__.__name__

    @property
    def params(self) -> dict:
        """
        Strategy configuration parameters as a dict.

        Override this to expose the parameters that drive this strategy's
        behavior. The dict is stored in TradeLog alongside each trade so
        you can reconstruct exactly which configuration produced each result.

        Example override:
            @property
            def params(self) -> dict:
                return {
                    "symbol":     self.symbol,
                    "sma_fast":   self._sma_fast,
                    "sma_slow":   self._sma_slow,
                    "position_pct": self._position_pct,
                }
        """
        return {"symbol": self.symbol}
