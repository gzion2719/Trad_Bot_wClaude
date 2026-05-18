from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from typing import Callable, Optional

from ib_insync import IB, Contract, Order, Stock, Ticker, Trade

from config.settings import IB_HOST, IB_PORT, IB_CLIENT_ID

logger = logging.getLogger(__name__)

# Market data modes
REALTIME = 1  # requires live data subscription
FROZEN = 2  # last available price when market is closed
DELAYED = 3  # 15-min delay, free
DELAYED_FROZEN = 4  # delayed + frozen when market closed

_CONNECT_TIMEOUT = 10  # seconds to wait for connection to be ready
_PRICE_TIMEOUT = 10  # seconds to wait for market data tick
_PRICE_POLL = 0.25  # poll interval for price
_PRICE_CANCEL_WAIT = 0.5  # cooldown after cancelMktData (respects IBKR pacing)
_QUALIFY_TIMEOUT = 10  # seconds to wait for qualifyContracts
_ACCOUNT_TIMEOUT = 10  # seconds to wait for accountSummary / portfolio (reuses same value)
_HEARTBEAT_TIMEOUT = 10  # seconds to wait for reqCurrentTime
_ORDER_TIMEOUT = 10  # seconds to wait for placeOrder / cancelOrder wire send
_MKT_DATA_TYPE_TIMEOUT = 5  # seconds for reqMarketDataType (small wire ping, no ack)
_THREADSAFE_RESULT_SLACK = 5  # extra seconds on Future.result vs the in-coroutine deadline
_RECONNECT_DELAYS = [2, 5, 10, 30, 60]  # backoff schedule in seconds


