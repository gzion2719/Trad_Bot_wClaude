from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

from ib_insync import LimitOrder, MarketOrder, Order, Stock, StopOrder, Trade

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

# IBKR error codes that are informational, not real errors
_INFO_CODES = {
    2104, 2106, 2158, 2119, 10182,  # market data / connectivity notices
    202,                             # order cancelled (expected, not an error)
}

# IBKR error codes that are warnings (not fatal)
_WARNING_CODES = {
    201,   # order rejected
    399,   # order message
    10147, # order not found (already cancelled/filled externally)
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
    """

    def __init__(self, client: IBKRClient) -> None:
        self._client = client
        self._ib = client.ib

        # Internal cache: order_id -> Trade
        # Kept in sync via TWS push events from ALL sources
        self._orders: Dict[int, Trade] = {}

        # User-registered callbacks
        self._on_fill_callbacks: List[Callable[[OrderResult], None]] = []
        self._on_cancel_callbacks: List[Callable[[OrderResult], None]] = []
        self._on_error_callbacks: List[Callable[[int, int, str], None]] = []

        # Wire all relevant ib_insync events
        self._ib.orderStatusEvent += self._handle_order_status
        self._ib.openOrderEvent += self._handle_open_order      # external orders pushed by TWS
        self._ib.newOrderEvent += self._handle_new_order        # new orders from any source
        self._ib.cancelOrderEvent += self._handle_cancel_order  # cancels from any source
        self._ib.errorEvent += self._handle_error

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
        """
        self._ib.reqAllOpenOrders()
        self._ib.sleep(0.5)
        # Rebuild cache from what ib_insync now knows
        for trade in self._ib.openTrades():
            self._orders[trade.order.orderId] = trade
        logger.debug("Sync complete — %d open order(s) in cache.", len(self._orders))
        return len(self._orders)

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

        ib_order = self._build_ib_order(request)
        trade = self._ib.placeOrder(contract, ib_order)
        self._ib.sleep(0.5)

        self._orders[trade.order.orderId] = trade
        result = self._trade_to_result(trade)
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
        trade = self._orders.get(order_id) or self._find_trade(order_id)
        if trade is None:
            logger.warning("cancel_order: order %s not found in cache or TWS.", order_id)
            return False
        if trade.orderStatus.status not in _ACTIVE_STATUSES:
            logger.warning("cancel_order: order %s is not active (status=%s).", order_id, trade.orderStatus.status)
            return False
        self._ib.cancelOrder(trade.order)
        self._ib.sleep(0.5)
        logger.info("Cancel sent | id=%s | %s %s", order_id, trade.order.action, trade.contract.symbol)
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
                trade.order.orderId, trade.order.action, trade.contract.symbol,
            )
        if trades:
            self._ib.sleep(0.5)
        return len(trades)

    # ------------------------------------------------------------------
    # Public: queries
    # ------------------------------------------------------------------

    def get_open_orders(self, symbol: Optional[str] = None) -> List[OrderResult]:
        """Return all currently open orders from cache (optionally filtered by symbol)."""
        return [self._trade_to_result(t) for t in self._active_trades(symbol)]

    def get_positions(self) -> List[Position]:
        """Return current portfolio positions."""
        return [
            Position(
                symbol=pos.contract.symbol,
                quantity=pos.position,
                avg_cost=pos.avgCost,
                market_price=0.0,    # populated when live data feed is added
                market_value=0.0,
                unrealized_pnl=0.0,
                realized_pnl=0.0,
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
        trades = [
            t for t in self._orders.values()
            if t.orderStatus.status in _ACTIVE_STATUSES
        ]
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
            return LimitOrder(request.action.value, request.quantity, request.limit_price, tif=tif)
        if request.order_type == OrderType.STOP:
            return StopOrder(request.action.value, request.quantity, request.stop_price, tif=tif)
        if request.order_type == OrderType.STOP_LIMIT:
            from ib_insync import StopLimitOrder
            return StopLimitOrder(
                request.action.value,
                request.quantity,
                request.limit_price,
                request.stop_price,
                tif=tif,
            )
        raise ValueError(f"Unsupported order type: {request.order_type}")

    @staticmethod
    def _trade_to_result(trade: Trade) -> OrderResult:
        o = trade.order
        s = trade.orderStatus
        return OrderResult(
            order_id=o.orderId,
            symbol=trade.contract.symbol,
            action=o.action,
            quantity=o.totalQuantity,
            order_type=o.orderType,
            tif=o.tif,
            status=OrderStatus(s.status) if s.status in OrderStatus._value2member_map_ else OrderStatus.ERROR,
            filled=s.filled,
            remaining=s.remaining,
            avg_fill_price=s.avgFillPrice,
            limit_price=o.lmtPrice if o.lmtPrice else None,
            stop_price=o.auxPrice if o.auxPrice else None,
        )

    # ------------------------------------------------------------------
    # Private: TWS push event handlers
    # These fire for ALL order changes regardless of origin (API, TWS UI,
    # other API clients) — this is what keeps the cache in sync.
    # ------------------------------------------------------------------

    def _handle_open_order(self, trade: Trade) -> None:
        """TWS pushed an open order (from reqAllOpenOrders or spontaneous update)."""
        self._orders[trade.order.orderId] = trade
        logger.debug(
            "Open order synced | id=%s | %s %s | status=%s",
            trade.order.orderId, trade.order.action,
            trade.contract.symbol, trade.orderStatus.status,
        )

    def _handle_new_order(self, trade: Trade) -> None:
        """A new order appeared (placed via API or TWS UI)."""
        self._orders[trade.order.orderId] = trade
        logger.info(
            "New order detected | id=%s | %s %s x%s",
            trade.order.orderId, trade.order.action,
            trade.contract.symbol, trade.order.totalQuantity,
        )

    def _handle_cancel_order(self, trade: Trade) -> None:
        """An order was cancelled — from any source."""
        self._orders.pop(trade.order.orderId, None)
        result = self._trade_to_result(trade)
        logger.info(
            "Order cancelled (external or API) | id=%s | %s %s",
            trade.order.orderId, trade.order.action, trade.contract.symbol,
        )
        for cb in self._on_cancel_callbacks:
            cb(result)

    def _handle_order_status(self, trade: Trade) -> None:
        """Order status changed — update cache and fire callbacks."""
        self._orders[trade.order.orderId] = trade
        status = trade.orderStatus.status

        if status == "Filled":
            result = self._trade_to_result(trade)
            logger.info(
                "FILLED | %s %s x%s @ %.4f | id=%s",
                trade.order.action, trade.contract.symbol,
                trade.orderStatus.filled, trade.orderStatus.avgFillPrice,
                trade.order.orderId,
            )
            for cb in self._on_fill_callbacks:
                cb(result)

        elif status == "Cancelled":
            # May arrive here AND via _handle_cancel_order — pop is safe (no-op if already removed)
            self._orders.pop(trade.order.orderId, None)

    def _handle_error(self, req_id: int, error_code: int, error_string: str, contract) -> None:
        if error_code in _INFO_CODES:
            logger.debug("IBKR info | code=%s | %s", error_code, error_string)
            return
        if error_code in _WARNING_CODES:
            logger.warning("IBKR warning | reqId=%s | code=%s | %s", req_id, error_code, error_string)
            return
        logger.error("IBKR error | reqId=%s | code=%s | %s", req_id, error_code, error_string)
        for cb in self._on_error_callbacks:
            cb(req_id, error_code, error_string)
