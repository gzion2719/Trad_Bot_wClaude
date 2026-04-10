from __future__ import annotations

import logging
import math
from typing import Callable, Optional

from ib_insync import IB, Contract, Stock

from config.settings import IB_HOST, IB_PORT, IB_CLIENT_ID

logger = logging.getLogger(__name__)

# Market data modes
REALTIME = 1   # requires live data subscription
FROZEN   = 2   # last available price when market is closed
DELAYED  = 3   # 15-min delay, free
DELAYED_FROZEN = 4  # delayed + frozen when market closed


class IBKRClient:
    """
    Low-level wrapper around ib_insync.IB.

    Responsibilities:
      - Connection lifecycle (connect / disconnect)
      - Market data mode management
      - Contract qualification and price fetching
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

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self) -> None:
        if self.ib.isConnected():
            logger.warning("Already connected — skipping.")
            return
        logger.info(
            "Connecting to IBKR at %s:%s (clientId=%s) …",
            self._host, self._port, self._client_id,
        )
        self.ib.connect(self._host, self._port, clientId=self._client_id, readonly=False)
        self.ib.disconnectedEvent += self._on_disconnected

        # Paper accounts only get delayed data — set automatically
        mode = DELAYED if self.is_paper else REALTIME
        self._set_market_data_type(mode)

        logger.info("Connected | account=%s | paper=%s", self.account, self.is_paper)

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
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self.ib.isConnected()

    @property
    def is_paper(self) -> bool:
        return self._port == 7497

    @property
    def account(self) -> str:
        accounts = self.ib.wrapper.accounts
        return accounts[0] if accounts else "N/A"

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def _set_market_data_type(self, mode: int) -> None:
        self.ib.reqMarketDataType(mode)
        labels = {1: "realtime", 2: "frozen", 3: "delayed", 4: "delayed-frozen"}
        logger.info("Market data mode: %s", labels.get(mode, mode))

    def get_market_price(self, symbol: str, exchange: str = "SMART", currency: str = "USD") -> float:
        """
        Return the best available price for a stock symbol.

        Priority: last trade → close → bid/ask midpoint.
        Raises ValueError if no valid price can be obtained.
        """
        contract = self.qualify_contract(Stock(symbol, exchange, currency))
        ticker = self.ib.reqMktData(contract, "", False, False)
        self.ib.sleep(2)

        price = self._best_price(ticker)
        self.ib.cancelMktData(contract)  # cancel subscription — we only needed a snapshot

        if price is None:
            raise ValueError(f"Could not obtain a valid price for {symbol}.")

        logger.debug("Market price for %s: %.4f", symbol, price)
        return price

    @staticmethod
    def _best_price(ticker) -> Optional[float]:
        """Pick the most relevant price from a ticker, ignoring NaN / sentinel values."""
        def valid(v) -> bool:
            return v is not None and not math.isnan(v) and v > 0

        for candidate in (ticker.last, ticker.close, ticker.bid, ticker.ask):
            if valid(candidate):
                return float(candidate)

        # bid/ask midpoint as last resort
        if valid(ticker.bid) and valid(ticker.ask):
            return (ticker.bid + ticker.ask) / 2.0

        return None

    # ------------------------------------------------------------------
    # Contract utilities
    # ------------------------------------------------------------------

    def qualify_contract(self, contract: Contract) -> Contract:
        """
        Ask IBKR to fill in missing contract fields (conId, exchange, etc.).
        Raises RuntimeError if the contract cannot be resolved.
        """
        qualified = self.ib.qualifyContracts(contract)
        if not qualified:
            raise RuntimeError(f"Could not qualify contract: {contract}")
        logger.debug("Qualified contract: %s", qualified[0])
        return qualified[0]

    # ------------------------------------------------------------------
    # Account info
    # ------------------------------------------------------------------

    def get_account_summary(self) -> list:
        return self.ib.accountSummary()

    def get_positions(self) -> list:
        return self.ib.positions()
