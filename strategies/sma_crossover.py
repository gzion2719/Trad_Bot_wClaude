from __future__ import annotations

"""
SMACrossover -- Sprint 4.2 (post-architect-review)

10-day / 30-day Simple Moving Average crossover on daily bars.

Signal logic
------------
  BUY  when the fast SMA (10) crosses ABOVE the slow SMA (30)
  SELL when the fast SMA (10) crosses BELOW the slow SMA (30)
       OR when price closes below the stop price

Stop price
----------
  Lowest close of the 5 bars before entry minus a 0.5% buffer.
  Fallback: 3% below entry if the swing-low stop is < 1% distance.
  A broker-side GTC STOP order is placed immediately after the BUY fills
  (live only) so intraday gaps are covered even if the bot is offline.

Target price
------------
  Constructed as entry + 3 * (entry - stop) and passed to plan_trade() to
  satisfy the 1:3 R/R infrastructure rule.

  NOTE (H4): this strategy does NOT exit at the target price -- it exits
  on cross-down or stop hit only. The target is used purely so plan_trade()
  enforces the 2% max-risk sizing rule (which IS enforced). The 1:3 R/R
  guard is tautological here because target is always built to be exactly 3x.
  Future: add a real OCA bracket (STOP + LIMIT) to make the R/R enforceable.

RiskManager caps (C3)
---------------------
  Default RiskManager caps (max_order_value=$5k, max_position_value=$10k)
  are too low for QQQ on a $100k account. Wire with these values in main.py:

      rm = RiskManager(
          client=client, order_manager=om,
          max_order_value=120_000,
          max_position_value=100_000,
          max_daily_loss=-2_000,
          max_risk_per_trade_pct=0.02,
          min_reward_risk_ratio=3.0,
      )

Live wiring in main.py
-----------------------
    from strategies.sma_crossover import SMACrossover
    strategy = SMACrossover(
        client=client, order_manager=om, risk_manager=rm,
        reconnect=reconnect, feed=feed, symbol="QQQ",
        sma_fast=10, sma_slow=30,
    )
    scheduler = BarScheduler(strategy, interval_seconds=86400)
    scheduler.start()

Backtest
--------
    from backtester.engine import BacktestEngine
    from data.historical import HistoricalDataLoader
    from strategies.sma_crossover import SMACrossover

    df = HistoricalDataLoader.load_yfinance("QQQ", "2020-01-01", "2025-01-01")
    engine = BacktestEngine(
        strategy_class=SMACrossover,
        data=df, symbol="QQQ", initial_capital=100_000,
        strategy_kwargs={"sma_fast": 10, "sma_slow": 30},
    )
    engine.run().print_summary()
"""

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from models.order import (
    OrderAction,
    OrderRequest,
    OrderResult,
    OrderType,
    TimeInForce,
)
from strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

# How many days of history to fetch from yfinance on startup / each live tick.
_HISTORY_DAYS = 90


