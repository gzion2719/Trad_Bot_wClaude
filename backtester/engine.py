from __future__ import annotations

"""
BacktestEngine + MockOrderManager — Task 3.3 (extended for bracket simulation)

The engine replays historical OHLCV data through a strategy, bar by bar,
using MockOrderManager as a drop-in replacement for the real OrderManager.

Key design principle: strategies run UNCHANGED in backtesting. The same
class that runs live also runs here — no code changes, no special modes.
Only the injected OrderManager differs.

Bracket simulation (added for RSI2-MR):
  MKT orders  — fill at bar.open ± slippage_bps
  STP SELL    — triggers when bar.low ≤ stop_price; gap-through if bar.open < stop_price
  LMT SELL    — triggers when bar.high ≥ limit_price; fills at limit_price (no slip)
  GTC         — untriggered STP/LMT orders stay in queue across bars
  DAY         — untriggered orders expire at end-of-bar (not re-queued)

External data (VIX sidecar):
  Pass external_data={"VIX": vix_series} to BacktestEngine; the feed
  exposes feed.get_external("VIX", date) for strategies to read.

Usage:
    from backtester.engine import BacktestEngine
    from strategies.my_strategy import MyStrategy
    from data.historical import HistoricalDataLoader

    df = HistoricalDataLoader.load_yfinance("AAPL", "2023-01-01", "2024-01-01")
    engine = BacktestEngine(
        strategy_class=MyStrategy,
        data=df,
        symbol="AAPL",
        initial_capital=100_000,
    )
    result = engine.run()
    result.print_summary()
"""

import logging
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable, Dict, List, Optional, Type

import pandas as pd

from backtester.portfolio import BacktestPortfolio
from data.bar import Bar
from models.order import (
    OrderAction,
    OrderRequest,
    OrderResult,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)

logger = logging.getLogger(__name__)

# Incrementing counter for simulated order IDs
_order_id_counter = 0
_order_id_lock = threading.Lock()


def _next_order_id() -> int:
    global _order_id_counter
    with _order_id_lock:
        _order_id_counter += 1
        return _order_id_counter


# ══════════════════════════════════════════════════════════════════════════════
# MockOrderManager
# ══════════════════════════════════════════════════════════════════════════════


