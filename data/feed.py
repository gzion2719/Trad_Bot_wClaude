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

# BarScheduler will stop itself after this many consecutive on_tick() errors.
_MAX_CONSECUTIVE_ERRORS = 5


# ══════════════════════════════════════════════════════════════════════════════
# Abstract interface
# ══════════════════════════════════════════════════════════════════════════════

class DataFeed(ABC):
    """
    Abstract base class for all price data sources.

    Implement this to add a new data provider (Polygon.io, Alpaca, CSV, etc.)
    without changing any strategy code.
    """

    def __init__(self) -> None:
        # Initialised here so DataFeed.unsubscribe_all() can iterate over it
        # even when called on a subclass that doesn't override unsubscribe_all().
        self._subscriptions: Dict[str, List[Callable[[Bar], None]]] = {}

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
        super().__init__()
        self._client = client
        self._ib = client.ib
        # symbol → ib_insync RealTimeBar object (for unsubscribing)
        self._bar_objects: Dict[str, object] = {}
        # symbol → the updateEvent handler closure (so we can detach it on unsubscribe)
        self._handlers: Dict[str, Callable] = {}
        # symbol → most recent Bar
        self._latest: Dict[str, Bar] = {}

    def subscribe(self, symbol: str, callback: Callable[[Bar], None]) -> None:
        """
        Subscribe to 5-second real-time bars for a symbol.

        Duplicate callbacks for the same symbol are silently ignored.
        If contract qualification fails, no partial state is written
        (atomic — either fully subscribed or not at all).
        """
        from ib_insync import Stock

        symbol = symbol.upper()
        if symbol not in self._subscriptions:
            # Qualify and start the bar stream BEFORE writing any state.
            # If qualify_contract() raises, we haven't dirtied _subscriptions.
            contract = self._client.qualify_contract(Stock(symbol, "SMART", "USD"))
            bars = self._ib.reqRealTimeBars(
                contract,
                barSize=5,
                whatToShow="MIDPOINT",
                useRTH=False,
            )
            handler = self._make_handler(symbol)
            try:
                bars.updateEvent += handler
            except Exception:
                # updateEvent registration failed — cancel the now-orphaned
                # IBKR stream immediately so it doesn't leak resources.
                try:
                    self._ib.cancelRealTimeBars(bars)
                except Exception:
                    pass
                raise   # propagate original error to caller

            # All objects ready — write state atomically
            self._bar_objects[symbol] = bars
            self._handlers[symbol] = handler
            self._subscriptions[symbol] = []
            logger.info("IBKRFeed: subscribed to %s (5-sec bars).", symbol)

        # Dedup: don't register the same callback twice for the same symbol
        if callback not in self._subscriptions[symbol]:
            self._subscriptions[symbol].append(callback)

    def unsubscribe(self, symbol: str) -> None:
        """Cancel the real-time bar subscription for a symbol."""
        symbol = symbol.upper()
        if symbol in self._bar_objects:
            bars = self._bar_objects.pop(symbol)
            # Detach the handler to prevent memory leak
            handler = self._handlers.pop(symbol, None)
            if handler is not None:
                try:
                    bars.updateEvent -= handler
                except Exception:
                    pass  # handler may already be detached
            self._ib.cancelRealTimeBars(bars)
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

    Safety: stops itself automatically after _MAX_CONSECUTIVE_ERRORS
    consecutive on_tick() failures to prevent runaway error loops.

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
        if self._thread and self._thread.is_alive():
            # Allow up to interval + 30s for the current on_tick() to finish.
            # The extra 30s guards against slow strategy logic (network calls,
            # heavy computation). If the thread is still alive after the join,
            # it is a daemon thread and will be killed when the process exits.
            join_timeout = self._interval + 30
            self._thread.join(timeout=join_timeout)
            if self._thread.is_alive():
                logger.warning(
                    "BarScheduler: thread for %s did not stop within %ds "
                    "— it is a daemon thread and will exit with the process.",
                    self._strategy.name, join_timeout,
                )
        self._thread = None
        logger.info("BarScheduler stopped for %s.", self._strategy.name)

    def _run(self) -> None:
        consecutive_errors = 0
        while not self._stop_flag.is_set():
            try:
                self._strategy.on_tick()
                consecutive_errors = 0   # reset on success
            except Exception as exc:
                consecutive_errors += 1
                logger.error(
                    "BarScheduler: on_tick() raised for %s: %s "
                    "(consecutive error %d/%d)",
                    self._strategy.name, exc,
                    consecutive_errors, _MAX_CONSECUTIVE_ERRORS,
                    exc_info=True,
                )
                if consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                    logger.critical(
                        "BarScheduler: %d consecutive errors for strategy %s "
                        "— stopping scheduler to prevent runaway loop. "
                        "Investigate and restart manually.",
                        consecutive_errors, self._strategy.name,
                    )
                    return   # exit the thread

            # Sleep in 1-second chunks so stop_flag is checked promptly
            for _ in range(self._interval):
                if self._stop_flag.is_set():
                    return
                time.sleep(1)
