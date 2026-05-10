"""
RSI2-MR: SPY Regime-Filtered 2-Day Mean Reversion

Spec: SPY_RSI2_MR_Strategy_Spec_v2.md  (companion to DEVELOPER_INSTRUCTIONS.md)
Mode: B (placeholder 3R target; real exit is RSI(2)≥70 or 8-bar time stop)
Symbol: SPY (long only)
Timeframe: Daily — fires via DailyAt(16, 10) in REGISTRY

Entry: SPY close > SMA(200)  AND  RSI(2) ≤ rsi_oversold  AND  VIX ≤ vix_upper
       AND no existing position  AND cooldown elapsed  AND not FOMC eve  AND not Russell window
Stop:  entry_fill - atr_multiplier * ATR(14)  [GTC STP placed immediately after fill]
Target: entry_fill + 3 * stop_distance       [GTC LMT placeholder; seldom hits]
Exits: (priority order per spec §1.5)
  1. 8-bar time stop
  2. RSI(2) ≥ rsi_overbought
  3. Resting STP hit (intraday — handled by broker / MockOrderManager bracket sim)
  4. Resting LMT hit (intraday — rare)

Fixed safety constants (NOT optimized per spec):
  ATR_PERIOD=14, TIME_STOP_BARS=8, COOLDOWN_BARS=3

Tunable parameters (6, at spec ceiling):
  sma_period, rsi_period, rsi_oversold, rsi_overbought, vix_upper, atr_multiplier
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

from config.calendars.fomc import is_fomc_day
from config.calendars.market_calendar import (
    is_pre_long_holiday_closure,
    is_russell_rebalance_window,
    next_trading_day,
)
from data.vix_feed import VIXFeed
from models.order import (
    OrderAction,
    OrderRequest,
    OrderResult,
    OrderType,
    TimeInForce,
)
from strategies._indicators import atr_wilder, rsi_wilder, sma
from strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

# History depth for live mode yfinance fetch (trading days ≈ 400 calendar days)
_LIVE_HISTORY_DAYS = 400
# Warmup period before first eligible trade (per spec §1.12)
_WARMUP_BARS = 240
# Persistent state file path (relative to project root)
_DEFAULT_STATE_FILE = Path(__file__).parent.parent / "data" / "rsi2_mr_state.json"
# State schema version — bumped when on-disk fields gain new semantics.
# v2 (MS-B): strategy_peak_equity is now sourced from
# `_get_strategy_attributed_equity()` (initial_capital + own realized P&L +
# unrealized) instead of account-wide NetLiquidation. v1 files persisted the
# contaminated NetLiq value, so a one-shot reset of `strategy_peak_equity`
# and `circuit_breaker_until` is performed when v1 (or missing) state loads.
_STATE_SCHEMA_VERSION: int = 2


class RSI2MR_SPY(BaseStrategy):
    """
    SPY Regime-Filtered RSI(2) Mean-Reversion strategy.

    Args:
        sma_period:       Regime-gate SMA period. Default 200.
        rsi_period:       RSI look-back. Default 2.
        rsi_oversold:     Entry threshold (RSI ≤ this). Default 10.
        rsi_overbought:   Exit threshold (RSI ≥ this). Default 70.
        vix_upper:        VIX panic filter (VIX ≤ this to enter). Default 35.
        atr_multiplier:   Stop distance = atr_multiplier × ATR(14). Default 1.5.
        initial_capital:  Backtest equity proxy (live fetches from broker). Default 50000.
        vix_feed:         VIXFeed instance. If None, a live feed is created on start.
    """

    # ── Fixed safety constants (per spec — do NOT optimize) ───────────────────
    _ATR_PERIOD: int = 14
    _TIME_STOP_BARS: int = 8
    _COOLDOWN_BARS: int = 3

    # ── Circuit-breaker thresholds ────────────────────────────────────────────
    _CB_MAX_LOSSES: int = 5
    _CB_DRAWDOWN_PCT: float = 0.08  # 8% from strategy peak

    def __init__(
        self,
        client,
        order_manager,
        risk_manager=None,
        reconnect=None,
        feed=None,
        symbol: str = "SPY",
        sma_period: int = 200,
        rsi_period: int = 2,
        rsi_oversold: float = 10.0,
        rsi_overbought: float = 70.0,
        vix_upper: float = 35.0,
        atr_multiplier: float = 1.5,
        initial_capital: float = 50_000.0,
        vix_feed: Optional[VIXFeed] = None,
        state_file_path: Optional[Path] = None,
    ) -> None:
        super().__init__(
            client=client,
            order_manager=order_manager,
            risk_manager=risk_manager,
            reconnect=reconnect,
            feed=feed,
            symbol=symbol,
        )

        # Tunable parameters
        self._sma_period = sma_period
        self._rsi_period = rsi_period
        self._rsi_oversold = rsi_oversold
        self._rsi_overbought = rsi_overbought
        self._vix_upper = vix_upper
        self._atr_multiplier = atr_multiplier
        self._initial_capital = initial_capital

        # VIX feed (injected in backtest; created live in on_start)
        self._vix_feed: Optional[VIXFeed] = vix_feed

        # State file path (injectable for test isolation; defaults to module path)
        self._state_file_path: Path = state_file_path or _DEFAULT_STATE_FILE
        # Persist to disk only in live mode OR when an explicit path was injected
        # (for tests). Backtests with the default path skip persistence so they
        # cannot pollute the production VPS state file.
        self._persist_state: bool = (client is not None) or (state_file_path is not None)

        # Price history buffers (oldest-first)
        self._closes: List[float] = []
        self._highs: List[float] = []
        self._lows: List[float] = []
        self._bar_dates: List[date] = []  # for calendar filters (live+backtest)

        # Bar counter (backtest tick index; live uses date)
        self._bar_index: int = 0

        # Position state
        self._in_position: bool = False
        self._entry_price: float = 0.0
        self._stop_price: float = 0.0
        self._target_price: float = 0.0
        self._position_shares: int = 0
        self._stop_order_id: Optional[int] = None
        self._target_order_id: Optional[int] = None
        self._bars_held: int = 0  # bars elapsed since entry fill

        # Cooldown tracker
        self._cooldown_remaining: int = 0  # bars before next entry eligible

        # Circuit breaker
        self._consecutive_losses: int = 0
        self._strategy_peak_equity: float = initial_capital
        self._circuit_breaker_until: Optional[date] = None

        # MS-K guard: set True on partial-SELL detection. Independent from the
        # circuit breaker because the existing CB only blocks new entries —
        # this flag also blocks `_check_exits` so a dangling broker position
        # cannot trigger a fresh SELL for stale `_position_shares` (which
        # would naked-short whatever didn't fill on the original SELL).
        # Operator must clear via manual reconcile.
        self._partial_fill_halt: bool = False

        # Pending entry guard (between signal and fill)
        self._entry_pending: bool = False

        # VIX at signal time (stored for slippage computation and logging)
        self._vix_at_signal: Optional[float] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_start(self) -> None:
        if self._vix_feed is None:
            self._vix_feed = VIXFeed()

        # Restore circuit-breaker state (survives daily restarts)
        self._load_state()
        # MS-A1: track whether state file claimed we held a position so we can
        # detect a stale state-vs-broker mismatch after the reconcile pass below.
        _state_claimed_position: bool = self._entry_price > 0

        # Seed history for live mode
        if self.client is not None:
            self._refresh_history()
            # Reconcile existing position from broker
            self._reconcile_position()
            # MS-A1: state said in_position but broker disagrees → clear stale entry.
            if _state_claimed_position and not self._in_position:
                logger.warning(
                    "%s: state file claimed in_position with entry_price=%.2f but "
                    "broker shows no position — clearing stale state.",
                    self.name,
                    self._entry_price,
                )
                self._entry_price = 0.0
                self._save_state()

        logger.info(
            "%s starting | symbol=%s sma=%d rsi=%d oversold=%.0f overbought=%.0f "
            "vix_upper=%.0f atr_mult=%.1f",
            self.name,
            self.symbol,
            self._sma_period,
            self._rsi_period,
            self._rsi_oversold,
            self._rsi_overbought,
            self._vix_upper,
            self._atr_multiplier,
        )

    def on_stop(self) -> None:
        self._save_state()
        logger.info("%s stopped | symbol=%s", self.name, self.symbol)

    # ── Fill tracking ─────────────────────────────────────────────────────────

    def on_fill(self, result: OrderResult) -> None:
        if not result.is_filled:
            return
        if result.symbol != self.symbol:
            return

        self._entry_pending = False

        if result.action == OrderAction.BUY.value:
            # No pyramiding: this strategy assumes a single round-trip at a time.
            # If we ever add a second BUY before the first SELL, _entry_price would
            # be overwritten and cost_basis attribution would silently lose the prior leg.
            assert not self._in_position, (
                f"{self.name}: BUY fill received while _in_position=True — "
                "pyramiding is not supported."
            )
            self._in_position = True
            self._position_shares = int(result.filled)
            if result.avg_fill_price is not None:
                self._entry_price = result.avg_fill_price
            self._bars_held = 0
            # Persist entry price so a restart between BUY and SELL still attributes
            # cost_basis correctly on the eventual SELL fill.
            self._save_state()
            logger.info(
                "%s BUY filled | %s x%d @ %.2f | stop=%.2f target=%.2f",
                self.name,
                result.symbol,
                self._position_shares,
                self._entry_price,
                self._stop_price,
                self._target_price,
            )
            # Place resting STP + LMT GTC immediately after fill
            self._place_bracket_orders()

        elif result.action == OrderAction.SELL.value:
            # MS-K guard: detect partial SELL (FILLED status arrived but the
            # filled qty fell short of our position — IB cancelled the
            # remainder of a partial in low liquidity, or a bracket leg
            # rejected). Float-tolerant compare so 99.999 shares vs 100 isn't
            # treated as partial. On detection: trip a dedicated halt flag,
            # also trip the CB, fire ntfy, and EARLY-RETURN — do NOT zero
            # state, do NOT stamp cost_basis, do NOT cancel brackets. The
            # operator owns reconcile.
            if self._position_shares > 0 and (result.filled + 0.5) < self._position_shares:
                logger.error(
                    "%s: PARTIAL SELL DETECTED order_id=%s filled=%.4f of "
                    "position=%d. Halting strategy (entries + exits) until "
                    "manual reconcile clears _partial_fill_halt.",
                    self.name,
                    result.order_id,
                    result.filled,
                    self._position_shares,
                )
                self._partial_fill_halt = True
                # Defense-in-depth: trip CB so even if the halt flag is
                # cleared without restoring state, new entries are blocked
                # until the 1st of next month. Same idiom as
                # _update_circuit_breaker.
                today = date.today()
                next_month = today.replace(day=1) + timedelta(days=32)
                self._circuit_breaker_until = next_month.replace(day=1)
                self._fire_circuit_breaker_alert(
                    f"partial SELL fill {result.filled:.4f}/{self._position_shares}"
                )
                self._save_state()
                return

            fill_price = result.avg_fill_price or 0.0

            # MS-A1: stamp cost_basis on the SELL OrderResult so TradeLog.record()
            # can compute realized_pnl. Must happen BEFORE clearing _entry_price
            # below. Per runtime/strategy_runner.py callback contract, this hook
            # runs before the trade_log hook reads the result.
            if self._entry_price > 0:
                result.cost_basis = self._entry_price

            # Compute real_r_multiple and attach to result for TradeLog
            if self._entry_price > 0 and self._stop_price > 0:
                risk = self._entry_price - self._stop_price
                r_mult = (fill_price - self._entry_price) / risk if risk != 0 else 0.0
                result.real_r_multiple = r_mult
                # Update circuit-breaker counters
                self._update_circuit_breaker(r_mult)

            # Cancel any surviving bracket orders
            self._cancel_bracket_orders()

            self._in_position = False
            self._position_shares = 0
            self._entry_price = 0.0
            self._stop_price = 0.0
            self._target_price = 0.0
            self._cooldown_remaining = self._COOLDOWN_BARS

            self._save_state()

            logger.info(
                "%s SELL filled | %s @ %.2f | r=%.2f",
                self.name,
                result.symbol,
                fill_price,
                result.real_r_multiple if result.real_r_multiple is not None else float("nan"),
            )

    # ── Main tick ─────────────────────────────────────────────────────────────

    def on_tick(self) -> None:
        if self.reconnect and not self.reconnect.wait_for_connection(timeout=60):
            return
        if self.risk_manager and self.risk_manager.is_halted():
            return
        # MS-K guard: a prior partial SELL left dangling broker shares; both
        # entries AND exits are suspended until manual reconcile clears the
        # flag. Bracket orders (if any) remain live broker-side.
        if self._partial_fill_halt:
            return

        # ── Advance bar data ──────────────────────────────────────────────────
        if self.client is not None:
            # Live mode: refresh full history each tick (yfinance + VIX)
            if not self._refresh_history():
                return
        else:
            # Backtest mode: consume bar from feed
            bar = self.feed.get_latest(self.symbol)
            if bar is None:
                return
            self._closes.append(bar.close)
            self._highs.append(bar.high)
            self._lows.append(bar.low)
            self._bar_dates.append(
                bar.timestamp.date() if hasattr(bar.timestamp, "date") else bar.timestamp
            )
            # Trim buffers to prevent unbounded growth
            max_len = self._sma_period + 20
            if len(self._closes) > max_len:
                trim = len(self._closes) - max_len
                self._closes = self._closes[trim:]
                self._highs = self._highs[trim:]
                self._lows = self._lows[trim:]
                self._bar_dates = self._bar_dates[trim:]

        self._bar_index += 1

        # Warmup gate — use _bar_index (buffer is trimmed and cannot reach _WARMUP_BARS)
        if self._bar_index < _WARMUP_BARS:
            return
        if len(self._closes) < self._sma_period + self._ATR_PERIOD + 2:
            return

        # Today's date for calendar filters
        today = self._bar_dates[-1] if self._bar_dates else date.today()

        # ── Update circuit-breaker equity check ───────────────────────────────
        # Account-wide equity (drives position sizing + non-None gate below).
        current_equity = self._get_equity()
        # MS-B: strategy-attributed equity drives the circuit-breaker ratchet
        # so another strategy's gains cannot inflate this strategy's peak.
        strategy_equity = self._get_strategy_attributed_equity()
        if strategy_equity is not None and strategy_equity > self._strategy_peak_equity:
            self._strategy_peak_equity = strategy_equity
            self._save_state()

        # ── If in position: check exits FIRST (spec §1.5) ─────────────────────
        if self._in_position and not self._entry_pending:
            self._bars_held += 1  # increment first so _check_exits sees the true count
            self._check_exits(today)
            return  # exit-first rule: no new entry on same tick

        # ── Cooldown ──────────────────────────────────────────────────────────
        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1
            return

        # ── Circuit-breaker gate ──────────────────────────────────────────────
        if self._circuit_breaker_until is not None and today < self._circuit_breaker_until:
            return

        # ── Entry pending guard ───────────────────────────────────────────────
        if self._entry_pending:
            return

        # ── Indicator computation ─────────────────────────────────────────────
        try:
            sma200 = sma(self._closes, self._sma_period)
            rsi2 = rsi_wilder(self._closes, self._rsi_period)
            atr14 = atr_wilder(self._highs, self._lows, self._closes, self._ATR_PERIOD)
        except ValueError:
            return  # not enough bars yet

        current_close = self._closes[-1]

        # ── Regime gate ───────────────────────────────────────────────────────
        if current_close <= sma200:
            return  # bearish regime — no new entries

        # ── VIX filter ────────────────────────────────────────────────────────
        vix = self._get_vix(today)
        if vix is None:
            return  # fail-safe: block entry when VIX unavailable
        if vix > self._vix_upper:
            return  # panic regime

        # ── Calendar filters ──────────────────────────────────────────────────
        try:
            tomorrow = next_trading_day(today)
        except RuntimeError:
            tomorrow = today + timedelta(days=1)

        if is_fomc_day(tomorrow):
            return  # skip entry eve of Fed announcement
        if is_russell_rebalance_window(today) or is_russell_rebalance_window(tomorrow):
            return  # skip Russell rebalance window

        # ── RSI oversold entry signal ─────────────────────────────────────────
        if rsi2 > self._rsi_oversold:
            return  # not oversold

        # ── Daily loss-cap deference (spec §special filters) ─────────────────
        if current_equity is not None:
            daily_loss_threshold = -1_500.0  # 75% of $2k bot-halt level
            if (
                self.risk_manager is not None
                and hasattr(self.risk_manager, "_daily_pnl")
                and self.risk_manager._daily_pnl <= daily_loss_threshold
            ):
                return

        # ── Position sizing ───────────────────────────────────────────────────
        equity = current_equity or self._initial_capital
        stop = current_close - self._atr_multiplier * atr14
        if stop >= current_close:
            logger.debug(
                "%s: degenerate stop %.2f >= close %.2f — skipping", self.name, stop, current_close
            )
            return

        target = current_close + 3.0 * (current_close - stop)  # placeholder 3R
        shares = self._calc_shares(current_close, stop, target, equity)
        if shares < 1:
            return

        # ── Store signal context for on_fill ─────────────────────────────────
        self._stop_price = stop
        self._target_price = target
        self._vix_at_signal = vix

        # ── Place entry order (MKT → fills at next bar open) ─────────────────
        slippage_bps = 2.0 if vix > 25.0 else 1.0  # stored for MockOrderManager
        request = OrderRequest(
            symbol=self.symbol,
            action=OrderAction.BUY,
            quantity=shares,
            tif=TimeInForce.GTC,
            backtest_slippage_bps=slippage_bps,  # type: ignore[call-arg]
        )
        try:
            self.safe_place_order(request, current_price=current_close)
            self._entry_pending = True
            logger.info(
                "%s BUY queued | %s x%d entry≈%.2f stop=%.2f target=%.2f "
                "rsi=%.1f vix=%.1f sma200=%.2f",
                self.name,
                self.symbol,
                shares,
                current_close,
                stop,
                target,
                rsi2,
                vix,
                sma200,
            )
        except Exception as exc:
            logger.warning("%s: entry rejected — %s", self.name, exc)
            # Do NOT zero stop/target here: in live mode the broker may have accepted
            # the order before the local exception fired.  If on_fill(BUY) arrives,
            # _place_bracket_orders() needs stop/target to place the protective stop.
            # Reconciliation on next tick will clear stale state if no fill arrives.

    # ── Exit logic ────────────────────────────────────────────────────────────

    def _check_exits(self, today: date) -> None:
        """Check exit conditions in priority order (spec §1.5)."""
        # Priority 1: time stop (8 bars)
        if self._bars_held >= self._TIME_STOP_BARS:
            self._exit(reason="time-stop")
            return

        # Priority 2: RSI(2) ≥ overbought
        try:
            rsi2 = rsi_wilder(self._closes, self._rsi_period)
        except ValueError:
            return
        if rsi2 >= self._rsi_overbought:
            self._exit(reason="rsi-exit")
            return

        # Priority 3 & 4: handled by resting STP/LMT in MockOrderManager
        # (broker in live mode). on_fill handles state reset.

        # Forced flat: pre-long-holiday closure
        if is_pre_long_holiday_closure(today):
            self._exit(reason="forced-flat-holiday")

    def _exit(self, reason: str) -> None:
        if self._position_shares <= 0:
            logger.warning("%s: exit(%s) but position_shares=0 — skipping.", self.name, reason)
            return

        # Cancel bracket orders BEFORE placing SELL
        self._cancel_bracket_orders()

        vix = self._vix_at_signal  # use entry VIX for slippage attribution
        slippage_bps = 2.0 if (vix is not None and vix > 25.0) else 1.0

        request = OrderRequest(
            symbol=self.symbol,
            action=OrderAction.SELL,
            quantity=self._position_shares,
            tif=TimeInForce.GTC,
            backtest_slippage_bps=slippage_bps,  # type: ignore[call-arg]
        )
        try:
            # Use last close as risk-check price; fall back to entry price on cold
            # restart (closes not yet loaded) so we never pass 0.0 to the risk check.
            _exit_ref = self._closes[-1] if self._closes else self._entry_price or 1.0
            self.safe_place_order(request, current_price=_exit_ref)
            logger.info(
                "%s SELL queued | %s x%d reason=%s bars_held=%d",
                self.name,
                self.symbol,
                self._position_shares,
                reason,
                self._bars_held,
            )
        except Exception as exc:
            logger.error(
                "%s: exit rejected (%s) — %s. Bracket already cancelled; position unprotected.",
                self.name,
                reason,
                exc,
            )

    # ── Bracket orders ────────────────────────────────────────────────────────

    def _place_bracket_orders(self) -> None:
        """Place resting STP and placeholder LMT GTC after BUY fill."""
        if self._stop_price <= 0 or self._target_price <= 0:
            return
        if self._position_shares <= 0:
            return

        vix = self._vix_at_signal
        slippage_bps = (2.0 if (vix is not None and vix > 25.0) else 1.0) + 1.0  # +1bp for STP

        # Protective stop
        try:
            stop_req = OrderRequest(
                symbol=self.symbol,
                action=OrderAction.SELL,
                quantity=self._position_shares,
                order_type=OrderType.STOP,
                stop_price=self._stop_price,
                tif=TimeInForce.GTC,
                backtest_slippage_bps=slippage_bps,  # type: ignore[call-arg]
            )
            sr = self.om.place_order(stop_req, allow_duplicate=True)
            self._stop_order_id = sr.order_id
        except Exception as exc:
            logger.error("%s: failed to place protective STP — %s", self.name, exc)

        # Placeholder LMT target (3R)
        try:
            lmt_req = OrderRequest(
                symbol=self.symbol,
                action=OrderAction.SELL,
                quantity=self._position_shares,
                order_type=OrderType.LIMIT,
                limit_price=self._target_price,
                tif=TimeInForce.GTC,
                backtest_slippage_bps=0.0,  # type: ignore[call-arg]
            )
            lr = self.om.place_order(lmt_req, allow_duplicate=True)
            self._target_order_id = lr.order_id
        except Exception as exc:
            logger.warning("%s: failed to place placeholder LMT — %s", self.name, exc)

    def _cancel_bracket_orders(self) -> None:
        for oid in (self._stop_order_id, self._target_order_id):
            if oid is not None:
                try:
                    self.om.cancel_order(oid)
                except Exception:
                    pass
        self._stop_order_id = None
        self._target_order_id = None

    # ── Circuit breaker ───────────────────────────────────────────────────────

    def _update_circuit_breaker(self, r_multiple: float) -> None:
        if r_multiple < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        # MS-B: drawdown is measured against strategy-attributed equity so
        # another strategy's losses cannot trip RSI2MR's circuit breaker.
        current_equity = self._get_strategy_attributed_equity()
        cb_fired = False
        reason = ""
        if self._consecutive_losses >= self._CB_MAX_LOSSES:
            cb_fired = True
            reason = f"{self._consecutive_losses} consecutive losses"
        elif (
            current_equity is not None
            and self._strategy_peak_equity > 0
            and (current_equity / self._strategy_peak_equity - 1) <= -self._CB_DRAWDOWN_PCT
        ):
            cb_fired = True
            pct = (current_equity / self._strategy_peak_equity - 1) * 100
            reason = f"{pct:.1f}% drawdown from strategy peak"

        if cb_fired:
            today = date.today()
            # Halt until 1st of next month
            next_month = today.replace(day=1) + timedelta(days=32)
            self._circuit_breaker_until = next_month.replace(day=1)
            self._consecutive_losses = 0  # reset after trip
            logger.warning(
                "%s: CIRCUIT BREAKER fired (%s) — halting entries until %s",
                self.name,
                reason,
                self._circuit_breaker_until,
            )
            self._fire_circuit_breaker_alert(reason)
            self._save_state()

    def _fire_circuit_breaker_alert(self, reason: str) -> None:
        try:
            topic = os.environ.get("NTFY_TOPIC", "")
            if not topic:
                return
            import urllib.request

            msg = f"RSI2MR circuit breaker: {reason} — halted until {self._circuit_breaker_until}"
            req = urllib.request.Request(
                f"https://ntfy.sh/{topic}",
                data=msg.encode(),
                headers={"Title": "TradeBot circuit breaker", "Priority": "urgent"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception as exc:
            logger.debug("%s: ntfy alert failed (non-critical): %s", self.name, exc)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_equity(self) -> Optional[float]:
        """Live: fetch from broker. Backtest: query MockOrderManager portfolio."""
        if self.client is not None:
            try:
                summary = {s.tag: s.value for s in self.client.get_account_summary()}
                return float(summary["NetLiquidation"])
            except Exception as exc:
                logger.warning("%s: equity fetch failed: %s", self.name, exc)
                return None
        # Backtest path — MockOrderManager exposes current_equity()
        if self.om is not None and hasattr(self.om, "current_equity"):
            return self.om.current_equity()
        return self._initial_capital

    def _get_strategy_attributed_equity(self) -> Optional[float]:
        """
        MS-B: equity attributed to THIS strategy only.

        Live: ``initial_capital + sum(realized P&L for this strategy) +
        unrealized P&L on the currently open position``. Used by the
        circuit-breaker peak-equity ratchet and 8% drawdown trip so another
        strategy's losses cannot fire RSI2MR's circuit breaker.

        Backtest: falls back to ``_get_equity()`` (single-strategy backtest →
        account equity == strategy equity, so no contamination).

        Returns ``None`` if live equity cannot be computed (e.g. no TradeLog
        wired yet AND we need a fresh signal of broker-side liquidity loss).
        """
        # Backtest path: single-strategy today, no contamination — reuse
        # account equity. TODO(multi-strategy-backtest): if BacktestEngine
        # ever supports running N strategies against one MockOrderManager,
        # this branch re-introduces the bug being fixed in MS-B.
        if self.client is None:
            return self._get_equity()

        # Live path: need a TradeLog to query own realized P&L. In production
        # `StrategyRunner.build()` always wires `_trade_log`, so this branch is
        # a defensive fallback for tests / future single-instance call sites.
        if self._trade_log is None or self._strategy_name is None:
            # No way to attribute realized P&L — return the static
            # initial_capital. Peak ratchet stays at initial_capital and a
            # drawdown to ≤92% of initial WILL fire the 8% CB. Not strictly
            # conservative; just isolated from cross-strategy contamination.
            return self._initial_capital

        try:
            # Lexical compare against the epoch yields "all-time" realized P&L.
            realized = self._trade_log.realized_pnl_since(
                self._strategy_name, "1970-01-01T00:00:00+00:00"
            )
        except Exception as exc:
            logger.warning("%s: realized-pnl fetch failed: %s", self.name, exc)
            return self._initial_capital

        unrealized = 0.0
        if self._in_position and self._position_shares > 0 and self._closes:
            # NOTE: live `_closes[-1]` is the previous daily close (yfinance is
            # refreshed inside on_tick at 16:10 ET). Between ticks this mark is
            # hours stale; with an 8% CB threshold the lag is acceptable.
            current_close = self._closes[-1]
            unrealized = (current_close - self._entry_price) * self._position_shares

        return self._initial_capital + realized + unrealized

    def _get_vix(self, today: date) -> Optional[float]:
        """Backtest: from feed external series. Live: from VIXFeed."""
        if self.client is None and self.feed is not None and hasattr(self.feed, "get_external"):
            return self.feed.get_external("vix", today)
        if self._vix_feed is not None:
            # client is not None here (live mode); use latest close
            return self._vix_feed.get_latest_close()
        return None

    def _refresh_history(self) -> bool:
        """Fetch last N days of SPY adjusted-close history from yfinance (live only)."""
        from data.historical import HistoricalDataLoader
        from datetime import datetime as dt

        end = dt.now().strftime("%Y-%m-%d")
        start = (dt.now() - timedelta(days=_LIVE_HISTORY_DAYS)).strftime("%Y-%m-%d")
        try:
            df = HistoricalDataLoader.load_yfinance(self.symbol, start, end)
            self._closes = list(df["close"].astype(float))
            self._highs = list(df["high"].astype(float))
            self._lows = list(df["low"].astype(float))
            self._bar_dates = [idx.date() if hasattr(idx, "date") else idx for idx in df.index]
            return True
        except Exception as exc:
            logger.warning("%s: history refresh failed — skipping tick: %s", self.name, exc)
            return False

    def _reconcile_position(self) -> None:
        """On startup, check if we hold a position from before this session."""
        try:
            for pos in self.om.get_positions():
                if pos.symbol == self.symbol and pos.quantity > 0:
                    self._in_position = True
                    self._position_shares = int(pos.quantity)
                    # MS-A1: if state file did not provide an entry price (clean
                    # install / corrupted state), fall back to broker avg_cost.
                    # Safe today because RSI2MR owns SPY exclusively (MS-D guards
                    # future shared-symbol cases at REGISTRY build time).
                    if self._entry_price <= 0 and pos.avg_cost > 0:
                        self._entry_price = float(pos.avg_cost)
                        logger.warning(
                            "%s: entry_price recovered from broker avg_cost=%.2f "
                            "(no state file value).",
                            self.name,
                            self._entry_price,
                        )
                    elif self._entry_price <= 0:
                        # IBKR sometimes reports avg_cost=0 (CLAUDE.md Q4).
                        # Surface loudly: cost_basis on the next SELL will be
                        # None and realized P&L attribution will miss this trade.
                        logger.error(
                            "%s: reconciled position has avg_cost=%.2f and no "
                            "state-file entry — cost_basis will be unavailable "
                            "for the next SELL.",
                            self.name,
                            pos.avg_cost,
                        )
                    logger.warning(
                        "%s: reconciled existing position %s x%d from broker "
                        "(entry_price=%.2f).",
                        self.name,
                        self.symbol,
                        self._position_shares,
                        self._entry_price,
                    )
        except Exception as exc:
            logger.warning("%s: position reconcile failed: %s", self.name, exc)

    def _calc_shares(self, entry: float, stop: float, target: float, equity: float) -> int:
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
                logger.warning("%s: plan_trade rejected — %s", self.name, exc)
                return 0
        from risk.position_sizer import PositionSizer

        shares = int(PositionSizer.risk_based(equity, entry, stop, risk_pct=0.02))
        return min(shares, max_by_cash)

    # ── State persistence ─────────────────────────────────────────────────────

    def _save_state(self) -> None:
        if not self._persist_state:
            return  # backtest path with default state file — skip disk I/O
        try:
            state = {
                "schema_version": _STATE_SCHEMA_VERSION,
                "consecutive_losses": self._consecutive_losses,
                "strategy_peak_equity": self._strategy_peak_equity,
                "circuit_breaker_until": (
                    self._circuit_breaker_until.isoformat() if self._circuit_breaker_until else None
                ),
                # MS-A1: persist entry price so cost_basis survives a restart
                # between BUY fill and SELL fill.
                "entry_price": self._entry_price,
                "in_position": self._in_position,
                # MS-K: partial-fill halt must survive restart so the operator
                # has full investigation context after a bot bounce.
                "partial_fill_halt": self._partial_fill_halt,
            }
            self._state_file_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_file_path.write_text(json.dumps(state, indent=2))
        except Exception as exc:
            logger.warning("%s: could not save state: %s", self.name, exc)

    def _load_state(self) -> None:
        if not self._persist_state:
            return  # backtest path with default state file — skip disk I/O
        try:
            if not self._state_file_path.exists():
                return
            state = json.loads(self._state_file_path.read_text())
            self._consecutive_losses = int(state.get("consecutive_losses", 0))
            self._strategy_peak_equity = float(
                state.get("strategy_peak_equity", self._initial_capital)
            )
            cb_until = state.get("circuit_breaker_until")
            if cb_until:
                self._circuit_breaker_until = date.fromisoformat(cb_until)
            # MS-B migration: pre-v2 state files used account-wide NetLiquidation
            # as the equity proxy, so the persisted peak/CB are contaminated.
            # Reset both fields once; the next on_tick will ratchet from
            # initial_capital using the new strategy-attributed equity.
            # Defensive parse: explicit `null` or non-int → treat as v1 so the
            # migration still fires (rather than crashing into the bare except
            # below and silently re-resetting on the next save).
            _raw_version = state.get("schema_version")
            schema_version = int(_raw_version) if isinstance(_raw_version, int) else 1
            if schema_version < _STATE_SCHEMA_VERSION:
                logger.warning(
                    "%s: state file schema v%d → v%d migration — resetting "
                    "strategy_peak_equity (%.2f → %.2f) and clearing "
                    "circuit_breaker_until (%s → None). Pre-MS-B values used "
                    "account-wide NetLiquidation and were unsafe.",
                    self.name,
                    schema_version,
                    _STATE_SCHEMA_VERSION,
                    self._strategy_peak_equity,
                    self._initial_capital,
                    self._circuit_breaker_until,
                )
                self._strategy_peak_equity = self._initial_capital
                self._circuit_breaker_until = None
            # MS-A1: recover entry price across restarts (only if state says we
            # are in position — keeps the pre-MS-A1 default-None path clean).
            if state.get("in_position"):
                ep = state.get("entry_price", 0.0)
                if ep:
                    self._entry_price = float(ep)
            # MS-K: restore partial-fill halt — operator needs the same
            # investigation context after a bot bounce.
            self._partial_fill_halt = bool(state.get("partial_fill_halt", False))
            logger.info(
                "%s: loaded state — consecutive_losses=%d peak_equity=%.2f cb_until=%s "
                "entry_price=%.2f",
                self.name,
                self._consecutive_losses,
                self._strategy_peak_equity,
                self._circuit_breaker_until,
                self._entry_price,
            )
        except Exception as exc:
            logger.warning("%s: could not load state (using defaults): %s", self.name, exc)

    # ── Metadata ──────────────────────────────────────────────────────────────

    @property
    def params(self) -> dict:
        return {
            "symbol": self.symbol,
            "sma_period": self._sma_period,
            "rsi_period": self._rsi_period,
            "rsi_oversold": self._rsi_oversold,
            "rsi_overbought": self._rsi_overbought,
            "vix_upper": self._vix_upper,
            "atr_multiplier": self._atr_multiplier,
            # Fixed constants (for audit trail completeness)
            "atr_period": self._ATR_PERIOD,
            "time_stop_bars": self._TIME_STOP_BARS,
            "cooldown_bars": self._COOLDOWN_BARS,
        }
