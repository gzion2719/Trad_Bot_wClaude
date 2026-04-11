from __future__ import annotations

"""
DataFeed + IBKRFeed + BarScheduler — Task 3.1

DataFeed is the abstract interface all data sources must implement.
IBKRFeed is the IBKR implementation using real-time bars via ib_insync.
BarScheduler is the timer that drives strategy.on_tick() on a fixed interval.

Design principle: strategies only ever interact with DataFeed, never with
IBKRClient directly for price data. This means swapping to Polygon.io or
Alpaca later requires writing one new class, not touching any strategy code.

Usage:
    feed = IBKRFeed(client)
    feed.subscribe("AAPL", lambda bar: print(bar))
    feed.subscribe("MSFT", my_strategy.on_bar)

    scheduler = BarScheduler(strategy=my_strategy, interval_seconds=60)
    scheduler.start()
    # ... bot runs ...
    scheduler.stop()
    feed.unsubscribe_all()
"""

import logging
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from data.bar import Bar

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Abstract interface
# ══════════════════════════════════════════════════════════════════════════════

class DataFeed(ABC):
    """
    Abstract base class for all price data sources.

    Implement this to add a new data provider (Polygon.io, Alpaca, CSV, etc.)
    without changing any strategy code.
    """

    @abstractmethod
    def subscribe(self, symbol: str, callback: Callable[[Bar], None]) -> None:
        """
        Subscribe to bar updates for a symbol.

        The callback is called each time a new bar is ready.
        Multiple callbacks can be registered for the same symbol.
        """

    @abstractmethod
    def unsubscribe(self, symbol: str) -> None:
        """Cancel the subscription for a symbol and stop receiving bars."""

    def unsubscribe_all(self) -> None:
        """Unsubscribe all active symbols. Called on strategy shutdown."""
        for symbol in list(self._subscriptions.keys()):
            self.unsubscribe(symbol)

    @abstractmethod
    def get_latest(self, symbol: str) -> Optional[Bar]:
        """Return the most recent bar received for a symbol, or None."""

    @abstractmethod
    def is_live(self, symbol: str) -> bool:
        """
        Return True if data for this symbol is real-time (not delayed).
        Strategies should check this before making time-sensitive decisions.
        """


# ══════════════════════════════════════════════════════════════════════════════
# IBKR implementation
# ══════════════════════════════════════════════════════════════════════════════

class IBKRFeed(DataFeed):
    """
    Real-time (or delayed) bar feed using IBKR's reqRealTimeBars API.

    Delivers 5-second real-time bars. For longer bar sizes (1 min, 1 hour),
    use BarScheduler to poll get_latest() on a timer instead.

    Paper accounts receive delayed data — is_live() returns False for those.

    Args:
        client:   IBKRClient instance (must already be connected).
        bar_size: Passed to reqRealTimeBars. Only "5 secs" is supported by
                  IBKR's real-time bar API. For other sizes use BarScheduler.
    """

    def __init__(self, client) -> None:  # client: IBKRClient — avoids circular import
        self._client = client
        self._ib = client.ib
        # symbol → list of user callbacks
        self._subscriptions: Dict[str, List[Callable[[Bar], None]]] = {}
        # symbol → ib_insync RealTimeBar object (for unsubscribing)
        self._bar_objects: Dict[str, object] = {}
        # symbol → most recent Bar
        self._latest: Dict[str, Bar] = {}

    def subscribe(self, symbol: str, callback: Callable[[Bar], None]) -> None:
        """Subscribe to 5-second real-time bars for a symbol."""
        from ib_insync import Stock

        symbol = symbol.upper()
        if symbol not in self._subscriptions:
            self._subscriptions[symbol] = []
            contract = self._client.qualify_contract(Stock(symbol, "SMART", "USD"))
            bars = self._ib.reqRealTimeBars(
                contract,
                barSize=5,
                whatToShow="MIDPOINT",
                useRTH=False,
            )
            self._bar_objects[symbol] = bars
            # Wire the ib_insync updateEvent to our handler
            bars.updateEvent += self._make_handler(symbol)
            logger.info("IBKRFeed: subscribed to %s (5-sec bars).", symbol)

        self._subscriptions[symbol].append(callback)

    def unsubscribe(self, symbol: str) -> None:
        """Cancel the real-time bar subscription for a symbol."""
        symbol = symbol.upper()
        if symbol in self._bar_objects:
            self._ib.cancelRealTimeBars(self._bar_objects.pop(symbol))
            self._subscriptions.pop(symbol, None)
            logger.info("IBKRFeed: unsubscribed from %s.", symbol)

    def unsubscribe_all(self) -> None:
        for symbol in list(self._bar_objects.keys()):
            self.unsubscribe(symbol)

    def get_latest(self, symbol: str) -> Optional[Bar]:
        return self._latest.get(symbol.upper())

    def is_live(self, symbol: str) -> bool:
        """False for paper accounts (delayed data)."""
        return not self._client.is_paper

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _make_handler(self, symbol: str) -> Callable:
        """Create a closure that converts an ib_insync bar update to our Bar."""
        def handler(bars, has_new_bar):
            if not has_new_bar or not bars:
                return
            rt = bars[-1]
            bar = Bar(
                symbol=symbol,
                timestamp=datetime.fromtimestamp(rt.time, tz=timezone.utc),
                open=float(rt.open_),
                high=float(rt.high),
                low=float(rt.low),
                close=float(rt.close),
                volume=int(rt.volume),
                is_delayed=self._client.is_paper,
            )
            self._latest[symbol] = bar
            for cb in self._subscriptions.get(symbol, []):
                try:
                    cb(bar)
                except Exception as exc:
                    logger.error(
                        "IBKRFeed callback error for %s: %s", symbol, exc, exc_info=True
                    )
        return handler


# ══════════════════════════════════════════════════════════════════════════════
# Bar Scheduler
# ══════════════════════════════════════════════════════════════════════════════

class BarScheduler:
    """
    Drives strategy.on_tick() on a fixed time interval.

    Decouples "when to evaluate" from strategy logic. The strategy only
    implements what to do — the scheduler decides when.

    Runs in a daemon thread so it shuts down automatically when the
    main process exits.

    Usage:
        scheduler = BarScheduler(strategy=my_strategy, interval_seconds=60)
        scheduler.start()
        # ... bot runs via client.ib.run() ...
        scheduler.stop()
    """

    def __init__(self, strategy, interval_seconds: int = 60) -> None:
        self._strategy = strategy
        self._interval = interval_seconds
        self._stop_flag = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the scheduler. Calls on_tick() every interval_seconds."""
        if self._thread is not None:
            logger.warning("BarScheduler already running.")
            return
        self._stop_flag.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"BarScheduler[{self._strategy.name}]",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "BarScheduler started for %s (interval=%ds).",
            self._strategy.name, self._interval,
        )

    def stop(self) -> None:
        """Stop the scheduler gracefully."""
        self._stop_flag.set()
        if self._thread:
            self._thread.join(timeout=self._interval + 5)
        self._thread = None
        logger.info("BarScheduler stopped for %s.", self._strategy.name)

    def _run(self) -> None:
        while not self._stop_flag.is_set():
            try:
                self._strategy.on_tick()
            except Exception as exc:
                logger.error(
                    "BarScheduler: on_tick() raised for %s: %s",
                    self._strategy.name, exc, exc_info=True,
                )
            # Sleep in 1-second chunks so stop_flag is checked promptly
            for _ in range(self._interval):
                if self._stop_flag.is_set():
                    return
                time.sleep(1)