class MockOrderManager:
    """
    Drop-in replacement for OrderManager during backtesting.

    Implements the same public interface as OrderManager so strategies
    don't need to know whether they are live or in a backtest.

    Fill simulation:
      - MKT orders fill at the current bar's open ± slippage (next bar after placement).
      - STP SELL orders trigger when bar.low ≤ stop_price; fills at stop_price or
        bar.open on gap-down, minus slippage.
      - LMT SELL orders trigger when bar.high ≥ limit_price; fills at limit_price.
      - GTC orders remain in queue until triggered or cancelled.
      - DAY orders expire at end-of-bar if not triggered.

    Important callback-safety note:
      When a BUY fills and on_fill places bracket (STP+LMT) orders via place_order(),
      those new orders are appended to self._pending_orders directly. The processing
      loop works on a snapshot taken at the start of _process_pending_orders() so
      newly-added orders are not processed until the next bar.

    Args:
        portfolio:   BacktestPortfolio that holds cash and positions.
    """

    def __init__(self, portfolio: BacktestPortfolio) -> None:
        self._portfolio = portfolio
        self._current_bar: Optional[Bar] = None
        self._next_bar: Optional[Bar] = None
        self._pending_orders: List[Dict] = []
        self._open_orders: Dict[int, OrderResult] = {}

        # Callbacks — same signature as real OrderManager
        self._on_fill_callbacks: List[Callable[[OrderResult], None]] = []
        self._on_cancel_callbacks: List[Callable[[OrderResult], None]] = []
        self._on_error_callbacks: List[Callable[[int, int, str], None]] = []

    # ------------------------------------------------------------------
    # Called by engine to advance the bar
    # ------------------------------------------------------------------

    def _set_bars(self, current: Bar, next_bar: Optional[Bar]) -> None:
        """Engine calls this before on_tick() each bar."""
        self._current_bar = current
        self._next_bar = next_bar
        self._process_pending_orders()

    def _apply_slippage(self, price: float, action: OrderAction, slippage_bps: float) -> float:
        """Apply slippage: BUY pays more, SELL receives less."""
        factor = slippage_bps / 10_000.0
        if action == OrderAction.BUY:
            return price * (1.0 + factor)
        return price * (1.0 - factor)

    def _process_pending_orders(self) -> None:
        """
        Process pending orders against the current bar.

        Works on a snapshot of pending orders taken at entry so that bracket
        orders added by on_fill callbacks are not double-processed this bar.
        """
        if not self._pending_orders:
            return

        bar = self._current_bar
        if bar is None:
            return

        # Snapshot current pending; callbacks may append to self._pending_orders
        to_process = list(self._pending_orders)
        self._pending_orders = []  # new orders from callbacks land here

        still_pending: List[Dict] = []

        for order in to_process:
            order_type = order.get("order_type", OrderType.MARKET)
            action: OrderAction = order["action"]
            symbol: str = order["symbol"]
            stop_px: Optional[float] = order.get("stop_price")
            limit_px: Optional[float] = order.get("limit_price")
            slippage_bps: float = order.get("slippage_bps", 0.0)
            tif: TimeInForce = order.get("tif", TimeInForce.GTC)

            fill_price: Optional[float] = None
            triggered = False

            if order_type == OrderType.MARKET:
                fill_price = self._apply_slippage(bar.open, action, slippage_bps)
                triggered = True

            elif order_type == OrderType.STOP and action == OrderAction.SELL:
                if stop_px is not None and bar.low <= stop_px:
                    if bar.open <= stop_px:
                        # Gap-down through stop — fill at open (already worse than stop)
                        fill_price = self._apply_slippage(bar.open, action, slippage_bps)
                    else:
                        fill_price = self._apply_slippage(stop_px, action, slippage_bps)
                    triggered = True

            elif order_type == OrderType.LIMIT and action == OrderAction.SELL:
                if limit_px is not None and bar.high >= limit_px:
                    fill_price = limit_px  # limit fills get no additional slippage
                    triggered = True

            if not triggered:
                if tif == TimeInForce.GTC:
                    still_pending.append(order)
                # DAY orders that didn't trigger expire silently
                continue

            if fill_price is None:
                still_pending.append(order)
                continue

            result = self._portfolio.fill(
                symbol=symbol,
                action=action,
                quantity=order["quantity"],
                price=fill_price,
                order_id=order["order_id"],
                submitted_at=order["submitted_at"],
            )

            if result.status == OrderStatus.FILLED:
                self._open_orders.pop(order["order_id"], None)
                for cb in self._on_fill_callbacks:
                    cb(result)
            # INACTIVE = portfolio rejected a triggered order (e.g. bracket LMT arriving
            # after STP already closed the position).  Discard — do NOT re-queue.

        # non-triggered orders go first; callback-added orders (brackets) go after
        self._pending_orders = still_pending + self._pending_orders

    # ------------------------------------------------------------------
    # OrderManager public interface
    # ------------------------------------------------------------------

    def place_order(
        self,
        request: OrderRequest,
        allow_duplicate: bool = False,
    ) -> OrderResult:
        """
        Queue an order.

        MKT orders fill at the next bar's open.
        STP/LMT GTC orders rest in the queue until triggered or cancelled.

        If allow_duplicate=False (default), a pending MKT order for the same
        symbol and action is treated as a duplicate. STP/LMT orders are always
        allowed through regardless (brackets need multiple SELL orders per symbol).
        """
        is_contingent = request.order_type in (OrderType.STOP, OrderType.LIMIT)
        if not allow_duplicate and not is_contingent:
            for existing in self._pending_orders:
                if (
                    existing["symbol"] == request.symbol.upper()
                    and existing["action"] == request.action
                    and existing.get("order_type", OrderType.MARKET) == OrderType.MARKET
                ):
                    logger.debug(
                        "Backtest: duplicate MKT order blocked | %s %s "
                        "(pass allow_duplicate=True to override)",
                        request.action.value,
                        request.symbol,
                    )
                    return self._open_orders[existing["order_id"]]

        order_id = _next_order_id()
        submitted_at = datetime.now(timezone.utc)

        pending: Dict = {
            "symbol": request.symbol,
            "action": request.action,
            "quantity": request.quantity,
            "order_id": order_id,
            "submitted_at": submitted_at,
            "order_type": request.order_type,
            "stop_price": request.stop_price,
            "limit_price": request.limit_price,
            "tif": request.tif,
            "slippage_bps": request.backtest_slippage_bps or 0.0,
        }
        self._pending_orders.append(pending)

        result = OrderResult(
            order_id=order_id,
            symbol=request.symbol,
            action=request.action.value,
            quantity=request.quantity,
            order_type=request.order_type.value,
            tif=request.tif.value,
            status=OrderStatus.SUBMITTED,
            filled=0,
            remaining=request.quantity,
            avg_fill_price=None,
            limit_price=request.limit_price,
            stop_price=request.stop_price,
            submitted_at=submitted_at,
        )
        self._open_orders[order_id] = result

        logger.debug(
            "Backtest: order queued | %s %s %s x%s",
            request.order_type.value,
            request.action.value,
            request.symbol,
            request.quantity,
        )
        return result

    def cancel_order(self, order_id: int) -> bool:
        if order_id in self._open_orders:
            self._open_orders.pop(order_id)
            self._pending_orders = [o for o in self._pending_orders if o["order_id"] != order_id]
            return True
        return False

    def cancel_all(self, symbol: Optional[str] = None) -> int:
        if symbol:
            to_cancel = [oid for oid, r in self._open_orders.items() if r.symbol == symbol.upper()]
        else:
            to_cancel = list(self._open_orders.keys())
        for oid in to_cancel:
            self.cancel_order(oid)
        return len(to_cancel)

    def get_open_orders(self, symbol: Optional[str] = None) -> List[OrderResult]:
        orders = list(self._open_orders.values())
        if symbol:
            orders = [o for o in orders if o.symbol == symbol.upper()]
        return orders

    def get_positions(self) -> List[Position]:
        return self._portfolio.get_positions()

    def has_open_order(
        self,
        symbol: str,
        action: Optional[OrderAction] = None,
    ) -> bool:
        orders = self.get_open_orders(symbol)
        if action is None:
            return bool(orders)
        return any(o.action == action.value for o in orders)

    def sync(self) -> int:
        return len(self._open_orders)

    def current_equity(self) -> float:
        """Return current simulated portfolio equity (cash + mark-to-market positions)."""
        return self._portfolio.current_equity()

    # Callback registration — same API as real OrderManager
    def on_fill(self, cb: Callable[[OrderResult], None]) -> None:
        self._on_fill_callbacks.append(cb)

    def on_cancel(self, cb: Callable[[OrderResult], None]) -> None:
        self._on_cancel_callbacks.append(cb)

    def on_error(self, cb: Callable[[int, int, str], None]) -> None:
        self._on_error_callbacks.append(cb)

    def _clear_callbacks(self) -> None:
        self._on_fill_callbacks.clear()
        self._on_cancel_callbacks.clear()
        self._on_error_callbacks.clear()


