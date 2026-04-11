from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from broker.ibkr_client import IBKRClient
from broker.order_manager import OrderManager


class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies.

    Every strategy must implement:
      - on_start()  — called once when the strategy is activated
      - on_tick()   — called on each data update (price tick, bar close, etc.)
      - on_stop()   — called once when the strategy is deactivated

    Strategies receive all infrastructure components as constructor arguments.
    They should NOT import or instantiate IBKRClient/OrderManager/RiskManager
    themselves — those are injected by main.py.

    Recommended on_tick() pattern:
        def on_tick(self):
            # 1. Pause cleanly if TWS is reconnecting
            if self.reconnect and not self.reconnect.wait_for_connection(timeout=60):
                return
            # 2. Stop trading if daily loss ceiling hit
            if self.risk_manager and self.risk_manager.is_halted():
                return
            # 3. Your strategy logic here
            price = self.client.get_market_price(self.symbol)
            ...
            # 4. Risk-check before placing
            self.risk_manager.check(request, price)
            self.om.place_order(request)
    """

    def __init__(
        self,
        client: IBKRClient,
        order_manager: OrderManager,
        risk_manager=None,    # RiskManager — Optional to avoid circular import at class level
        reconnect=None,       # ReconnectManager — Optional for same reason
    ) -> None:
        self.client = client
        self.om = order_manager
        self.risk_manager = risk_manager
        self.reconnect = reconnect

    @abstractmethod
    def on_start(self) -> None:
        """Initialize state, subscribe to data, set up indicators, etc."""

    @abstractmethod
    def on_tick(self) -> None:
        """Evaluate conditions and act on each data update."""

    @abstractmethod
    def on_stop(self) -> None:
        """Clean up — cancel open orders, unsubscribe data, flush state, etc."""

    @property
    def name(self) -> str:
        return self.__class__.__name__
