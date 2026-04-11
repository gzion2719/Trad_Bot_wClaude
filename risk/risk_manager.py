from __future__ import annotations

"""
RiskManager — Task 2.2

Sits between Strategy and OrderManager. Every strategy calls
risk_manager.check(request, current_price) before placing an order.
If any rule is violated, RiskViolationError is raised and no order is sent.

Four enforcement levels:
  1. Per-order:     single order value must not exceed max_order_value
  2. Per-symbol:    total symbol exposure must not exceed max_position_value
  3. Portfolio:     daily realized loss must not breach max_daily_loss
  4. Trade setup:   stop-loss and take-profit must meet minimum R/R ratio
                    and the risk per trade must not exceed max_risk_per_trade_pct
                    of total equity (default 2%)

Trade setup validation (call before sizing and placing every BUY):
    rm.validate_setup(
        entry_price=150.00,
        stop_price=145.00,
        take_profit_price=165.00,
        equity=10_000.0,
    )
    # Checks: R/R = (165-150)/(150-145) = 3.0 ✓ (min 3.0)
    # Checks: risk = 5.00/share; at 2% of $10k = $200 max risk

Wiring in main.py:
    rm = RiskManager(client=client, order_manager=om, ...)
    om.on_fill(rm.record_fill)
    # At market open, call rm.reset_daily() to reset counters.
"""

import logging
import math
import threading
from datetime import datetime, timezone
from typing import Optional

from broker.ibkr_client import IBKRClient
from models.order import OrderAction, OrderRequest, OrderResult

logger = logging.getLogger(__name__)


class RiskViolationError(Exception):
    """Raised when an order would violate a risk rule. Never catch silently."""