# ══════════════════════════════════════════════════════════════════════════════
# BacktestDataFeed  (lightweight — serves bars + external series to strategy)
# ══════════════════════════════════════════════════════════════════════════════


class BacktestDataFeed:
    """
    Minimal DataFeed shim for backtesting.

    Serves the current bar from the engine's replay loop.
    Also serves sidecar data (e.g. VIX) via get_external().

    External series are injected by BacktestEngine via _set_external() before
    the replay loop. Strategies call feed.get_external("VIX", bar_date) to
    retrieve the value for a given date.
    """

    def __init__(self, symbol: str) -> None:
        self._symbol = symbol.upper()
        self._current_bar: Optional[Bar] = None
        # key → {date → float}; populated by engine from external_data kwarg
        self._external: Dict[str, Dict[date, float]] = {}

    def _set_bar(self, bar: Bar) -> None:
        self._current_bar = bar

    def _set_external(self, key: str, data: Dict[date, float]) -> None:
        """Inject a date-keyed external series (e.g. {"VIX": {date(2020,1,2): 15.3, ...}})."""
        self._external[key] = data

    def get_latest(self, symbol: str) -> Optional[Bar]:
        if symbol.upper() == self._symbol:
            return self._current_bar
        return None

    def get_external(self, key: str, d: date) -> Optional[float]:
        """Return the value for key on date d, or None if unavailable."""
        series = self._external.get(key)
        if series is None:
            return None
        d_key = d.date() if hasattr(d, "date") else d
        return series.get(d_key)

    def is_live(self, symbol: str) -> bool:
        return False

    def subscribe(self, symbol: str, callback) -> None:
        pass

    def unsubscribe(self, symbol: str) -> None:
        pass

    def unsubscribe_all(self) -> None:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# BacktestResult
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class BacktestResult:
    """
    Output of BacktestEngine.run().

    Attributes:
        fills:           All simulated fills during the backtest.
        equity_curve:    Portfolio equity at each bar.
        initial_capital: Starting capital.
        final_equity:    Ending capital.
        metrics:         Dict of computed performance metrics.
        portfolio:       The full BacktestPortfolio (for custom analysis).
    """

    fills: List[OrderResult]
    equity_curve: pd.Series
    initial_capital: float
    final_equity: float
    metrics: Dict
    portfolio: BacktestPortfolio

    def print_summary(self) -> None:
        """Print the formatted metrics table to the console."""
        from backtester.metrics import summary

        summary(
            fills=self.fills,
            equity_curve=self.equity_curve,
            initial_capital=self.initial_capital,
        )