class IBKRClient:
    """
    Low-level wrapper around ib_insync.IB.

    Responsibilities:
      - Connection lifecycle (connect / disconnect / reconnect with backoff)
      - Market data mode management
      - Contract qualification and price fetching
      - Heartbeat to detect silent disconnections
      - Exposing the raw IB instance to higher-level components

    Does NOT place or track orders — that is OrderManager's job.
    """

    def __init__(
        self,
        host: str = IB_HOST,
        port: int = IB_PORT,
        client_id: int = IB_CLIENT_ID,
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self.ib = IB()
        self._on_disconnect_cb: Optional[Callable] = None
        # Saved by connect() on the main thread; used by _connect_async() in
        # ReconnectManager's daemon thread (Python 3.12 has no shared event loop).
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self, retries: int = 3) -> None:
        """
        Connect to TWS with automatic retry on failure.

        Args:
            retries: Number of reconnect attempts before raising.

        Raises:
            ConnectionError: All retry attempts exhausted.
        """
        # Save the event loop on the first call (always from the main thread).
        # ReconnectManager's daemon thread calls connect() on subsequent
        # disconnects; Python 3.12 provides no event loop in non-main threads,
        # so we schedule the async handshake on the saved main loop instead.
        if threading.current_thread() is threading.main_thread():
            self._main_loop = asyncio.get_event_loop()

        if self.ib.isConnected():
            logger.warning("Already connected — skipping.")
            return

        # retries = number of extra attempts after the first; total = retries + 1
        total_attempts = retries + 1
        last_exc: Optional[Exception] = None
        for attempt in range(1, total_attempts + 1):
            try:
                logger.info(
                    "Connecting to IBKR at %s:%s (clientId=%s, attempt %s/%s) …",
                    self._host,
                    self._port,
                    self._client_id,
                    attempt,
                    total_attempts,
                )
                # When reconnecting from ReconnectManager's daemon thread, the
                # main asyncio loop is already running (ib.run() in main.py).
                # run_coroutine_threadsafe schedules connectAsync() on it safely.
                if (
                    threading.current_thread() is not threading.main_thread()
                    and self._main_loop is not None
                    and self._main_loop.is_running()
                ):
                    future = asyncio.run_coroutine_threadsafe(
                        self.ib.connectAsync(
                            self._host,
                            self._port,
                            clientId=self._client_id,
                            timeout=_CONNECT_TIMEOUT,
                        ),
                        self._main_loop,
                    )
                    future.result(timeout=_CONNECT_TIMEOUT + 5)
                else:
                    self.ib.connect(
                        self._host,
                        self._port,
                        clientId=self._client_id,
                        readonly=False,
                        timeout=_CONNECT_TIMEOUT,
                    )
                # Always remove before adding — connect() may be called multiple times
                # (e.g., by ReconnectManager) and += would accumulate duplicate handlers,
                # causing _on_disconnected to fire N times on the next drop.
                try:
                    self.ib.disconnectedEvent -= self._on_disconnected
                except (TypeError, AttributeError):
                    pass  # not yet registered — safe to ignore
                self.ib.disconnectedEvent += self._on_disconnected

                # Wait until account state is populated (async handshake).
                # Use time.sleep — ib.sleep() is not safe from non-main threads.
                deadline = time.time() + _CONNECT_TIMEOUT
                while not self.ib.wrapper.accounts and time.time() < deadline:
                    time.sleep(0.1)

                if not self.ib.wrapper.accounts:
                    raise ConnectionError("Connected but account state never populated.")

                mode = DELAYED if self.is_paper else REALTIME
                self._set_market_data_type(mode)
                logger.info("Connected | account=%s | paper=%s", self.account, self.is_paper)
                if not self.is_paper:
                    logger.warning(
                        "!!! LIVE TRADING ACCOUNT DETECTED (account=%s) — "
                        "real money is at risk. Confirm this is intentional. !!!",
                        self.account,
                    )
                return  # success

            except Exception as exc:
                last_exc = exc
                if attempt < total_attempts:
                    delay = _RECONNECT_DELAYS[min(attempt - 1, len(_RECONNECT_DELAYS) - 1)]
                    logger.warning(
                        "Connection attempt %s/%s failed (%s). Retrying in %ss…",
                        attempt,
                        total_attempts,
                        exc,
                        delay,
                    )
                    time.sleep(delay)

        raise ConnectionError(
            f"Failed to connect to IBKR after {total_attempts} attempt(s). Last error: {last_exc}"
        )

    def disconnect(self) -> None:
        if not self.ib.isConnected():
            return
        self.ib.disconnect()
        logger.info("Disconnected from IBKR.")

    def _on_disconnected(self) -> None:
        logger.warning("IBKR connection dropped.")
        if self._on_disconnect_cb:
            self._on_disconnect_cb()

    def on_disconnect(self, callback: Callable) -> None:
        """Register a callback to be called when the connection drops."""
        self._on_disconnect_cb = callback

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    def is_alive(self) -> bool:
        """
        Check if the connection is truly alive by requesting server time.
        More reliable than isConnected() which can return True on a stale socket.

        Thread-safe: auto-routes via run_coroutine_threadsafe when called from a
        non-main thread (ReconnectManager daemon).
        """
        if not self.ib.isConnected():
            return False
        try:
            if self._needs_threadsafe_route():
                assert self._main_loop is not None  # narrowed by _needs_threadsafe_route

                async def _heartbeat():  # type: ignore[return]
                    return await self.ib.reqCurrentTimeAsync()  # type: ignore[attr-defined]

                fut = asyncio.run_coroutine_threadsafe(_heartbeat(), self._main_loop)
                t = fut.result(timeout=_HEARTBEAT_TIMEOUT)
            else:
                t = self.ib.reqCurrentTime()
            return t is not None
        except (RuntimeError, OSError, AttributeError, TimeoutError):
            return False
        except Exception:
            # concurrent.futures.TimeoutError is not a builtin TimeoutError on
            # all Python versions; catch broadly here -- a heartbeat failure
            # must never propagate. The caller treats False as "reconnect".
            return False

    # ------------------------------------------------------------------
    # Thread-safety routing
    # ------------------------------------------------------------------

    def _needs_threadsafe_route(self) -> bool:
        """
        True when an ib_insync call must be scheduled on the main asyncio loop.

        Returns False on the main thread (the sync ib_insync wrappers work
        normally there). Returns True on any other thread when the main loop
        is captured and running.

        Raises RuntimeError on a non-main thread when the main loop has not
        been captured yet (cold-start race: a daemon scheduler tick fires
        before connect() runs on the main thread). The previous design
        silently returned False here, which dropped daemon callers onto the
        broken sync path -- exactly the bug class this rule exists to fix.
        """
        if threading.current_thread() is threading.main_thread():
            return False
        if self._main_loop is None:
            raise RuntimeError(
                "IBKRClient called from a non-main thread before connect() ran. "
                "The main asyncio loop must be captured on the main thread first."
            )
        if not self._main_loop.is_running():
            raise RuntimeError(
                "IBKRClient called from a non-main thread but the main loop is "
                "not running. The main loop must be active to route ib_insync calls."
            )
        return True

    def sleep(self, seconds: float) -> None:
        """
        Thread-safe replacement for ib.sleep(seconds).

        On the main thread, defers to ib.sleep (drives the event loop while
        sleeping, which OrderManager relies on to let newOrderEvent fire).
        On a daemon thread with a running main loop, schedules asyncio.sleep on
        the main loop and blocks the daemon thread on the Future.
        On a daemon thread before connect() or after loop shutdown, falls back
        to plain time.sleep -- sleep is a wait, not a network call, so it does
        not need the loop to be available to be correct.
        """
        if threading.current_thread() is not threading.main_thread():
            if self._main_loop is not None and self._main_loop.is_running():
                fut = asyncio.run_coroutine_threadsafe(asyncio.sleep(seconds), self._main_loop)
                fut.result(timeout=seconds + 1.0)
            else:
                time.sleep(seconds)
        else:
            self.ib.sleep(seconds)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self.ib.isConnected()

    @property
    def is_paper(self) -> bool:
        # Paper accounts at IBKR always start with 'D'; live accounts start with 'U'.
        # This is more reliable than port-based
        # detection because IB Gateway paper can be configured on any port
        # via OverrideTwsApiPort.
        acct = self.account
        if acct and acct != "N/A":
            return acct.startswith("D")
        # Fallback for pre-connect checks: TWS paper port convention.
        return self._port == 7497

    @property
    def account(self) -> str:
        accounts = self.ib.wrapper.accounts
        return accounts[0] if accounts else "N/A"

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def _set_market_data_type(self, mode: int) -> None:
        """
        Set the market-data mode (REALTIME/FROZEN/DELAYED/DELAYED_FROZEN).

        Thread-safe: ib.reqMarketDataType -> Client.send -> Client.sendMsg calls
        getLoop() from the calling thread, which raises from a daemon thread
        (ReconnectManager). Without routing here, a reconnect that runs on the
        daemon thread fails inside connect()'s post-handshake step; TWS resets
        the data mode to REALTIME on every fresh session, and subsequent
        reqMktData calls on a paper account hit error 10089. Symptom: strategies
        that pull live prices (PingPong AAPL) stop trading after the first
        gateway auto-restart.
        """
        if self._needs_threadsafe_route():
            assert self._main_loop is not None

            async def _set() -> None:
                self.ib.reqMarketDataType(mode)

            fut = asyncio.run_coroutine_threadsafe(_set(), self._main_loop)
            fut.result(timeout=_MKT_DATA_TYPE_TIMEOUT + _THREADSAFE_RESULT_SLACK)
        else:
            self.ib.reqMarketDataType(mode)

        labels = {1: "realtime", 2: "frozen", 3: "delayed", 4: "delayed-frozen"}
        logger.info("Market data mode: %s", labels.get(mode, mode))

    def get_market_price(
        self,
        symbol: str,
        exchange: str = "SMART",
        currency: str = "USD",
        is_delayed: Optional[bool] = None,
    ) -> float:
        """
        Return the best available price for a stock symbol.

        Polls until a valid price arrives or timeout is reached.
        Priority: last trade → close → bid/ask midpoint.

        Args:
            is_delayed: If True, logs a staleness warning for strategies.
                        Defaults to True for paper accounts.

        Raises:
            ValueError: No valid price could be obtained within the timeout.
        """
        if is_delayed is None:
            is_delayed = self.is_paper

        if is_delayed:
            logger.debug(
                "Price for %s is DELAYED (15-min lag) — "
                "do not use for time-sensitive execution.",
                symbol,
            )

        # On a daemon thread the entire poll loop runs as a coroutine on the
        # main asyncio loop. Pre-fix, the sync ib_insync wrappers (qualifyContracts,
        # ib.sleep) called loop.run_until_complete() while the main loop was
        # already running -- RuntimeWarning, silent failure, zero fills.
        if self._needs_threadsafe_route():
            assert self._main_loop is not None
            fut = asyncio.run_coroutine_threadsafe(
                self._get_market_price_async(symbol, exchange, currency, is_delayed),
                self._main_loop,
            )
            return fut.result(timeout=_PRICE_TIMEOUT + _THREADSAFE_RESULT_SLACK)

        contract = self.qualify_contract(Stock(symbol, exchange, currency))
        ticker = self.ib.reqMktData(contract, "", False, False)
        price = None
        try:
            # Give TWS a moment to push the first tick before polling
            self.ib.sleep(1)

            # Poll until valid price or timeout
            deadline = time.time() + _PRICE_TIMEOUT
            while time.time() < deadline:
                price = self._best_price(ticker)
                if price is not None:
                    break
                self.ib.sleep(_PRICE_POLL)
        finally:
            # Always cancel subscription — even if an exception is raised.
            # Leaked subscriptions accumulate and IBKR will refuse new ones.
            self.ib.cancelMktData(contract)
            self.ib.sleep(_PRICE_CANCEL_WAIT)  # respect IBKR pacing limits

        if price is None:
            raise ValueError(
                f"Could not obtain a valid price for {symbol} within {_PRICE_TIMEOUT}s."
            )

        logger.debug(
            "Market price for %s: %.4f%s", symbol, price, " (delayed)" if is_delayed else ""
        )
        return price

    async def _get_market_price_async(
        self,
        symbol: str,
        exchange: str,
        currency: str,
        is_delayed: bool,
    ) -> float:
        """
        Async variant of get_market_price, scheduled on the main loop from a
        non-main caller via run_coroutine_threadsafe.

        Critical: every wait MUST be `await asyncio.sleep`, never `self.ib.sleep`
        (which would re-enter loop.run_until_complete from inside the loop --
        self-deadlock).

        Note: `is_delayed` is forwarded for logging only. The market-data mode
        (DELAYED vs REALTIME) is set once at connect time via _set_market_data_type
        and applies to all subsequent reqMktData calls automatically.
        """
        qualified = await self.ib.qualifyContractsAsync(Stock(symbol, exchange, currency))
        if not qualified:
            raise RuntimeError(f"Could not qualify contract for {symbol}")
        contract = next((c for c in qualified if c.primaryExchange), qualified[0])

        ticker = self.ib.reqMktData(contract, "", False, False)
        price: Optional[float] = None
        loop = asyncio.get_running_loop()
        try:
            await asyncio.sleep(1)  # first tick
            deadline = loop.time() + _PRICE_TIMEOUT
            while loop.time() < deadline:
                price = self._best_price(ticker)
                if price is not None:
                    break
                await asyncio.sleep(_PRICE_POLL)
        finally:
            self.ib.cancelMktData(contract)
            await asyncio.sleep(_PRICE_CANCEL_WAIT)

        if price is None:
            raise ValueError(
                f"Could not obtain a valid price for {symbol} within {_PRICE_TIMEOUT}s."
            )

        logger.debug(
            "Market price for %s: %.4f%s", symbol, price, " (delayed)" if is_delayed else ""
        )
        return price

    @staticmethod
    def _best_price(ticker: Ticker) -> Optional[float]:
        """
        Pick the most relevant price from a ticker, ignoring NaN / sentinel values.
        Priority: last trade → close → bid/ask midpoint.
        """

        def valid(v) -> bool:
            return v is not None and not math.isnan(v) and v > 0

        # Check last and close first
        for candidate in (ticker.last, ticker.close):
            if valid(candidate):
                return float(candidate)

        # bid/ask midpoint as fallback (checked separately so both must be valid)
        if valid(ticker.bid) and valid(ticker.ask):
            return (ticker.bid + ticker.ask) / 2.0

        # Individual bid or ask as last resort
        for candidate in (ticker.bid, ticker.ask):
            if valid(candidate):
                return float(candidate)

        return None

    # ------------------------------------------------------------------
    # Contract utilities
    # ------------------------------------------------------------------

    def qualify_contract(self, contract: Contract) -> Contract:
        """
        Ask IBKR to fill in missing contract fields (conId, primaryExchange, etc.).

        Returns the best match (primary exchange preferred over regional).
        Raises RuntimeError if the contract cannot be resolved.

        Thread-safe: auto-routes via run_coroutine_threadsafe when called from a
        non-main thread (any strategy's on_tick via OrderManager.place_order or
        get_market_price).
        """
        if self._needs_threadsafe_route():
            assert self._main_loop is not None

            async def _qualify() -> list:
                return await self.ib.qualifyContractsAsync(contract)  # type: ignore[attr-defined]

            fut = asyncio.run_coroutine_threadsafe(_qualify(), self._main_loop)
            qualified = fut.result(timeout=_QUALIFY_TIMEOUT + _THREADSAFE_RESULT_SLACK)
        else:
            qualified = self.ib.qualifyContracts(contract)

        if not qualified:
            raise RuntimeError(f"Could not qualify contract: {contract}")

        # Prefer contract with a primaryExchange set (avoids ambiguous regional exchanges)
        best = next((c for c in qualified if c.primaryExchange), qualified[0])
        logger.debug("Qualified contract: %s", best)
        return best

    # ------------------------------------------------------------------
    # Account info
    # ------------------------------------------------------------------

    def get_account_summary(self) -> list:
        """
        Return the cached account-summary tag list.

        Thread-safe: auto-routes via run_coroutine_threadsafe when called from a
        non-main thread. ib.accountSummary() triggers _run() on the first call
        (uncached); subsequent calls hit the cache and are safe even from a
        daemon, but we always route to keep the contract uniform.
        """
        if self._needs_threadsafe_route():
            assert self._main_loop is not None

            async def _fetch_summary() -> list:
                return await self.ib.accountSummaryAsync()  # type: ignore[attr-defined]

            fut = asyncio.run_coroutine_threadsafe(_fetch_summary(), self._main_loop)
            return fut.result(timeout=_ACCOUNT_TIMEOUT + _THREADSAFE_RESULT_SLACK)
        return self.ib.accountSummary()

    def get_positions(self) -> list:
        """
        Return current portfolio items with full P&L data.

        Uses ib.portfolio() (not ib.positions()) because portfolio() includes
        marketPrice, marketValue, unrealizedPNL, and realizedPNL.
        ib.positions() only has contract, position, and avgCost.

        Thread-safe: portfolio() has no Async variant (it's a pure dict read
        of the Wrapper cache), so we wrap it in a trivial coroutine that runs
        on the event-loop thread -- the sole writer of that cache.
        """
        if self._needs_threadsafe_route():
            assert self._main_loop is not None

            async def _fetch() -> list:
                return self.ib.portfolio()

            fut = asyncio.run_coroutine_threadsafe(_fetch(), self._main_loop)
            return fut.result(timeout=_ACCOUNT_TIMEOUT + _THREADSAFE_RESULT_SLACK)
        return self.ib.portfolio()

    # ------------------------------------------------------------------
    # Order-submission wire calls (thread-safe)
    # ------------------------------------------------------------------

    def ib_place_order(self, contract: Contract, ib_order: Order) -> Trade:
        """
        Thread-safe wrapper around ib.placeOrder.

        ib.placeOrder → Client.placeOrder → Client.send → Client.sendMsg calls
        getLoop() (asyncio.get_event_loop_policy().get_event_loop()), which raises
        'There is no current event loop in thread X' from any non-main thread.
        Routing via run_coroutine_threadsafe ensures sendMsg executes on the main
        loop thread where the loop is always available.

        Returns the Trade object returned by ib.placeOrder.
        """
        if self._needs_threadsafe_route():
            assert self._main_loop is not None

            async def _place() -> Trade:
                return self.ib.placeOrder(contract, ib_order)

            fut = asyncio.run_coroutine_threadsafe(_place(), self._main_loop)
            return fut.result(timeout=_ORDER_TIMEOUT + _THREADSAFE_RESULT_SLACK)
        return self.ib.placeOrder(contract, ib_order)

    def ib_cancel_order(self, order: Order, manual_cancel_time: str = "") -> None:
        """
        Thread-safe wrapper around ib.cancelOrder.

        Same sendMsg / getLoop issue as ib_place_order. cancelOrder return value
        (Optional[Trade]) is discarded; callers rely on cancelOrderEvent callbacks.
        """
        if self._needs_threadsafe_route():
            assert self._main_loop is not None

            async def _cancel() -> None:
                self.ib.cancelOrder(order, manual_cancel_time)

            fut = asyncio.run_coroutine_threadsafe(_cancel(), self._main_loop)
            fut.result(timeout=_ORDER_TIMEOUT + _THREADSAFE_RESULT_SLACK)
        else:
            self.ib.cancelOrder(order, manual_cancel_time)

    def get_account_summary_threadsafe(self) -> list:
        """Deprecated alias for get_account_summary() -- kept for back-compat.

        The base method now auto-detects the calling thread (see the
        "IBKRClient method thread-safety rule" in WORKFLOW.md). Migrate
        callers to get_account_summary() and delete this alias.
        Tracked: BACKLOG TS-CLEANUP.
        """
        return self.get_account_summary()

    def get_positions_threadsafe(self) -> list:
        """Deprecated alias for get_positions() -- kept for back-compat.

        The base method now auto-detects the calling thread (see the
        "IBKRClient method thread-safety rule" in WORKFLOW.md). Migrate
        callers to get_positions() and delete this alias.
        Tracked: BACKLOG TS-CLEANUP.
        """
        return self.get_positions()