class RiskManager:
    """
    Pre-trade risk guard.

    All public methods are thread-safe.

    Args:
        client:                  IBKRClient — used to read current positions.
        order_manager:           OrderManager — used to read open orders.
        max_order_value:         Maximum USD value of a single order (qty × price).
                                 Example: 5000.0 → no single order > $5,000.
        max_position_value:      Maximum total USD exposure in one symbol (existing + new).
                                 Example: 10000.0 → never hold more than $10,000 in any stock.
        max_daily_loss:          Maximum USD loss allowed today (negative number).
                                 Example: -500.0 → halt if down $500 on the day.
        max_open_orders:         Maximum number of open orders at any time.
        max_risk_per_trade_pct:  Maximum fraction of total equity that can be lost
                                 if a single trade's stop-loss is hit.
                                 Default 0.02 = 2% (e.g., $20 on a $1,000 account).
                                 Enforced by validate_setup().
        min_reward_risk_ratio:   Minimum reward-to-risk ratio required for every trade.
                                 Default 3.0 = take-profit must be ≥ 3× the stop distance.
                                 Example: entry $100, stop $95 → target must be ≥ $115.
                                 Enforced by validate_setup().
    """

    def __init__(
        self,
        client: IBKRClient,
        order_manager,                          # OrderManager — avoids circular import
        max_order_value: float = 5_000.0,
        max_position_value: float = 10_000.0,
        max_daily_loss: float = -500.0,
        max_open_orders: int = 10,
        max_risk_per_trade_pct: float = 0.02,   # 2% of equity per trade
        min_reward_risk_ratio: float = 3.0,     # minimum 1:3 R/R
    ) -> None:
        if max_daily_loss >= 0:
            raise ValueError("max_daily_loss must be negative (e.g., -500.0)")
        if not (0 < max_risk_per_trade_pct <= 1.0):
            raise ValueError(
                f"max_risk_per_trade_pct must be between 0 and 1.0, "
                f"got {max_risk_per_trade_pct}"
            )
        if min_reward_risk_ratio <= 0:
            raise ValueError(
                f"min_reward_risk_ratio must be positive, got {min_reward_risk_ratio}"
            )

        self._client = client
        self._om = order_manager
        self.max_order_value = max_order_value
        self.max_position_value = max_position_value
        self.max_daily_loss = max_daily_loss
        self.max_open_orders = max_open_orders
        self.max_risk_per_trade_pct = max_risk_per_trade_pct
        self.min_reward_risk_ratio = min_reward_risk_ratio

        self._daily_realized_pnl: float = 0.0
        self._lock = threading.Lock()

        logger.info(
            "RiskManager initialized | max_order=$%.0f | max_position=$%.0f "
            "| max_daily_loss=$%.0f | max_open_orders=%d "
            "| max_risk_per_trade=%.1f%% | min_R/R=1:%.1f",
            max_order_value, max_position_value, max_daily_loss, max_open_orders,
            max_risk_per_trade_pct * 100, min_reward_risk_ratio,
        )

    # ------------------------------------------------------------------
    # Pre-trade check — call this before every place_order()
    # ------------------------------------------------------------------

    def check(self, request: OrderRequest, current_price: float) -> None:
        """
        Validate an order against all risk rules.

        Args:
            request:       The order about to be placed.
            current_price: Current market price of the symbol (used for exposure calc).

        Raises:
            RiskViolationError: If any rule is breached. The order must NOT be placed.
        """
        with self._lock:
            daily_pnl = self._daily_realized_pnl

        # Rule 0: Daily loss ceiling
        if daily_pnl <= self.max_daily_loss:
            raise RiskViolationError(
                f"Daily loss ceiling breached (P&L={daily_pnl:.2f}, "
                f"limit={self.max_daily_loss:.2f}). Trading is halted for today."
            )

        order_value = request.quantity * current_price

        # Rule 1: Single-order value cap
        if order_value > self.max_order_value:
            raise RiskViolationError(
                f"Order value ${order_value:.2f} exceeds max_order_value "
                f"${self.max_order_value:.2f} "
                f"({request.quantity} × {current_price:.2f})."
            )

        # Rule 2: Per-symbol exposure cap (only for BUY orders adding to exposure)
        if request.action == OrderAction.BUY:
            existing_value = self._get_position_value(request.symbol, current_price)
            projected_value = existing_value + order_value
            if projected_value > self.max_position_value:
                raise RiskViolationError(
                    f"Projected position in {request.symbol} would be "
                    f"${projected_value:.2f} (existing=${existing_value:.2f} + "
                    f"new=${order_value:.2f}), exceeding max_position_value "
                    f"${self.max_position_value:.2f}."
                )

        # Rule 3: Open order count cap
        try:
            open_count = len(self._om.get_open_orders())
        except Exception:
            # If we can't read order state, assume the worst and block trading.
            # Trading with unknown open-order count risks exceeding limits.
            open_count = self.max_open_orders
        if open_count >= self.max_open_orders:
            raise RiskViolationError(
                f"Open order count ({open_count}) is at max_open_orders "
                f"({self.max_open_orders}). Cancel some orders before placing new ones."
            )

        logger.debug(
            "Risk check PASSED | %s %s x%s | order_value=$%.2f | daily_pnl=$%.2f",
            request.action.value, request.symbol, request.quantity,
            order_value, daily_pnl,
        )

    # ------------------------------------------------------------------
    # Trade setup validation + sizing — primary entry point for strategies
    # ------------------------------------------------------------------

    def plan_trade(
        self,
        entry_price: float,
        stop_price: float,
        take_profit_price: float,
        equity: float,
        order_action: OrderAction = OrderAction.BUY,
    ) -> int:
        """
        Validate the trade setup and return the correctly sized share count.

        This is the SINGLE method strategies should call before placing any order.
        It atomically:
          1. Validates the R/R ratio and 2% risk rule via validate_setup()
          2. Sizes the position via PositionSizer.risk_based() using this
             RiskManager's max_risk_per_trade_pct — sizing and validation
             always use the same risk percentage (no silent decoupling).

        IMPORTANT — equity must be fresh from the broker at call time:
            equity = float(client.get_account_summary()["NetLiquidation"])
        Do NOT cache equity across bars — it changes as position P&L fluctuates.
        Stale equity produces wrong risk calculations (e.g. $200 budget when
        account has dropped from $10k to $8k intraday).

        Args:
            entry_price:       Planned entry price per share.
            stop_price:        Stop-loss level.
            take_profit_price: Target exit price.
            equity:            Current total account value in USD (fresh from broker).
            order_action:      OrderAction.BUY (long) or OrderAction.SELL (short).

        Returns:
            Number of shares to trade (integer, minimum 1).

        Raises:
            RiskViolationError: If R/R or 2% rule is violated. Do not place the trade.
            ValueError:         If inputs are structurally invalid.

        Example (long):
            shares = rm.plan_trade(
                entry_price=150.0,
                stop_price=145.0,
                take_profit_price=165.0,
                equity=10_000.0,
            )
            # R/R = (165-150)/(150-145) = 3.0 ✓
            # risk/share = $5; max_risk = $200; shares = floor(200/5) = 40

        Example (short):
            shares = rm.plan_trade(
                entry_price=100.0,
                stop_price=105.0,
                take_profit_price=85.0,
                equity=10_000.0,
                order_action=OrderAction.SELL,
            )
            # R/R = (100-85)/(105-100) = 3.0 ✓
            # risk/share = $5; max_risk = $200; shares = floor(200/5) = 40
        """
        from risk.position_sizer import PositionSizer  # avoids circular import at module level
        self.validate_setup(entry_price, stop_price, take_profit_price, equity, order_action)

        if order_action == OrderAction.SELL:
            # Shorts: stop_price > entry_price, so risk_per_share = stop - entry.
            # risk_based() computes (entry_arg - stop_arg) internally, which must
            # be positive. We pass the higher price as "entry" and lower as "stop"
            # so the arithmetic yields the correct short risk distance.
            # Example: entry=100, stop=105 → sizing_high=105, sizing_low=100
            #          risk_based() computes 105 - 100 = $5/share ✓
            sizing_high = stop_price    # short's stop (above entry) → sizer's "entry"
            sizing_low  = entry_price   # short's entry (below stop) → sizer's "stop"
            return PositionSizer.risk_based(
                equity=equity,
                entry_price=sizing_high,
                stop_price=sizing_low,
                risk_pct=self.max_risk_per_trade_pct,
            )

        # Long: stop_price < entry_price, risk_based() uses entry - stop directly.
        return PositionSizer.risk_based(
            equity=equity,
            entry_price=entry_price,
            stop_price=stop_price,
            risk_pct=self.max_risk_per_trade_pct,
        )

    def validate_setup(
        self,
        entry_price: float,
        stop_price: float,
        take_profit_price: float,
        equity: float,
        order_action: OrderAction = OrderAction.BUY,
    ) -> None:
        """
        Validate a planned trade's risk/reward profile before placing the order.

        Prefer calling plan_trade() which atomically validates and sizes.
        Call this directly only if you need validation without sizing.

        Two rules are enforced:

          Rule A — Minimum reward-to-risk ratio (default 1:3):
            Long:  (take_profit - entry) / (entry - stop) >= min_reward_risk_ratio
            Short: (entry - take_profit) / (stop - entry) >= min_reward_risk_ratio
            Example (long): entry=100, stop=95, target=115
            → reward=$15, risk=$5, R/R=3.0 ✓

          Rule B — Maximum risk per trade (default 2% of equity):
            The dollar risk per share if stop is hit must not exceed equity × 2%.
            If 1 share already costs more than the budget, the trade is rejected
            regardless of sizing — the setup is too wide for the account.

        Args:
            entry_price:       Planned entry price per share.
            stop_price:        Stop-loss level (below entry for longs, above for shorts).
            take_profit_price: Target exit price (above entry for longs, below for shorts).
            equity:            Total account value in USD (must be fresh — see plan_trade()).
            order_action:      OrderAction.BUY (long) or OrderAction.SELL (short).

        Raises:
            RiskViolationError: If either rule is breached. Do not place the trade.
            ValueError:         If inputs are structurally invalid.
        """
        if entry_price <= 0:
            raise ValueError(f"entry_price must be positive, got {entry_price}")
        if stop_price <= 0:
            raise ValueError(f"stop_price must be positive, got {stop_price}")
        if take_profit_price <= 0:
            raise ValueError(f"take_profit_price must be positive, got {take_profit_price}")
        if equity <= 0:
            raise ValueError(f"equity must be positive, got {equity}")

        if order_action == OrderAction.BUY:
            if stop_price >= entry_price:
                raise ValueError(
                    f"stop_price ({stop_price:.4f}) must be below entry_price "
                    f"({entry_price:.4f}) for a long (BUY) position."
                )
            if take_profit_price <= entry_price:
                raise ValueError(
                    f"take_profit_price ({take_profit_price:.4f}) must be above "
                    f"entry_price ({entry_price:.4f}) for a long (BUY) position."
                )
            risk_per_share   = entry_price - stop_price
            reward_per_share = take_profit_price - entry_price
        else:  # SHORT / SELL
            if stop_price <= entry_price:
                raise ValueError(
                    f"stop_price ({stop_price:.4f}) must be above entry_price "
                    f"({entry_price:.4f}) for a short (SELL) position."
                )
            if take_profit_price >= entry_price:
                raise ValueError(
                    f"take_profit_price ({take_profit_price:.4f}) must be below "
                    f"entry_price ({entry_price:.4f}) for a short (SELL) position."
                )
            risk_per_share   = stop_price - entry_price
            reward_per_share = entry_price - take_profit_price

        rr_ratio = reward_per_share / risk_per_share

        # Rule A: R/R ratio
        if rr_ratio < self.min_reward_risk_ratio:
            raise RiskViolationError(
                f"Trade R/R ratio {rr_ratio:.2f} is below the minimum "
                f"{self.min_reward_risk_ratio:.1f}. "
                f"Entry={entry_price:.4f} | Stop={stop_price:.4f} "
                f"(risk=${risk_per_share:.4f}/share) | "
                f"Target={take_profit_price:.4f} (reward=${reward_per_share:.4f}/share). "
                f"Adjust target or tighten stop."
            )

        # Rule B: 2% risk rule — even 1 share must fit within the budget
        max_risk_dollars = equity * self.max_risk_per_trade_pct
        if risk_per_share > max_risk_dollars:
            raise RiskViolationError(
                f"Stop distance ${risk_per_share:.4f}/share exceeds the maximum "
                f"risk budget ${max_risk_dollars:.2f} "
                f"({self.max_risk_per_trade_pct * 100:.1f}% of ${equity:.2f} equity). "
                f"Even 1 share would risk ${risk_per_share:.4f} — tighten the stop or "
                f"wait for a lower-risk entry."
            )

        logger.debug(
            "Trade setup VALID | %s | entry=%.4f | stop=%.4f | target=%.4f "
            "| R/R=1:%.2f (min 1:%.1f) | risk/share=$%.4f | max_risk=$%.2f",
            order_action.value, entry_price, stop_price, take_profit_price,
            rr_ratio, self.min_reward_risk_ratio,
            risk_per_share, max_risk_dollars,
        )

    # ------------------------------------------------------------------
    # Fill tracker — wire to om.on_fill()
    # ------------------------------------------------------------------

    def record_fill(self, result: OrderResult) -> None:
        """
        Hook called on every fill. Logs the event but does NOT update daily P&L.

        Wire this up in main.py:
            om.on_fill(risk_manager.record_fill)

        WHY daily P&L is NOT tracked here:
            Computing realized P&L from fills requires knowing the cost basis of
            the shares being sold. That information lives in the IBKR portfolio
            API, not in the fill event itself. Reconstructing it here would
            duplicate logic and risk drift.

            Instead, push accurate P&L from IBKR account data by calling
            update_daily_pnl() periodically (e.g., every minute):
                pnl = float(client.get_account_summary()["RealizedPnL"])
                rm.update_daily_pnl(pnl)

            reset_daily() must be called at market open to zero out the counter.
        """
        if result.avg_fill_price is None:
            return   # unfilled — nothing to record

        logger.debug(
            "Fill recorded | %s %s x%s @ %.4f",
            result.action, result.symbol, result.filled, result.avg_fill_price,
        )

    def update_daily_pnl(self, pnl: float) -> None:
        """
        Directly set today's realized P&L from an external source
        (e.g., read from IBKR account summary).

        Call this periodically (e.g., every minute) for accurate loss tracking.

        Args:
            pnl: Today's net realized P&L in USD (negative = loss).
        """
        with self._lock:
            self._daily_realized_pnl = pnl

        if pnl <= self.max_daily_loss:
            logger.warning(
                "Daily loss ceiling BREACHED: P&L=$%.2f, limit=$%.2f. "
                "All new orders will be rejected until reset_daily() is called.",
                pnl, self.max_daily_loss,
            )

    def reset_daily(self) -> None:
        """
        Reset daily P&L counter. Call this at market open each day.

        In main.py you can schedule this with a timer or by checking
        the current time at the start of each on_tick().
        """
        with self._lock:
            self._daily_realized_pnl = 0.0
        logger.info("RiskManager daily counters reset (market open).")

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    def is_halted(self) -> bool:
        """
        Returns True if the daily loss ceiling is breached.

        Strategies should check this at the top of on_tick() and return
        early if True — no new orders should be placed while halted.
        """
        with self._lock:
            return self._daily_realized_pnl <= self.max_daily_loss

    def daily_pnl(self) -> float:
        """Return today's tracked realized P&L."""
        with self._lock:
            return self._daily_realized_pnl

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_position_value(self, symbol: str, current_price: float) -> float:
        """
        Return the current market value of an existing position in this symbol.
        Returns 0.0 if no position or if the value can't be determined.
        """
        try:
            positions = self._om.get_positions()
            for pos in positions:
                if pos.symbol == symbol.upper():
                    # Use market_value from IBKR if available, else calculate
                    if pos.market_value is not None and not math.isnan(pos.market_value):
                        return abs(float(pos.market_value))
                    # Fallback: quantity × current price
                    return abs(pos.quantity * current_price)
        except Exception as exc:
            logger.debug("Could not read position for %s: %s", symbol, exc)
        return 0.0
