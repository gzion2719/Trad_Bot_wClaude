from __future__ import annotations

"""
PingPongTest -- a deliberately trivial TEST-ONLY strategy.

Purpose
-------
Generate a steady, predictable stream of fills on the paper account so the
bot and the dashboard can be observed end-to-end. It alternates a 1-share
BUY and a 1-share SELL every scheduler tick (wired as Interval(300) in
REGISTRY -> one order every 5 minutes during market hours).

**P&L is explicitly NOT a goal.** This is not a real strategy. It exists
only to make the bot visibly "do something" and to let the operator verify
that fills, per-strategy attribution, and the dashboard all reflect reality.

Independence
------------
Fully independent of SMACrossover-QQQ and RSI2MR-SPY: own symbol (AAPL --
the MS-D registry guard forbids sharing a symbol), own RiskManager (built
by StrategyRunner from this strategy's RiskCaps), own scheduler thread,
and fills routed back by `OrderResult.strategy_name`.

Design notes (from the pre-implementation code review)
------------------------------------------------------
- Orders use `tif=DAY`, not the GTC default: a market order placed near or
  after the close must auto-expire at session end rather than rest as a
  PreSubmitted order forever (which would deadlock the open-order guard).
- An explicit market-hours gate (`_is_market_open`) keeps the 24/7 Interval
  scheduler from placing orders outside RTH. Holiday-unaware by design --
  a DAY order placed on a holiday simply expires and clears via on_cancel.
- A short-lived `_order_pending` flag (cleared by on_fill / on_error /
  on_cancel, with a timeout self-heal) closes the double-order race in the
  window where an order is Filled (no longer "open") but the position is
  not yet visible from `get_positions()`.
- Position state is tracked authoritatively in `on_fill`. The broker is
  reconciled only at `on_start` (and on a pending-timeout): an existing
  AAPL position of exactly `qty` shares is adopted; any other holding
  (wrong size, or short) disables the strategy so it never liquidates an
  unexpected position on a shared paper account.
- No state file. Restarts self-correct via the on_start reconcile, which
  also recovers a cost basis from the broker `avg_cost`. A restart in the
  brief window between a BUY fill and the reconcile can leave one SELL
  without a cost basis -- acceptable, since P&L is not this strategy's job.

Turning it off
--------------
Delete the `PingPongTest-AAPL` entry from `STRATEGY_METADATA` and the
matching line from `_STRATEGY_CLASSES`, then redeploy. No state to clean up.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from broker.order_manager import DuplicateOrderError
from models.order import OrderAction, OrderRequest, OrderResult, OrderType, TimeInForce
from risk.risk_manager import RiskViolationError
from strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

# Force-clear a pending order after this long with no fill/cancel/error event,
# then re-reconcile from the broker. Comfortably above the reconnect wait (60s)
# and below the 300s scheduler interval, so a stuck order is caught next tick.
_PENDING_TIMEOUT_SECONDS = 90.0


class PingPongTest(BaseStrategy):
    """
    Test-only strategy: alternates BUY 1 / SELL 1 every tick during RTH.

    Args:
        qty: Shares per order. Default 1. Kept tiny on purpose -- this
             strategy's job is visibility, not exposure.
    """

    def __init__(
        self,
        client,
        order_manager,
        risk_manager=None,
        reconnect=None,
        feed=None,
        symbol: str = "",
        qty: int = 1,
    ) -> None:
        super().__init__(
            client=client,
            order_manager=order_manager,
            risk_manager=risk_manager,
            reconnect=reconnect,
            feed=feed,
            symbol=symbol,
        )
        if qty < 1:
            raise ValueError(f"qty must be >= 1, got {qty}")
        self._qty: int = int(qty)

        self._in_position: bool = False
        self._position_shares: int = 0
        self._entry_price: float = 0.0  # avg BUY fill price; stamped onto SELL cost_basis

        # Pending-order guard (CR-H1): set when an order is in flight, cleared
        # by on_fill / on_error / on_cancel or a timeout self-heal.
        self._order_pending: bool = False
        self._pending_order_id: Optional[int] = None
        self._pending_since: Optional[datetime] = None

        # Set True if on_start finds an unexpected AAPL holding -- the strategy
        # then no-ops until restarted with a clean position.
        self._disabled: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_start(self) -> None:
        # Wire order-error / cancel hooks so a rejected or expired order clears
        # the pending flag (MockOrderManager in backtests may not expose these).
        if self.om is not None:
            try:
                self.om.on_error(self._on_order_error)
            except AttributeError:
                pass
            try:
                self.om.on_cancel(self._on_order_cancel)
            except AttributeError:
                pass

        self._reconcile_from_broker()
        logger.info(
            "%s starting | symbol=%s qty=%d -- interval-driven TEST strategy "
            "(alternating BUY/SELL; P&L is not a goal)%s",
            self.name,
            self.symbol,
            self._qty,
            " [DISABLED -- unexpected position]" if self._disabled else "",
        )

    def on_stop(self) -> None:
        logger.info(
            "%s stopped | symbol=%s in_position=%s",
            self.name,
            self.symbol,
            self._in_position,
        )

    # ------------------------------------------------------------------
    # Fill tracking -- auto-wired by BaseStrategy.__init__ (strategy-filtered)
    # ------------------------------------------------------------------

    def on_fill(self, result: OrderResult) -> None:
        # on_fill runs inside OrderManager's callback loop; an exception here
        # would propagate into the scheduler's consecutive-error budget. Never
        # raise from this method.
        try:
            if not result.is_filled:
                return
            if result.symbol != self.symbol:  # defensive -- already strategy-filtered
                return

            self._clear_pending()

            if result.action == OrderAction.BUY.value:
                self._in_position = True
                self._position_shares = int(result.filled)
                if result.avg_fill_price is not None:
                    self._entry_price = float(result.avg_fill_price)
                logger.info(
                    "%s: BUY filled %s x%d @ %.2f",
                    self.name,
                    self.symbol,
                    self._position_shares,
                    result.avg_fill_price or 0.0,
                )
            elif result.action == OrderAction.SELL.value:
                # Stamp cost_basis so TradeLog records realized P&L (the
                # dashboard reads it). Must happen before the trade_log hook
                # per the callback-order contract in runtime/strategy_runner.py.
                if self._entry_price > 0:
                    result.cost_basis = self._entry_price
                logger.info(
                    "%s: SELL filled %s x%d @ %.2f (cost_basis=%s)",
                    self.name,
                    self.symbol,
                    int(result.filled),
                    result.avg_fill_price or 0.0,
                    f"{self._entry_price:.2f}" if self._entry_price > 0 else "none",
                )
                self._in_position = False
                self._position_shares = 0
                self._entry_price = 0.0
        except Exception as exc:  # pragma: no cover - defensive, on_fill must not raise
            logger.error("%s: on_fill error (non-fatal) -- %s", self.name, exc)

    def _on_order_error(self, req_id: int, code: int, msg: str) -> None:
        """Clear the pending flag if OUR in-flight order was rejected.

        The req_id match is intentional: on_error fires account-wide, so we
        only clear for our own order. If a clear is ever missed (id mismatch,
        connectivity-code error with no order id), the 90s pending-timeout in
        on_tick is the backstop.
        """
        if self._order_pending and req_id == self._pending_order_id:
            logger.warning(
                "%s: pending order %s errored (code=%d msg=%s) -- clearing pending.",
                self.name,
                req_id,
                code,
                msg,
            )
            self._clear_pending()

    def _on_order_cancel(self, result: OrderResult) -> None:
        """Clear the pending flag if OUR in-flight order was cancelled/expired."""
        if self._order_pending and result.order_id == self._pending_order_id:
            logger.warning(
                "%s: pending order %s cancelled/expired -- clearing pending.",
                self.name,
                result.order_id,
            )
            self._clear_pending()

    # ------------------------------------------------------------------
    # Main tick
    # ------------------------------------------------------------------

    def on_tick(self) -> None:
        if self._disabled:
            return
        if self.reconnect and not self.reconnect.wait_for_connection(timeout=60):
            return
        if self.risk_manager and self.risk_manager.is_halted():
            return

        # Pending-order guard: never place a second order while one is in
        # flight. Self-heal if no fill/cancel/error event ever arrived.
        if self._order_pending:
            if self._pending_age_seconds() < _PENDING_TIMEOUT_SECONDS:
                return
            logger.warning(
                "%s: order %s pending > %.0fs with no fill/cancel/error -- "
                "force-clearing and re-reconciling from the broker.",
                self.name,
                self._pending_order_id,
                _PENDING_TIMEOUT_SECONDS,
            )
            self._clear_pending()
            self._reconcile_from_broker()
            if self._disabled:
                return
            # Only place again THIS tick if the reconcile positively confirmed
            # a held position (-> a clean SELL). If the broker shows flat, the
            # stuck order may have filled without the portfolio snapshot
            # catching up yet -- the exact duplicate-order window the pending
            # flag exists to close (post-impl CR H1). Return and let the next
            # tick (300s later, ample settle time) place from settled truth.
            if not self._in_position:
                return

        if not self._is_market_open():
            logger.debug("%s: market closed -- skipping tick.", self.name)
            return

        try:
            price = self.client.get_market_price(self.symbol)
        except (ValueError, RuntimeError) as exc:
            logger.debug("%s: no price for %s (%s) -- skipping tick.", self.name, self.symbol, exc)
            return
        except Exception as exc:  # noqa: BLE001 - on_tick must never raise (scheduler budget)
            logger.error(
                "%s: unexpected error fetching price for %s -- skipping tick: %s",
                self.name,
                self.symbol,
                exc,
            )
            return

        action = OrderAction.SELL if self._in_position else OrderAction.BUY
        quantity = self._position_shares if self._in_position else self._qty
        if quantity < 1:
            logger.warning("%s: computed quantity %d < 1 -- skipping tick.", self.name, quantity)
            return

        request = OrderRequest(
            symbol=self.symbol,
            action=action,
            quantity=quantity,
            order_type=OrderType.MARKET,
            tif=TimeInForce.DAY,
        )

        # Arm the pending guard BEFORE submitting. place_order's internal
        # sleep(0.5) yields to the IB event loop, where a fast MKT fill on
        # a liquid symbol can fire on_fill *before* place_order returns.
        # on_fill calls _clear_pending; if we set _order_pending=True after
        # safe_place_order, we overwrite that clear and lock the strategy
        # out until the 90s timeout self-heal (and even then only on alternate
        # ticks). Order matters: pending_since must be set before order_pending
        # so the timeout calculation in _pending_age_seconds is valid the
        # instant on_tick on another thread could read it (currently single-
        # threaded, but cheap insurance).
        self._pending_since = datetime.now(timezone.utc)
        self._pending_order_id = None
        self._order_pending = True
        try:
            result = self.safe_place_order(request, current_price=price)
        except RiskViolationError as exc:
            self._clear_pending()
            logger.warning("%s: %s order blocked by risk check -- %s", self.name, action.value, exc)
            return
        except DuplicateOrderError as exc:
            self._clear_pending()
            logger.warning("%s: %s order rejected as duplicate -- %s", self.name, action.value, exc)
            return
        except Exception as exc:
            self._clear_pending()
            logger.error("%s: %s order failed to place -- %s", self.name, action.value, exc)
            return

        # Only stamp the order_id if pending is still set. If on_fill fired
        # during safe_place_order's internal sleep, _clear_pending() already
        # ran -- do NOT resurrect the flag with a now-terminal order id.
        if self._order_pending:
            self._pending_order_id = result.order_id
        logger.info(
            "%s: %s %s x%d queued (order %s) @ ~%.2f",
            self.name,
            action.value,
            self.symbol,
            quantity,
            result.order_id,
            price,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _clear_pending(self) -> None:
        self._order_pending = False
        self._pending_order_id = None
        self._pending_since = None

    def _pending_age_seconds(self) -> float:
        if self._pending_since is None:
            return 0.0
        return (datetime.now(timezone.utc) - self._pending_since).total_seconds()

    def _is_market_open(self) -> bool:
        """
        True during regular US equity trading hours (Mon-Fri 09:30-16:00 ET).

        Holiday-unaware by design: a DAY-tif order placed on a holiday simply
        expires unfilled at session close and clears _order_pending via the
        on_cancel hook. A full holiday calendar is overkill for a test-only
        strategy (pre-impl CR L3).
        """
        try:
            import zoneinfo

            now_et = datetime.now(zoneinfo.ZoneInfo("America/New_York"))
        except Exception as exc:  # pragma: no cover - tzdata is required by main.py
            logger.warning("%s: timezone lookup failed (%s) -- skipping tick.", self.name, exc)
            return False
        if now_et.weekday() >= 5:  # Saturday / Sunday
            return False
        minutes = now_et.hour * 60 + now_et.minute
        return 9 * 60 + 30 <= minutes < 16 * 60

    def _reconcile_from_broker(self) -> None:
        """
        Sync in-memory position state with the broker.

        Adopts an existing AAPL position of exactly `qty` shares (recovering a
        cost basis from the broker avg_cost). Any other holding -- a different
        size, or a short -- disables the strategy so it never liquidates an
        unexpected position on a shared paper account (pre-impl CR H3/M1).
        """
        if self.om is None:
            return
        try:
            positions = self.om.get_positions()
        except Exception as exc:
            logger.warning("%s: position reconcile failed (%s) -- assuming flat.", self.name, exc)
            return

        held = 0
        avg_cost = 0.0
        for pos in positions:
            if pos.symbol == self.symbol:
                held = int(pos.quantity)
                avg_cost = float(pos.avg_cost or 0.0)
                break

        if held == 0:
            self._in_position = False
            self._position_shares = 0
            self._entry_price = 0.0
            return

        if held == self._qty:
            self._in_position = True
            self._position_shares = held
            self._entry_price = avg_cost if avg_cost > 0 else 0.0
            logger.warning(
                "%s: adopted existing %s position x%d (entry_price=%s).",
                self.name,
                self.symbol,
                held,
                f"{self._entry_price:.2f}" if self._entry_price > 0 else "unknown",
            )
            return

        # Unexpected holding (wrong size or short) -- do NOT trade it.
        self._disabled = True
        logger.error(
            "%s: broker shows a %s position of %d shares (expected 0 or %d). "
            "Disabling this test strategy -- it will not trade until restarted "
            "with a clean position. Investigate the paper account.",
            self.name,
            self.symbol,
            held,
            self._qty,
        )

    # ------------------------------------------------------------------
    # Strategy metadata
    # ------------------------------------------------------------------

    @property
    def params(self) -> dict:
        return {"symbol": self.symbol, "qty": self._qty}