class SMACrossover(BaseStrategy):
    """
    SMA crossover strategy -- 10/30 daily bars, single long position at a time.

    Args:
        sma_fast:        Fast SMA period. Default 10.
        sma_slow:        Slow SMA period. Default 30.
        initial_capital: Equity proxy used in backtests (no broker available).
                         Ignored in live -- equity is fetched fresh from broker.
    """

    def __init__(
        self,
        client,
        order_manager,
        risk_manager=None,
        reconnect=None,
        feed=None,
        symbol: str = "",
        sma_fast: int = 10,
        sma_slow: int = 30,
        initial_capital: float = 100_000.0,
    ) -> None:
        super().__init__(
            client=client,
            order_manager=order_manager,
            risk_manager=risk_manager,
            reconnect=reconnect,
            feed=feed,
            symbol=symbol,
        )
        self._sma_fast = sma_fast
        self._sma_slow = sma_slow
        self._initial_capital = initial_capital

        self._closes: List[float] = []  # rolling daily close history

        self._in_position: bool = False  # True only after BUY fill confirmed
        self._entry_pending: bool = False  # True between BUY placement and fill (H3)
        self._position_shares: int = 0  # actual filled quantity (set in on_fill)
        self._stop_price: float = 0.0  # planned stop, set at entry
        self._stop_order_id: Optional[int] = None  # broker-side STOP order ID (C2)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_start(self) -> None:
        # Step 1 (C4 / NEW-3): seed close history FIRST so _calc_stop() has data
        # when we find a reconciled position one step below.
        if self.client is not None:
            self._refresh_closes()

        # Step 2 (C4 / NEW-3): reconcile any position held before this session.
        if self.om is not None:
            try:
                for pos in self.om.get_positions():
                    if pos.symbol == self.symbol and pos.quantity > 0:
                        self._in_position = True
                        self._position_shares = int(pos.quantity)
                        logger.warning(
                            "%s: reconciled existing position %s x%d from broker.",
                            self.name,
                            self.symbol,
                            self._position_shares,
                        )

                        if self.client is not None:
                            # Fix #1 (Q2-hardened): cancel ANY surviving SELL order
                            # from the prior session BEFORE placing a new stop.
                            # Original Fix #1 only cancelled STOP orders; a pending
                            # SELL MARKET from a prior _exit() would survive and, once
                            # the new STOP is placed, both could fill → short flip.
                            for existing in self.om.get_open_orders(symbol=self.symbol):
                                if existing.action == OrderAction.SELL.value:
                                    try:
                                        self.om.cancel_order(existing.order_id)
                                        logger.info(
                                            "%s: cancelled pre-existing SELL order %d "
                                            "(type=%s) from prior session.",
                                            self.name,
                                            existing.order_id,
                                            existing.order_type,
                                        )
                                    except Exception as exc2:
                                        logger.warning(
                                            "%s: could not cancel pre-existing SELL %d: %s",
                                            self.name,
                                            existing.order_id,
                                            exc2,
                                        )

                            # Determine stop price for the reconciled position.
                            # Primary: swing-low of recent closes (market structure).
                            # Fix #2 fallback: avg_cost * 0.97 when closes unavailable
                            # (yfinance down at startup) -- ensures the position is
                            # never left naked regardless of data-fetch outcome.
                            stop: float = 0.0
                            stop_source: str = ""
                            if self._closes:
                                latest = self._closes[-1]
                                candidate = self._calc_stop(latest)
                                if candidate < latest:
                                    stop = candidate
                                    stop_source = "swing-low"
                                else:
                                    logger.warning(
                                        "%s: swing-low stop %.2f >= latest close %.2f "
                                        "-- using avg_cost fallback.",
                                        self.name,
                                        candidate,
                                        latest,
                                    )

                            if stop == 0.0 and pos.avg_cost > 0:
                                # Fix Q3: guard that fallback stop never lands above
                                # current price.  If closes are available, floor at
                                # latest_close * 0.97 so a deeply unrealised position
                                # doesn't receive a stop above market.
                                candidate = pos.avg_cost * 0.97
                                if self._closes:
                                    candidate = min(candidate, self._closes[-1] * 0.97)
                                stop = candidate
                                stop_source = "avg_cost*0.97 (fallback)"

                            if stop > 0:
                                self._stop_price = stop
                                stop_req = OrderRequest(
                                    symbol=self.symbol,
                                    action=OrderAction.SELL,
                                    quantity=self._position_shares,
                                    order_type=OrderType.STOP,
                                    stop_price=stop,
                                    tif=TimeInForce.GTC,
                                )
                                try:
                                    sr = self.om.place_order(stop_req, allow_duplicate=True)
                                    self._stop_order_id = sr.order_id
                                    logger.warning(
                                        "%s: protective STOP placed for reconciled "
                                        "position %s x%d @ %.2f [%s] (order %d).",
                                        self.name,
                                        self.symbol,
                                        self._position_shares,
                                        stop,
                                        stop_source,
                                        self._stop_order_id,
                                    )
                                except Exception as exc2:
                                    logger.error(
                                        "%s: FAILED to place reconciled stop -- "
                                        "position unprotected: %s",
                                        self.name,
                                        exc2,
                                    )
                            else:
                                logger.error(
                                    "%s: reconciled position %s x%d is UNPROTECTED "
                                    "-- no close history and avg_cost=0.",
                                    self.name,
                                    self.symbol,
                                    self._position_shares,
                                )
                        break
            except Exception as exc:
                logger.warning("%s: position reconcile failed -- %s", self.name, exc)

        # Step 3 (H3): register order-error callback to clear _entry_pending.
        if self.om is not None:
            try:
                self.om.on_error(self._on_order_error)
            except AttributeError:
                pass  # MockOrderManager may not expose on_error

        logger.info(
            "%s starting | symbol=%s sma_fast=%d sma_slow=%d",
            self.name,
            self.symbol,
            self._sma_fast,
            self._sma_slow,
        )

    def on_stop(self) -> None:
        # NEW-1: do NOT cancel the broker STOP on shutdown.
        # A GTC stop's entire purpose is to protect the position while the bot
        # is offline.  Cancelling it here would leave the position naked against
        # overnight/weekend gap-downs -- exactly the scenario C2 was meant to fix.
        # The stop is cancelled only by _exit() (before a SELL) or on_fill SELL.
        if self._stop_order_id is not None:
            logger.info(
                "%s: shutdown with active broker STOP order %d @ %.2f -- "
                "stop remains live to protect position during downtime.",
                self.name,
                self._stop_order_id,
                self._stop_price,
            )
        logger.info("%s stopped | symbol=%s", self.name, self.symbol)

    # ------------------------------------------------------------------
    # Fill tracking -- auto-wired by BaseStrategy.__init__
    # ------------------------------------------------------------------

    def on_fill(self, result: OrderResult) -> None:
        if not result.is_filled:
            return
        if result.symbol != self.symbol:  # H1 -- ignore fills for other symbols
            return

        self._entry_pending = False  # H3 -- clear regardless of direction

        if result.action == OrderAction.BUY.value:
            self._position_shares = int(result.filled)
            self._in_position = True
            logger.info(
                "%s BUY filled | %s x%d @ %.2f | stop=%.2f",
                self.name,
                result.symbol,
                self._position_shares,
                result.avg_fill_price or 0.0,
                self._stop_price,
            )

            # C2 -- place broker-side protective STOP immediately (live only).
            # Protects against intraday gaps and bot downtime between daily ticks.
            # Skipped in backtest (client=None) -- stop enforced in on_tick instead.
            if self.client is not None and self._stop_price > 0:
                stop_req = OrderRequest(
                    symbol=self.symbol,
                    action=OrderAction.SELL,
                    quantity=self._position_shares,
                    order_type=OrderType.STOP,
                    stop_price=self._stop_price,
                    tif=TimeInForce.GTC,
                )
                try:
                    stop_result = self.om.place_order(stop_req, allow_duplicate=True)
                    self._stop_order_id = stop_result.order_id
                    logger.info(
                        "%s protective STOP placed | %s @ %.2f (order %d)",
                        self.name,
                        self.symbol,
                        self._stop_price,
                        self._stop_order_id,
                    )
                except Exception as exc:
                    logger.error(
                        "%s: FAILED to place protective stop -- position is unprotected: %s",
                        self.name,
                        exc,
                    )

        elif result.action == OrderAction.SELL.value:
            # Cancel the outstanding protective stop (C2), if any
            if self._stop_order_id is not None:
                try:
                    self.om.cancel_order(self._stop_order_id)
                except Exception:
                    pass
                self._stop_order_id = None

            self._in_position = False
            self._position_shares = 0
            self._stop_price = 0.0
            logger.info(
                "%s SELL filled | %s @ %.2f",
                self.name,
                result.symbol,
                result.avg_fill_price or 0.0,
            )

    def _on_order_error(self, req_id: int, code: int, msg: str) -> None:
        """Clear pending entry state if the broker rejects our order (H3)."""
        if self._entry_pending:
            logger.warning(
                "%s: order error (req=%d code=%d msg=%s) -- clearing _entry_pending.",
                self.name,
                req_id,
                code,
                msg,
            )
            self._entry_pending = False

    # ------------------------------------------------------------------
    # Main tick
    # ------------------------------------------------------------------

    def on_tick(self) -> None:
        if self.reconnect and not self.reconnect.wait_for_connection(timeout=60):
            return
        if self.risk_manager and self.risk_manager.is_halted():
            return

        # Write UTC timestamp so the health check timer can detect a missed tick.
        try:
            health_path = os.path.join(
                os.path.dirname(os.path.dirname(__file__)), "data", "health.txt"
            )
            os.makedirs(os.path.dirname(health_path), exist_ok=True)
            with open(health_path, "w") as _hf:
                _hf.write(datetime.now(timezone.utc).isoformat())
        except Exception as _he:
            logger.warning("on_tick: could not write health.txt: %s", _he)

        # C1 -- get a real daily close.
        # Live:    re-fetch the last _HISTORY_DAYS of daily bars from yfinance
        #          so SMAs are computed on actual daily closes, not 5-sec samples.
        # Backtest: BacktestDataFeed already serves the correct daily bar.
        # Q4/Q6a: re-arm broker STOP if position is held but stop tracking was lost
        # (covers avg_cost==0 path on reconcile and rejected-SELL aftermath).
        if (
            self.client is not None
            and self._in_position
            and self._stop_order_id is None
            and self._stop_price > 0
        ):
            stop_req = OrderRequest(
                symbol=self.symbol,
                action=OrderAction.SELL,
                quantity=self._position_shares,
                order_type=OrderType.STOP,
                stop_price=self._stop_price,
                tif=TimeInForce.GTC,
            )
            try:
                sr = self.om.place_order(stop_req, allow_duplicate=True)
                self._stop_order_id = sr.order_id
                logger.warning(
                    "%s: re-armed broker STOP for unprotected position "
                    "%s x%d @ %.2f (order %d).",
                    self.name,
                    self.symbol,
                    self._position_shares,
                    self._stop_price,
                    self._stop_order_id,
                )
            except Exception as exc:
                logger.error(
                    "%s: re-arm STOP failed -- position remains unprotected: %s",
                    self.name,
                    exc,
                )

        if self.client is not None:
            if not self._refresh_closes():
                return  # data fetch failed -- skip tick
            latest_close = self._closes[-1]
        else:
            bar = self.feed.get_latest(self.symbol)
            if bar is None:
                return
            self._closes.append(bar.close)
            # L1 -- trim to prevent unbounded growth
            max_len = self._sma_slow + 10
            if len(self._closes) > max_len:
                self._closes = self._closes[-max_len:]
            latest_close = bar.close

        if len(self._closes) < self._sma_slow + 1:
            return  # still in warmup period

        fast_now = self._sma(self._sma_fast)
        slow_now = self._sma(self._sma_slow)
        fast_prev = self._sma_prev(self._sma_fast)
        slow_prev = self._sma_prev(self._sma_slow)

        cross_up = fast_prev <= slow_prev and fast_now > slow_now
        cross_down = fast_prev >= slow_prev and fast_now < slow_now

        # H3 -- guard: don't enter again while a BUY order is still pending
        if not self._in_position and not self._entry_pending:
            if cross_up:
                self._enter(latest_close)
        elif self._in_position:
            stop_hit = self._stop_price > 0 and latest_close <= self._stop_price
            if stop_hit or cross_down:
                self._exit(latest_close, reason="stop" if stop_hit else "cross-down")

    # ------------------------------------------------------------------
    # Entry / exit helpers
    # ------------------------------------------------------------------

    def _enter(self, price: float) -> None:
        entry = price
        stop = self._calc_stop(entry)

        # M1 -- validate stop is strictly below entry before proceeding
        if stop >= entry:
            logger.warning(
                "%s: stop %.2f >= entry %.2f (degenerate calc) -- skipping signal.",
                self.name,
                stop,
                entry,
            )
            return

        target = entry + 3.0 * (entry - stop)  # see H4 note in module docstring

        equity = self._get_equity()
        if equity is None or equity <= 0:  # H2 -- fail closed on bad equity
            return

        shares = self._calc_shares(entry, stop, target, equity)
        if shares < 1:
            logger.debug("%s: position size < 1 share -- skipping entry.", self.name)
            return

        request = OrderRequest(
            symbol=self.symbol,
            action=OrderAction.BUY,
            quantity=shares,
            tif=TimeInForce.GTC,
        )
        try:
            self.safe_place_order(request, current_price=entry)
            self._entry_pending = True  # H3 -- confirmed by on_fill, NOT here
            self._stop_price = stop  # stash so on_fill can arm the broker stop
            logger.info(
                "%s BUY queued | %s x%d entry=%.2f stop=%.2f target=%.2f",
                self.name,
                self.symbol,
                shares,
                entry,
                stop,
                target,
            )
        except Exception as exc:
            logger.warning("%s: entry order rejected -- %s", self.name, exc)

    def _exit(self, price: float, reason: str) -> None:
        # H5 -- refuse to sell if position size is not yet confirmed by on_fill
        if self._position_shares <= 0:
            logger.warning(
                "%s: exit signal (%s) but _position_shares=0 "
                "-- BUY fill not yet confirmed, deferring one tick.",
                self.name,
                reason,
            )
            return

        # NEW-2: cancel the broker STOP *before* placing the SELL to eliminate
        # the double-fill race.  If both executed, we would flip short by
        # _position_shares.  Cancel failure is logged but does not block the
        # SELL -- the on_fill SELL branch also attempts a cancel as a backstop.
        if self._stop_order_id is not None:
            try:
                self.om.cancel_order(self._stop_order_id)
            except Exception as exc:
                logger.warning(
                    "%s: could not cancel stop order %d before exit (%s) -- "
                    "proceeding with SELL; double-fill possible if stop already triggered.",
                    self.name,
                    self._stop_order_id,
                    exc,
                )
            # Fix #3: do NOT null out _stop_order_id here; do it only after the
            # SELL is successfully placed.  If safe_place_order() raises (e.g.
            # RiskManager blocks the SELL), we must keep the ID so the caller
            # can tell that the stop has already been cancelled and the position
            # is now unprotected -- otherwise state silently desyncs.

        request = OrderRequest(
            symbol=self.symbol,
            action=OrderAction.SELL,
            quantity=self._position_shares,
            tif=TimeInForce.GTC,
        )
        try:
            self.safe_place_order(request, current_price=price)
            self._stop_order_id = None  # Fix #3: clear only after SELL is queued
            logger.info(
                "%s SELL queued | %s x%d @ %.2f reason=%s",
                self.name,
                self.symbol,
                self._position_shares,
                price,
                reason,
            )
        except Exception as exc:
            logger.error(
                "%s: exit order rejected -- %s. "
                "Broker STOP was already cancelled; position is unprotected.",
                self.name,
                exc,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _refresh_closes(self) -> bool:
        """
        Fetch the last _HISTORY_DAYS of daily closes from yfinance and store in
        self._closes. Returns True on success, False on failure (caller skips tick).

        Called from on_start() to seed history and from on_tick() each live tick.
        Never called in backtest (client is None in that context).
        """
        from data.historical import HistoricalDataLoader

        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=_HISTORY_DAYS)).strftime("%Y-%m-%d")
        try:
            df = HistoricalDataLoader.load_yfinance(self.symbol, start, end)
            self._closes = [float(c) for c in df["close"].tail(self._sma_slow + 10).tolist()]
            return True
        except Exception as exc:
            logger.warning(
                "%s: daily bar fetch failed -- skipping tick: %s",
                self.name,
                exc,
            )
            return False

    def _sma(self, n: int) -> float:
        return sum(self._closes[-n:]) / n

    def _sma_prev(self, n: int) -> float:
        return sum(self._closes[-n - 1 : -1]) / n

    def _calc_stop(self, entry: float) -> float:
        """Swing-low stop: lowest close of the 5 bars before entry minus 0.5%."""
        lookback = self._closes[-6:-1]  # 5 bars prior to current
        if not lookback:
            return entry * 0.97
        swing_low = min(lookback)
        stop = swing_low * 0.995
        # Fallback: if stop is too tight (< 1% distance) use 3%
        if (entry - stop) / entry < 0.01:
            stop = entry * 0.97
        return stop

    def _get_equity(self) -> Optional[float]:
        """
        Return current account equity.
        Live:    fetches NetLiquidation from broker -- fresh every tick per policy.
        Backtest: returns initial_capital (static proxy).
        Returns None on broker failure -- caller must treat None as no-trade. (H2)
        """
        if self.client is None:
            return self._initial_capital
        try:
            summary = {s.tag: s.value for s in self.client.get_account_summary()}
            return float(summary["NetLiquidation"])
        except Exception as exc:
            logger.warning(
                "%s: equity fetch failed -- skipping entry: %s",
                self.name,
                exc,
            )
            return None

    def _calc_shares(
        self,
        entry: float,
        stop: float,
        target: float,
        equity: float,
    ) -> int:
        """
        Size the trade.

        Live:    rm.plan_trade() enforces 2% max-risk rule and returns share count.
        Backtest (risk_manager=None): PositionSizer.risk_based() at 2%.

        Hard cap at 95% of equity / entry price prevents over-sizing when
        the stop distance is very tight (e.g. congestion zone).
        """
        max_by_cash = max(1, int(equity * 0.95 / entry))

        if self.risk_manager is not None:
            try:
                shares = self.risk_manager.plan_trade(
                    entry_price=entry,
                    stop_price=stop,
                    take_profit_price=target,
                    equity=equity,
                )
                return min(shares, max_by_cash)
            except Exception as exc:
                logger.warning("%s: plan_trade rejected -- %s", self.name, exc)
                return 0
        else:
            from risk.position_sizer import PositionSizer

            shares = int(PositionSizer.risk_based(equity, entry, stop, risk_pct=0.02))
            return min(shares, max_by_cash)

    # ------------------------------------------------------------------
    # Strategy metadata
    # ------------------------------------------------------------------

    @property
    def params(self) -> dict:
        p: dict = {
            "symbol": self.symbol,
            "sma_fast": self._sma_fast,
            "sma_slow": self._sma_slow,
        }
        if self.client is None:
            # Backtest only — live equity comes from the broker, not this field.
            p["initial_capital_backtest"] = self._initial_capital
        return p