# ══════════════════════════════════════════════════════════════════════════════
# BacktestEngine
# ══════════════════════════════════════════════════════════════════════════════


class BacktestEngine:
    """
    Replays historical OHLCV data through a strategy and returns results.

    The same strategy class used in live trading runs here unchanged.
    Only the injected OrderManager (MockOrderManager) differs.

    Args:
        strategy_class:  The strategy class to instantiate (not an instance).
        data:            DataFrame from HistoricalDataLoader. Must have columns:
                         open, high, low, close, volume and a DatetimeIndex.
        symbol:          Ticker symbol the data is for.
        initial_capital: Starting cash in USD.
        commission:      Flat fee per trade in USD (default $1.00).
        strategy_kwargs: Extra keyword arguments passed to strategy.__init__.
        external_data:   Optional dict of {key: pd.Series} for sidecar data
                         (e.g. {"VIX": vix_series}). Series must be date-indexed.
                         Accessible to the strategy via feed.get_external(key, date).

    Example:
        engine = BacktestEngine(
            strategy_class=RSI2MR_SPY,
            data=spy_df,
            symbol="SPY",
            initial_capital=50_000,
            external_data={"VIX": vix_series},
        )
        result = engine.run()
        result.print_summary()
    """

    def __init__(
        self,
        strategy_class: Type,
        data: pd.DataFrame,
        symbol: str,
        initial_capital: float = 100_000.0,
        commission: float = 1.0,
        strategy_kwargs: Optional[Dict] = None,
        external_data: Optional[Dict[str, pd.Series]] = None,
    ) -> None:
        self.strategy_class = strategy_class
        self.data = data.copy()
        self.symbol = symbol.upper()
        self.initial_capital = initial_capital
        self.commission = commission
        self.strategy_kwargs = strategy_kwargs or {}
        self.external_data = external_data or {}

        if data.empty:
            raise ValueError("data DataFrame is empty — nothing to backtest.")
        required = {"open", "high", "low", "close", "volume"}
        missing = required - set(data.columns)
        if missing:
            raise ValueError(f"data is missing required columns: {missing}")

    def run(self) -> BacktestResult:
        """
        Execute the backtest.

        For each bar in data:
          1. Update portfolio prices (mark to market)
          2. Set current/next bar on MockOrderManager (fills pending orders)
          3. Call strategy.on_tick()
          4. Snapshot equity

        Returns:
            BacktestResult with fills, equity curve, and metrics.
        """
        from backtester.metrics import summary

        portfolio = BacktestPortfolio(
            initial_capital=self.initial_capital,
            commission=self.commission,
        )
        mock_om = MockOrderManager(portfolio)
        data_feed = BacktestDataFeed(self.symbol)

        # Inject external series (e.g. VIX) into feed
        for key, series in self.external_data.items():
            ext_dict: Dict[date, float] = {}
            for ts, val in series.items():
                d = ts.date() if hasattr(ts, "date") else ts
                ext_dict[d] = float(val)
            data_feed._set_external(key, ext_dict)
            logger.debug("Backtest: external series %r injected (%d points)", key, len(ext_dict))

        # Instantiate strategy with mock components.
        # feed and symbol are passed explicitly — BaseStrategy.__init__ declares
        # them as named parameters so they are always available.
        # client=None is safe: strategies must use self.feed, not client.get_market_price(),
        # during a backtest. risk_manager=None means safe_place_order() skips risk checks.
        strategy = self.strategy_class(
            client=None,
            order_manager=mock_om,
            risk_manager=None,
            reconnect=None,
            feed=data_feed,
            symbol=self.symbol,
            **self.strategy_kwargs,
        )

        bars = list(self.data.itertuples())
        n = len(bars)

        logger.info(
            "Backtest starting | %s | %d bars | capital=$%.0f",
            self.symbol,
            n,
            self.initial_capital,
        )

        strategy.on_start()

        for i, row in enumerate(bars):
            ts = row.Index
            current_bar = Bar(
                symbol=self.symbol,
                timestamp=ts if hasattr(ts, "tzinfo") else ts.to_pydatetime(),
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=int(row.volume),
                is_delayed=False,
            )
            next_bar = None
            if i + 1 < n:
                nr = bars[i + 1]
                next_bar = Bar(
                    symbol=self.symbol,
                    timestamp=nr.Index,
                    open=float(nr.open),
                    high=float(nr.high),
                    low=float(nr.low),
                    close=float(nr.close),
                    volume=int(nr.volume),
                )

            # Update prices so equity checks are current
            portfolio.update_prices({self.symbol: current_bar.close})

            # Advance bar — fills pending orders at this bar's open
            mock_om._set_bars(current_bar, next_bar)
            data_feed._set_bar(current_bar)

            # Run strategy logic
            try:
                strategy.on_tick()
            except Exception as exc:
                logger.error(
                    "Strategy on_tick() raised at bar %d (%s): %s",
                    i,
                    ts,
                    exc,
                    exc_info=True,
                )

            # Snapshot equity after this bar
            portfolio.snapshot_equity()

        # Fill any orders queued on the last bar (no next bar — fill at last close)
        if mock_om._pending_orders and len(bars) > 0:
            last = bars[-1]
            portfolio.update_prices({self.symbol: float(last.close)})
            mock_om._current_bar = Bar(
                symbol=self.symbol,
                timestamp=last.Index,
                open=float(last.close),  # use close as proxy fill price
                high=float(last.high),
                low=float(last.low),
                close=float(last.close),
                volume=int(last.volume),
            )
            mock_om._process_pending_orders()

        strategy.on_stop()

        fills = portfolio.get_fills()
        equity_curve = pd.Series(
            portfolio.equity_curve,
            index=self.data.index[: len(portfolio.equity_curve)],
            name="equity",
        )
        final_equity = portfolio.current_equity()

        metrics = summary(
            fills=fills,
            equity_curve=equity_curve,
            initial_capital=self.initial_capital,
        )

        logger.info(
            "Backtest complete | %d fills | final equity=$%.2f | return=%.2f%%",
            len(fills),
            final_equity,
            metrics["total_return_pct"],
        )

        return BacktestResult(
            fills=fills,
            equity_curve=equity_curve,
            initial_capital=self.initial_capital,
            final_equity=final_equity,
            metrics=metrics,
            portfolio=portfolio,
        )
