from __future__ import annotations

"""
BacktestEngine + MockOrderManager — Task 3.3

The engine replays historical OHLCV data through a strategy, bar by bar,
using MockOrderManager as a drop-in replacement for the real OrderManager.

Key design principle: strategies run UNCHANGED in backtesting. The same
class that runs live also runs here — no code changes, no special modes.
Only the injected OrderManager differs.

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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Type

import pandas as pd

from backtester.portfolio import BacktestPortfolio
from data.bar import Bar
from models.order import (
    OrderAction, OrderRequest, OrderResult, OrderStatus, Position,
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

    Fill simulation: orders fill at the NEXT bar's open price (realistic —
    avoids look-ahead bias by not filling at the same bar's close).

    Args:
        portfolio:   BacktestPortfolio that holds cash and positions.
        symbol:      Primary symbol being traded (used for price lookup).
    """

    def __init__(self, portfolio: BacktestPortfolio) -> None:
        self._portfolio = portfolio
        self._current_bar: Optional[Bar] = None
        self._next_bar: Optional[Bar] = None   # fill price comes from here
        self._pending_orders: List[Dict] = []  # orders waiting to fill next bar
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

    def _process_pending_orders(self) -> None:
        """Fill any pending orders at the current bar's open price."""
        if not self._pending_orders:
            return

        fill_price = (
            self._current_bar.open if self._current_bar else None
        )
        if fill_price is None:
            return

        still_pending = []
        for order in self._pending_orders:
            symbol = order["symbol"]
            prices = self._portfolio._current_prices
            price  = prices.get(symbol, fill_price)

            result = self._portfolio.fill(
                symbol=symbol,
                action=order["action"],
                quantity=order["quantity"],
                price=price,
                order_id=order["order_id"],
                submitted_at=order["submitted_at"],
            )

            if result.status == OrderStatus.FILLED:
                self._open_orders.pop(order["order_id"], None)
                for cb in self._on_fill_callbacks:
                    cb(result)
            else:
                # Fill was skipped (insufficient cash, no position to sell)
                still_pending.append(order)

        self._pending_orders = still_pending

    # ------------------------------------------------------------------
    # OrderManager public interface
    # ------------------------------------------------------------------

    def place_order(
        self,
        request: OrderRequest,
        allow_duplicate: bool = False,
    ) -> OrderResult:
        """Queue an order. It fills at the next bar's open."""
        order_id = _next_order_id()
        submitted_at = datetime.now(timezone.utc)

        pending = {
            "symbol":       request.symbol,
            "action":       request.action,
            "quantity":     request.quantity,
            "order_id":     order_id,
            "submitted_at": submitted_at,
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
            "Backtest: order queued | %s %s x%s (fills next bar)",
            request.action.value, request.symbol, request.quantity,
        )
        return result

    def cancel_order(self, order_id: int) -> bool:
        if order_id in self._open_orders:
            self._open_orders.pop(order_id)
            self._pending_orders = [
                o for o in self._pending_orders if o["order_id"] != order_id
            ]
            return True
        return False

    def cancel_all(self, symbol: Optional[str] = None) -> int:
        if symbol:
            to_cancel = [
                oid for oid, r in self._open_orders.items()
                if r.symbol == symbol.upper()
            ]
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
# BacktestDataFeed  (lightweight — serves bars from DataFrame to strategy)
# ══════════════════════════════════════════════════════════════════════════════

class BacktestDataFeed:
    """
    Minimal DataFeed shim for backtesting.

    Serves the current bar from the engine's replay loop.
    Strategies that call feed.get_latest("AAPL") get the current bar.
    """

    def __init__(self, symbol: str) -> None:
        self._symbol = symbol.upper()
        self._current_bar: Optional[Bar] = None

    def _set_bar(self, bar: Bar) -> None:
        self._current_bar = bar

    def get_latest(self, symbol: str) -> Optional[Bar]:
        if symbol.upper() == self._symbol:
            return self._current_bar
        return None

    def is_live(self, symbol: str) -> bool:
        return False   # backtests are never live

    def subscribe(self, symbol: str, callback) -> None:
        pass   # no streaming in backtest — strategy pulls via get_latest()

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
    fills:           List[OrderResult]
    equity_curve:    pd.Series
    initial_capital: float
    final_equity:    float
    metrics:         Dict
    portfolio:       BacktestPortfolio

    def print_summary(self) -> None:
        """Print the formatted metrics table to the console."""
        from backtester.metrics import summary
        summary(
            fills=self.fills,
            equity_curve=self.equity_curve,
            initial_capital=self.initial_capital,
            portfolio=self.portfolio,
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

    Example:
        engine = BacktestEngine(
            strategy_class=MyStrategy,
            data=df,
            symbol="AAPL",
            initial_capital=100_000,
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
    ) -> None:
        self.strategy_class = strategy_class
        self.data = data.copy()
        self.symbol = symbol.upper()
        self.initial_capital = initial_capital
        self.commission = commission
        self.strategy_kwargs = strategy_kwargs or {}

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
        mock_om   = MockOrderManager(portfolio)
        data_feed = BacktestDataFeed(self.symbol)

        # Instantiate strategy with mock components
        # client=None is safe because strategies should use data_feed, not
        # client.get_market_price() during backtesting
        strategy = self.strategy_class(
            client=None,
            order_manager=mock_om,
            risk_manager=None,
            reconnect=None,
            **self.strategy_kwargs,
        )

        # Give strategy a way to access the current bar via a feed attribute
        # Strategies that want bar data should accept a feed= kwarg or use
        # self.om.get_positions() / get_open_orders() for state.
        if hasattr(strategy, "feed"):
            strategy.feed = data_feed

        bars = list(self.data.itertuples())
        n    = len(bars)

        logger.info(
            "Backtest starting | %s | %d bars | capital=$%.0f",
            self.symbol, n, self.initial_capital,
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

            # Update prices so risk/exposure checks are current
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
                    i, ts, exc, exc_info=True,
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
                open=float(last.close),   # use close as proxy fill price
                high=float(last.high),
                low=float(last.low),
                close=float(last.close),
                volume=int(last.volume),
            )
            mock_om._process_pending_orders()

        strategy.on_stop()

        fills        = portfolio.get_fills()
        equity_curve = pd.Series(
            portfolio.equity_curve,
            index=self.data.index[:len(portfolio.equity_curve)],
            name="equity",
        )
        final_equity = portfolio.current_equity()

        metrics = summary(
            fills=fills,
            equity_curve=equity_curve,
            initial_capital=self.initial_capital,
            portfolio=portfolio,
        )

        logger.info(
            "Backtest complete | %d fills | final equity=$%.2f | return=%.2f%%",
            len(fills), final_equity, metrics["total_return_pct"],
        )

        return BacktestResult(
            fills=fills,
            equity_curve=equity_curve,
            initial_capital=self.initial_capital,
            final_equity=final_equity,
            metrics=metrics,
            portfolio=portfolio,
        )
