from __future__ import annotations

import asyncio
import logging
import math
import threading
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from ib_insync import Fill, LimitOrder, MarketOrder, Order, Stock, StopOrder, Trade

from broker.ibkr_client import IBKRClient
from models.order import (
    OrderAction,
    OrderRequest,
    OrderResult,
    OrderStatus,
    OrderType,
    Position,
)

logger = logging.getLogger(__name__)

# Mapping from IBKR execution side ("BOT"/"SLD") to our action string ("BUY"/"SELL")
_EXEC_SIDE_TO_ACTION: dict[str, str] = {"BOT": "BUY", "SLD": "SELL"}

# IBKR codes that are pure noise — suppress to DEBUG
_DEBUG_CODES = {
    2104,
    2106,
    2158,
    2119,
    10182,  # market data farm connected/ok
}

# IBKR connectivity and data-farm status — log at INFO (visible but not alarming)
_INFO_CODES = {
    1102,  # connectivity between IB and TWS restored
    2103,  # market data farm connection broken (transient)
    2105,  # HMDS data farm connection broken (transient)
    2107,  # HMDS data farm connection ok
    2157,  # sec-def data farm connection broken (transient)
}

# IBKR error codes that are warnings (not fatal)
_WARNING_CODES = {
    201,  # order rejected
    202,  # order cancelled — expected, not an error
    399,  # order message
    1100,  # connectivity between IB and TWS lost (transient — 1102 follows)
    10147,  # order not found (already cancelled/filled externally)
}

# IBKR error codes that indicate a lost/broken connection
# These are passed to on_error so callers can decide to halt or reconnect.
_CONNECTION_ERROR_CODES = {
    502,  # Couldn't connect to TWS
    503,  # TWS is out of date and must be upgraded
    504,  # Not connected
}

# Active order statuses
_ACTIVE_STATUSES = {"PendingSubmit", "PreSubmitted", "Submitted"}


class DuplicateOrderError(Exception):
    """Raised when an equivalent open order already exists."""


