from __future__ import annotations

"""
BacktestPortfolio — Task 3.3

Simulates a brokerage account during a backtest. Tracks cash, positions,
fills, and the equity curve.

Not used directly by strategies — injected into MockOrderManager which
exposes the same interface as the real OrderManager.
"""

import logging
import math
from datetime import datetime, timezone
from typing import Dict, List, Optional

from models.order import OrderAction, OrderResult, OrderStatus, Position

logger = logging.getLogger(__name__)


class BacktestPortfolio:
    """
    Simulated portfolio for backtesting.

    Tracks:
      - Cash balance
      - Share positions per symbol
      - All fills (as OrderResult objects)
      - Equity curve (snapshot after each bar)

    Args:
        initial_capital: Starting cash in USD.
        commission:      Flat fee per trade in USD (default $1.00).
                         Set to 0.0 for commission-free simulation.
    """

    def __init__(
        self,
        initial_capital: float = 100_000.0,
        commission: float = 1.0,
    ) -> None:
        self.initial_capital = initial_capital
        self.commission = commission

        self._cash: float = initial_capital
        self._positions: Dict[str, float] = {}   # symbol → shares (float for fractional)
        self._avg_cost: Dict[str, float] = {}    # symbol → average cost per share
        self._fills: List[OrderResult] = []
        self.equity_curve: List[float] = []      # equity after each bar — set by engine
        self._current_prices: Dict[str, float] = {}

    # ------------------------------------------------------------------
    # Fills
    # ------------------------------------------------------------------

    def fill(
        self,
        symbol: str,
        action: OrderAction,
        quantity: float,
        price: float,
        order_id: int,
        submitted_at: Optional[datetime] = None,
    ) -> OrderResult:
        """
        Execute a simulated fill. Updates cash and positions.

        Returns the OrderResult for the fill so callbacks can fire.
        """
        cost = quantity * price
        commission = self.commission

        if action == OrderAction.BUY:
            total_cost = cost + commission
            if total_cost > self._cash:
                logger.warning(
                    "Backtest: insufficient cash for %s x%s @ %.2f "
                    "(need $%.2f, have $%.2f) — order skipped.",
                    symbol, quantity, price, total_cost, self._cash,
                )
                # Return a rejected result instead of crashing
                return OrderResult(
                    order_id=order_id, symbol=symbol, action=action.value,
                    quantity=quantity, order_type="LMT", tif="GTC",
                    status=OrderStatus.INACTIVE,
                    filled=0, remaining=quantity,
                    avg_fill_price=None, limit_price=None, stop_price=None,
                    submitted_at=submitted_at or datetime.now(timezone.utc),
                )
            self._cash -= total_cost
            prev_qty  = self._positions.get(symbol, 0.0)
            prev_cost = self._avg_cost.get(symbol, 0.0)
            new_qty   = prev_qty + quantity
            # Weighted average cost basis
            self._avg_cost[symbol] = (
                (prev_qty * prev_cost + quantity * price) / new_qty
                if new_qty > 0 else 0.0
            )
            self._positions[symbol] = new_qty

        elif action == OrderAction.SELL:
            held = self._positions.get(symbol, 0.0)
            actual_qty = min(quantity, held)   # can't sell more than we hold
            if actual_qty <= 0:
                logger.warning(
                    "Backtest: no position in %s to sell — order skipped.", symbol
                )
                return OrderResult(
                    order_id=order_id, symbol=symbol, action=action.value,
                    quantity=quantity, order_type="LMT", tif="GTC",
                    status=OrderStatus.INACTIVE,
                    filled=0, remaining=quantity,
                    avg_fill_price=None, limit_price=None, stop_price=None,
                    submitted_at=submitted_at or datetime.now(timezone.utc),
                )
            proceeds = actual_qty * price - commission
            self._cash += proceeds
            self._positions[symbol] = held - actual_qty
            if self._positions[symbol] <= 0:
                self._positions.pop(symbol, None)
                self._avg_cost.pop(symbol, None)
            quantity = actual_qty   # reflect actual fill size

        result = OrderResult(
            order_id=order_id,
            symbol=symbol,
            action=action.value,
            quantity=quantity,
            order_type="LMT",
            tif="GTC",
            status=OrderStatus.FILLED,
            filled=quantity,
            remaining=0,
            avg_fill_price=price,
            limit_price=None,
            stop_price=None,
            submitted_at=submitted_at or datetime.now(timezone.utc),
        )
        self._fills.append(result)

        logger.debug(
            "Backtest fill: %s %s x%.0f @ %.4f | cash=%.2f",
            action.value, symbol, quantity, price, self._cash,
        )
        return result

    # ------------------------------------------------------------------
    # Pricing & equity
    # ------------------------------------------------------------------

    def update_prices(self, prices: Dict[str, float]) -> None:
        """Update mark-to-market prices. Called by the engine each bar."""
        self._current_prices.update(prices)

    def current_equity(self) -> float:
        """Cash + market value of all positions."""
        position_value = sum(
            qty * self._current_prices.get(sym, self._avg_cost.get(sym, 0.0))
            for sym, qty in self._positions.items()
        )
        return self._cash + position_value

    def snapshot_equity(self) -> None:
        """Append current equity to the equity curve. Called by engine each bar."""
        self.equity_curve.append(self.current_equity())

    # ------------------------------------------------------------------
    # Queries (mirrors OrderManager's interface for strategies)
    # ------------------------------------------------------------------

    def get_positions(self) -> List[Position]:
        """Return current simulated positions."""
        return [
            Position(
                symbol=sym,
                quantity=qty,
                avg_cost=self._avg_cost.get(sym, 0.0),
                market_price=self._current_prices.get(sym),
                market_value=qty * self._current_prices.get(sym, 0.0) if sym in self._current_prices else None,
                unrealized_pnl=(
                    qty * (self._current_prices[sym] - self._avg_cost.get(sym, 0.0))
                    if sym in self._current_prices else None
                ),
                realized_pnl=None,   # tracked at portfolio level, not per-position
                account="BACKTEST",
            )
            for sym, qty in self._positions.items()
            if qty > 0
        ]

    def get_fills(self) -> List[OrderResult]:
        """Return all fills recorded during the backtest."""
        return list(self._fills)

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def total_return(self) -> float:
        """Total return as a fraction, e.g. 0.15 = 15%."""
        return (self.current_equity() - self.initial_capital) / self.initial_capital
