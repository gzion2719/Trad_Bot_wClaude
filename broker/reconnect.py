from __future__ import annotations

"""
ReconnectManager — Task 2.1

Watches for TWS disconnections and automatically reconnects with exponential
backoff. Strategies pause cleanly during the gap by calling wait_for_connection()
at the top of on_tick().

Typical wiring in main.py:
    reconnect = ReconnectManager(client, order_manager)
    reconnect.start()

Typical usage inside a strategy:
    def on_tick(self):
        self.reconnect.wait_for_connection(timeout=60)
        if self.risk_manager.is_halted():
            return
        # ... rest of strategy logic ...
"""

import logging
import threading
import time
from typing import Callable, Optional

from broker.ibkr_client import IBKRClient

logger = logging.getLogger(__name__)

# Backoff schedule in seconds between reconnect attempts
_BACKOFF = [5, 10, 30, 60, 120]


class ReconnectManager:
    """
    Automatically reconnects to TWS after a disconnect.

    How it works:
      1. Registers itself as the IBKRClient's disconnect callback.
      2. On disconnect, clears an internal Event (all waiters block).
      3. A background daemon thread retries client.connect() with backoff.
      4. On success, calls order_manager.sync() and sets the Event (waiters unblock).
      5. If max_attempts is exhausted, logs CRITICAL and sets a halted flag.

    Thread safety: all state changes go through threading primitives.
    """

    def __init__(
        self,
        client: IBKRClient,
        order_manager,                          # OrderManager — avoids circular import
        on_reconnected: Optional[Callable] = None,
        max_attempts: int = 10,
    ) -> None:
        self._client = client
        self._om = order_manager
        self._on_reconnected_cb = on_reconnected
        self._max_attempts = max_attempts

        # Set = connected (strategies run normally). Cleared on disconnect.
        self._connected_event = threading.Event()

        # Set when all reconnect attempts are exhausted.
        self._halted = threading.Event()

        # Background daemon thread — started by start()
        self._thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """
        Arm the manager. Call once after the initial connection succeeds.

        Registers the disconnect callback and marks the connection as live.
        """
        if self._thread is not None:
            logger.warning("ReconnectManager already started — ignoring.")
            return

        # Mark as connected right away (we were just connected by the caller)
        self._connected_event.set()

        # Hook into the client's disconnect callback
        self._client.on_disconnect(self._on_disconnect)

        # Start the background thread
        self._thread = threading.Thread(
            target=self._reconnect_loop,
            name="ReconnectManager",
            daemon=True,          # dies automatically when main thread exits
        )
        self._thread.start()
        logger.info("ReconnectManager started.")

    def stop(self) -> None:
        """
        Disarm the manager. Call during clean shutdown.

        Does NOT disconnect — just stops the background thread.
        """
        self._stop_flag.set()
        self._connected_event.set()   # unblock any waiters so they can exit
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("ReconnectManager stopped.")

    def wait_for_connection(self, timeout: float = 60.0) -> bool:
        """
        Block until connected (or timeout expires).

        Call this at the top of every strategy on_tick() to pause cleanly
        during a reconnect gap rather than throwing connection errors.

        Args:
            timeout: Maximum seconds to wait. 0 = non-blocking check.

        Returns:
            True if connected, False if timeout expired or manager halted.
        """
        connected = self._connected_event.wait(timeout=timeout)
        if not connected:
            logger.warning(
                "wait_for_connection timed out after %.0fs — TWS may still be reconnecting.",
                timeout,
            )
        return connected

    @property
    def is_connected(self) -> bool:
        """True if the connection is currently live."""
        return self._connected_event.is_set()

    @property
    def is_halted(self) -> bool:
        """True if all reconnect attempts were exhausted. Bot should stop trading."""
        return self._halted.is_set()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_disconnect(self) -> None:
        """
        Called by IBKRClient when TWS drops.
        Clears the connected event so strategies block on wait_for_connection().
        The background thread detects this and starts the retry loop.
        """
        if self._stop_flag.is_set():
            return   # clean shutdown — don't trigger a reconnect
        logger.warning("Disconnect detected — pausing strategies and scheduling reconnect.")
        self._connected_event.clear()

    def _reconnect_loop(self) -> None:
        """
        Background thread: sleeps while connected, retries on disconnect.
        """
        while not self._stop_flag.is_set():
            # Wait for a disconnect signal (event gets cleared on disconnect)
            # Poll every 2s so we can respond to stop_flag promptly
            is_still_connected = self._connected_event.wait(timeout=2.0)
            if is_still_connected:
                # Still connected — nothing to do, loop back
                continue

            if self._stop_flag.is_set():
                break

            # Connection lost — attempt to reconnect
            logger.warning("Starting reconnect sequence (max %d attempts).", self._max_attempts)
            self._attempt_reconnect()

    def _attempt_reconnect(self) -> None:
        """Try to reconnect, applying exponential backoff between attempts."""
        for attempt in range(1, self._max_attempts + 1):
            if self._stop_flag.is_set():
                return

            logger.info(
                "Reconnect attempt %d/%d …", attempt, self._max_attempts
            )
            try:
                self._client.connect(retries=0)   # ReconnectManager owns the retry loop
                # Success — re-sync orders and notify strategies
                logger.info("Reconnected to TWS successfully.")
                self._om.sync()
                self._connected_event.set()
                if self._on_reconnected_cb:
                    self._on_reconnected_cb()
                return

            except Exception as exc:
                delay = _BACKOFF[min(attempt - 1, len(_BACKOFF) - 1)]
                if attempt < self._max_attempts:
                    logger.warning(
                        "Reconnect attempt %d/%d failed (%s). Waiting %ds…",
                        attempt, self._max_attempts, exc, delay,
                    )
                    # Sleep in small increments so stop_flag is checked promptly
                    for _ in range(delay):
                        if self._stop_flag.is_set():
                            return
                        time.sleep(1)
                else:
                    logger.critical(
                        "All %d reconnect attempts exhausted. Last error: %s. "
                        "Bot is halted — manual intervention required.",
                        self._max_attempts, exc,
                    )
                    self._halted.set()
                    # Leave _connected_event cleared so strategies keep blocking
                    # (they will check is_halted and exit their own loops)