class OrderManager:
    """
    High-level order management layer.

    Responsibilities:
      - Validate and place orders
      - Prevent duplicate submissions
      - Cancel single or all orders
      - Stay in sync with TWS regardless of source (API, manual, other clients)
      - Fire callbacks on fill / cancel / error events

    Thread safety: all mutations to self._orders are protected by self._lock.
    """

    def __init__(self, client: IBKRClient) -> None:
        self._client = client
        self._ib = client.ib

        # Internal cache: order_id -> Trade
        # ALL reads and writes must hold self._lock
        self._orders: Dict[int, Trade] = {}
        self._lock = threading.Lock()

        # Multi-strategy mode: order_id -> strategy_name (set at place_order time).
        # Read by _trade_to_result / _fill_to_result so OrderResult.strategy_name
        # is populated for every fill — letting StrategyRunner route fills back
        # to the originating strategy. None for un-tagged or external orders.
        self._strategy_name_by_order_id: Dict[int, Optional[str]] = {}

        # Tracks execIds of fills that have been processed (live or replayed).
        # Guarded by self._lock. Used by reconcile_fills() to avoid double-firing
        # on_fill callbacks if a fill arrives both via live event and replay.
        self._seen_exec_ids: set[str] = set()

        # User-registered callbacks
        self._on_fill_callbacks: List[Callable[[OrderResult], None]] = []
        self._on_cancel_callbacks: List[Callable[[OrderResult], None]] = []
        self._on_error_callbacks: List[Callable[[int, int, str], None]] = []

        # Wire all relevant ib_insync events
        self._ib.orderStatusEvent += self._handle_order_status
        self._ib.openOrderEvent += self._handle_open_order
        self._ib.newOrderEvent += self._handle_new_order
        self._ib.cancelOrderEvent += self._handle_cancel_order
        self._ib.errorEvent += self._handle_error
        self._ib.execDetailsEvent += self._handle_exec_details

        # Pull all currently open orders on startup (only if connected)
        if client.is_connected:
            self.sync()

    # ------------------------------------------------------------------
    # Public: sync
    # ------------------------------------------------------------------

    def sync(self) -> int:
        """
        Pull all open orders from TWS (across all sessions and clients).
        Called automatically on init; call manually to re-sync after
        external changes (manual TWS actions, other API clients, etc.).

        Returns:
            Number of open orders found.

        Thread safety: may be called from non-main threads (e.g. ReconnectManager
        daemon). ib_insync internally calls asyncio.get_event_loop() which raises
        RuntimeError in Python 3.12 non-main threads. We detect this and route
        through run_coroutine_threadsafe on the main loop instead (B-08 part 2).
        """
        main_loop = getattr(self._client, "_main_loop", None)
        if (
            threading.current_thread() is not threading.main_thread()
            and main_loop is not None
            and main_loop.is_running()
        ):

            async def _do_sync() -> list:
                # Must use the *Async variant: sync ib_insync wrappers call
                # loop.run_until_complete() internally, which raises "This event
                # loop is already running" because we're already awaiting on it.
                await self._ib.reqAllOpenOrdersAsync()
                await asyncio.sleep(0.5)
                return list(self._ib.openTrades())

            fut = asyncio.run_coroutine_threadsafe(_do_sync(), main_loop)
            # 30s matches ib_insync's own internal request timeout. A TimeoutError
            # here propagates to ReconnectManager._attempt_reconnect() which halts
            # via os._exit(1) — correct: a hung sync means unknown order state.
            open_trades = fut.result(timeout=30)
        else:
            self._ib.reqAllOpenOrders()
            self._ib.sleep(0.5)
            # Fetch trades BEFORE acquiring the lock so event callbacks are not
            # blocked while openTrades() waits on ib_insync's internal state.
            open_trades = list(self._ib.openTrades())
        with self._lock:
            for trade in open_trades:
                self._orders[trade.order.orderId] = trade
            count = len(self._orders)
        logger.debug("Sync complete — %d open order(s) in cache.", count)
        return count

    def reconcile_fills(self) -> int:
        """
        Replay any fills that arrived while we were disconnected.

        Calls ib.fills() (today's fills only) and fires on_fill callbacks for
        any execution whose execId has not been seen by _handle_exec_details.
        Idempotent: safe to call multiple times — already-seen execIds are skipped.

        Returns:
            Number of missed fills replayed (0 if none).

        Note: ib.fills() returns today's fills only. Fills missed across a
        midnight boundary will not be recovered by this method.
        """
        try:
            fills = self._ib.fills()
        except Exception as exc:
            logger.warning("reconcile_fills: ib.fills() failed: %s", exc, exc_info=True)
            return 0

        replayed = 0
        for fill in fills:
            exec_id = fill.execution.execId
            with self._lock:
                if exec_id in self._seen_exec_ids:
                    continue
                self._seen_exec_ids.add(exec_id)
                self._orders.pop(fill.execution.orderId, None)
                # Build the result first (it reads _strategy_name_by_order_id),
                # then drop the strategy_name entry — terminal state, no longer needed.
                result = self._fill_to_result(fill)
                self._strategy_name_by_order_id.pop(fill.execution.orderId, None)

            logger.info(
                "Reconciled missed fill | %s %s x%s @ %.4f | execId=%s",
                result.action,
                result.symbol,
                result.filled,
                result.avg_fill_price or 0.0,
                exec_id,
            )
            for cb in self._on_fill_callbacks:
                try:
                    cb(result)
                except Exception as exc:
                    logger.warning(
                        "on_fill callback raised during fill reconciliation: %s",
                        exc,
                        exc_info=True,
                    )
            replayed += 1

        if replayed:
            logger.info("Fill reconciliation complete — %d missed fill(s) replayed.", replayed)
        else:
            logger.debug("Fill reconciliation: no missed fills.")
        return replayed

    # ------------------------------------------------------------------
    # Public: event subscription
    # ------------------------------------------------------------------

    def on_fill(self, callback: Callable[[OrderResult], None]) -> None:
        """Register a callback fired when an order is fully filled."""
        self._on_fill_callbacks.append(callback)

    def on_cancel(self, callback: Callable[[OrderResult], None]) -> None:
        """Register a callback fired when an order is cancelled (any source)."""
        self._on_cancel_callbacks.append(callback)

    def on_error(self, callback: Callable[[int, int, str], None]) -> None:
        """Register a callback fired on real IBKR errors (reqId, code, message)."""
        self._on_error_callbacks.append(callback)

    # ------------------------------------------------------------------
    # Public: order placement
    # ------------------------------------------------------------------

    def place_order(self, request: OrderRequest, allow_duplicate: bool = False) -> OrderResult:
        """
        Validate, de-duplicate, and submit an order.

        Args:
            request:          Fully populated OrderRequest.
            allow_duplicate:  Skip duplicate check if True (use with caution).

        Returns:
            OrderResult snapshot at submission time.

        Raises:
            DuplicateOrderError: An equivalent open order already exists.
            ConnectionError:     Not connected to IBKR.
            RuntimeError:        Contract qualification failed.
        """
        if not self._client.is_connected:
            raise ConnectionError("Not connected to IBKR.")

        if not allow_duplicate:
            self._check_duplicate(request)

        contract = self._client.qualify_contract(
            Stock(request.symbol, request.exchange, request.currency)
        )

        submitted_at = datetime.now(timezone.utc)
        ib_order = self._build_ib_order(request)
        trade = self._ib.placeOrder(contract, ib_order)
        # Use the client's thread-safe sleep -- ib.sleep() from a daemon thread
        # re-enters loop.run_until_complete on an already-running loop.
        self._client.sleep(0.5)

        # Add to cache AFTER ib.sleep so the newOrderEvent has already fired.
        # If the event fires first, _handle_new_order adds it; this is a safe upsert.
        with self._lock:
            self._orders[trade.order.orderId] = trade
            self._strategy_name_by_order_id[trade.order.orderId] = request.strategy_name

        result = self._trade_to_result(trade, submitted_at=submitted_at)
        logger.info(
            "Order placed | %s %s x%s @ %s | id=%s | status=%s",
            result.action,
            result.symbol,
            result.quantity,
            request.limit_price or "MKT",
            result.order_id,
            result.status,
        )
        return result

    # ------------------------------------------------------------------
    # Public: cancellation
    # ------------------------------------------------------------------

    def cancel_order(self, order_id: int) -> bool:
        """
        Cancel a specific order by its IBKR order ID.

        Returns:
            True if cancellation was sent, False if order not found or not active.
        """
        with self._lock:
            trade = self._orders.get(order_id)
        if trade is None:
            trade = self._find_trade(order_id)
        if trade is None:
            logger.warning("cancel_order: order %s not found in cache or TWS.", order_id)
            return False
        if trade.orderStatus.status not in _ACTIVE_STATUSES:
            logger.warning(
                "cancel_order: order %s is not active (status=%s).",
                order_id,
                trade.orderStatus.status,
            )
            return False
        self._ib.cancelOrder(trade.order)
        self._client.sleep(0.5)
        logger.info(
            "Cancel sent | id=%s | %s %s", order_id, trade.order.action, trade.contract.symbol
        )
        return True

    def cancel_all(self, symbol: Optional[str] = None) -> int:
        """
        Cancel all open orders, optionally filtered by symbol.

        Returns:
            Number of cancellations sent.
        """
        trades = self._active_trades(symbol)
        for trade in trades:
            self._ib.cancelOrder(trade.order)
            logger.info(
                "Cancel sent | id=%s | %s %s",
                trade.order.orderId,
                trade.order.action,
                trade.contract.symbol,
            )
        if trades:
            self._client.sleep(0.5)
        return len(trades)

    # ------------------------------------------------------------------
    # Public: queries
    # ------------------------------------------------------------------

    def get_open_orders(self, symbol: Optional[str] = None) -> List[OrderResult]:
        """Return all currently open orders from cache (optionally filtered by symbol)."""
        if not self._client.is_connected:
            raise ConnectionError("Not connected to IBKR.")
        return [self._trade_to_result(t) for t in self._active_trades(symbol)]

    def get_positions(self) -> List[Position]:
        """Return current portfolio positions."""
        if not self._client.is_connected:
            raise ConnectionError("Not connected to IBKR.")

        def _clean(v) -> Optional[float]:
            """Return None for NaN / zero sentinel values IBKR sends before data arrives."""
            if v is None:
                return None
            try:
                f = float(v)
            except (TypeError, ValueError):
                return None
            return None if math.isnan(f) else f

        return [
            Position(
                symbol=pos.contract.symbol,
                quantity=pos.position,
                avg_cost=pos.averageCost,  # PortfolioItem uses averageCost, not avgCost
                market_price=_clean(pos.marketPrice),
                market_value=_clean(pos.marketValue),
                unrealized_pnl=_clean(pos.unrealizedPNL),
                realized_pnl=_clean(pos.realizedPNL),
                account=pos.account,
            )
            for pos in self._client.get_positions()
        ]

    def has_open_order(self, symbol: str, action: Optional[OrderAction] = None) -> bool:
        """Check whether an active open order exists for a symbol (and optionally a side)."""
        trades = self._active_trades(symbol)
        if action is None:
            return bool(trades)
        return any(t.order.action == action.value for t in trades)

    # ------------------------------------------------------------------
    # Private: internal helpers
    # ------------------------------------------------------------------

    def _active_trades(self, symbol: Optional[str] = None) -> List[Trade]:
        with self._lock:
            trades = [t for t in self._orders.values() if t.orderStatus.status in _ACTIVE_STATUSES]
        if symbol:
            trades = [t for t in trades if t.contract.symbol == symbol.upper()]
        return trades

    def _find_trade(self, order_id: int) -> Optional[Trade]:
        """Fallback: search ib_insync's full trade list."""
        for trade in self._ib.trades():
            if trade.order.orderId == order_id:
                return trade
        return None

    def _check_duplicate(self, request: OrderRequest) -> None:
        if self.has_open_order(request.symbol, request.action):
            raise DuplicateOrderError(
                f"An open {request.action.value} order for {request.symbol} already exists. "
                "Pass allow_duplicate=True to override."
            )

    @staticmethod
    def _build_ib_order(request: OrderRequest) -> Order:
        tif = request.tif.value
        if request.order_type == OrderType.MARKET:
            return MarketOrder(request.action.value, request.quantity, tif=tif)
        if request.order_type == OrderType.LIMIT:
            assert request.limit_price is not None  # validated in OrderRequest.__post_init__
            return LimitOrder(request.action.value, request.quantity, request.limit_price, tif=tif)
        if request.order_type == OrderType.STOP:
            assert request.stop_price is not None  # validated in OrderRequest.__post_init__
            return StopOrder(request.action.value, request.quantity, request.stop_price, tif=tif)
        if request.order_type == OrderType.STOP_LIMIT:
            from ib_insync import StopLimitOrder

            assert request.limit_price is not None and request.stop_price is not None
            return StopLimitOrder(
                request.action.value,
                request.quantity,
                request.limit_price,
                request.stop_price,
                tif=tif,
            )
        raise ValueError(f"Unsupported order type: {request.order_type}")

    def _trade_to_result(
        self,
        trade: Trade,
        submitted_at: Optional[datetime] = None,
    ) -> OrderResult:
        o = trade.order
        s = trade.orderStatus

        # avg_fill_price is 0.0 for unfilled orders — return None instead
        avg_price: Optional[float] = s.avgFillPrice
        if avg_price is None or (
            isinstance(avg_price, float) and (math.isnan(avg_price) or avg_price == 0.0)
        ):
            avg_price = None

        if s.status in OrderStatus._value2member_map_:
            mapped_status = OrderStatus(s.status)
        else:
            logger.warning(
                "Unknown IBKR order status '%s' for order %s — mapping to ERROR. "
                "This may indicate a new TWS version introduced a new status string.",
                s.status,
                o.orderId,
            )
            mapped_status = OrderStatus.ERROR

        # dict.get() is safe under the GIL; avoid acquiring self._lock here
        # because callers (e.g. reconcile_fills) may already hold it.
        strat_name = self._strategy_name_by_order_id.get(o.orderId)
        return OrderResult(
            order_id=o.orderId,
            symbol=trade.contract.symbol,
            action=o.action,
            quantity=o.totalQuantity,
            order_type=o.orderType,
            tif=o.tif,
            status=mapped_status,
            filled=s.filled,
            remaining=s.remaining,
            avg_fill_price=avg_price,
            limit_price=o.lmtPrice if o.lmtPrice else None,
            stop_price=o.auxPrice if o.auxPrice else None,
            submitted_at=submitted_at or datetime.now(timezone.utc),
            strategy_name=strat_name,
        )

    def _fill_to_result(self, fill: Fill) -> OrderResult:
        """Build an OrderResult from an ib_insync Fill (used during reconciliation)."""
        ex = fill.execution
        # dict.get() is safe under the GIL; avoid acquiring self._lock here
        # because callers (e.g. reconcile_fills) may already hold it.
        strat_name = self._strategy_name_by_order_id.get(ex.orderId)
        return OrderResult(
            order_id=ex.orderId,
            symbol=fill.contract.symbol,
            action=_EXEC_SIDE_TO_ACTION.get(ex.side, ex.side),
            quantity=float(ex.shares),
            order_type="MKT",  # original order type is not recoverable from a fill
            tif="GTC",
            status=OrderStatus.FILLED,
            filled=float(ex.shares),
            remaining=0.0,
            avg_fill_price=float(ex.avgPrice) if ex.avgPrice else None,
            limit_price=None,
            stop_price=None,
            submitted_at=datetime.now(timezone.utc),
            strategy_name=strat_name,
        )

    # ------------------------------------------------------------------
    # Private: TWS push event handlers
    # ------------------------------------------------------------------

    def _handle_open_order(self, trade: Trade) -> None:
        """TWS pushed an open order (from reqAllOpenOrders or spontaneous update)."""
        with self._lock:
            self._orders[trade.order.orderId] = trade
        logger.debug(
            "Open order synced | id=%s | %s %s | status=%s",
            trade.order.orderId,
            trade.order.action,
            trade.contract.symbol,
            trade.orderStatus.status,
        )

    def _handle_new_order(self, trade: Trade) -> None:
        """A new order appeared — placed via API or TWS UI."""
        with self._lock:
            self._orders[trade.order.orderId] = trade
        logger.info(
            "New order detected | id=%s | %s %s x%s",
            trade.order.orderId,
            trade.order.action,
            trade.contract.symbol,
            trade.order.totalQuantity,
        )

    def _handle_cancel_order(self, trade: Trade) -> None:
        """An order was cancelled — from any source."""
        with self._lock:
            self._orders.pop(trade.order.orderId, None)
            # Snapshot result inside the lock: Trade is mutable; reading it
            # outside risks a concurrent status update on another thread.
            result = self._trade_to_result(trade)
            # Terminal state — drop the strategy_name entry to bound memory.
            self._strategy_name_by_order_id.pop(trade.order.orderId, None)
        logger.info(
            "Order cancelled | id=%s | %s %s",
            trade.order.orderId,
            trade.order.action,
            trade.contract.symbol,
        )
        for cb in self._on_cancel_callbacks:
            cb(result)

    def _handle_order_status(self, trade: Trade) -> None:
        """Order status changed — update cache and fire callbacks."""
        status = trade.orderStatus.status
        fill_result = None

        with self._lock:
            # Remove cancelled orders; update everything else.
            # Both operations happen under a single lock — no second acquisition,
            # no window where another thread sees a stale or partially-removed entry.
            if status == "Cancelled":
                self._orders.pop(trade.order.orderId, None)
            else:
                self._orders[trade.order.orderId] = trade
            # Snapshot fill result inside the lock while Trade is stable.
            if status == "Filled":
                fill_result = self._trade_to_result(trade)
            # Terminal states drop the strategy_name entry to bound memory.
            # _trade_to_result already read it for fill_result above.
            if status in ("Cancelled", "Filled"):
                self._strategy_name_by_order_id.pop(trade.order.orderId, None)

        if fill_result is not None:
            logger.info(
                "FILLED | %s %s x%s @ %.4f | id=%s",
                trade.order.action,
                trade.contract.symbol,
                trade.orderStatus.filled,
                trade.orderStatus.avgFillPrice,
                trade.order.orderId,
            )
            for cb in self._on_fill_callbacks:
                cb(fill_result)

        # "Cancelled" status here is a duplicate signal — _handle_cancel_order
        # already fires on_cancel callbacks. No second callback needed.

    def _handle_exec_details(self, trade: Trade, fill: Fill) -> None:
        """Record the execId of a live fill so reconcile_fills() can skip it."""
        with self._lock:
            self._seen_exec_ids.add(fill.execution.execId)

    def _handle_error(self, req_id: int, error_code: int, error_string: str, contract) -> None:
        if error_code in _DEBUG_CODES:
            logger.debug("IBKR info | code=%s | %s", error_code, error_string)
            return
        if error_code in _INFO_CODES:
            logger.info("IBKR notice | code=%s | %s", error_code, error_string)
            return
        if error_code in _WARNING_CODES:
            logger.warning(
                "IBKR warning | reqId=%s | code=%s | %s", req_id, error_code, error_string
            )
            return
        if error_code in _CONNECTION_ERROR_CODES:
            logger.error(
                "IBKR connection error | code=%s | %s — TWS connection may be lost.",
                error_code,
                error_string,
            )
            # Always propagate connection errors — callers may want to halt or reconnect.
            for cb in self._on_error_callbacks:
                cb(req_id, error_code, error_string)
            return
        logger.error("IBKR error | reqId=%s | code=%s | %s", req_id, error_code, error_string)
        for cb in self._on_error_callbacks:
            cb(req_id, error_code, error_string)

    # ------------------------------------------------------------------
    # Test / debug utilities
    # ------------------------------------------------------------------

    def _clear_callbacks(self) -> None:
        """
        Remove all registered callbacks (fill, cancel, error).

        Intended for test isolation — prevents callbacks registered in one
        test from firing in subsequent tests.
        """
        self._on_fill_callbacks.clear()
        self._on_cancel_callbacks.clear()
        self._on_error_callbacks.clear()
