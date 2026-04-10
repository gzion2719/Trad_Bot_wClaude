from __future__ import annotations

from abc import ABC, abstractmethod
from broker.order_manager import OrderManager
from broker.ibkr_client import IBKRClient


class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies.

    Every strategy must implement:
      - on_start()  — called once when the strategy is activated
      - on_tick()   — called on each data update (price tick, bar close, etc.)
      - on_stop()   — called once when the strategy is deactivated

    Strategies receive a shared IBKRClient and OrderManager so they
    can fetch prices and place orders without managing the connection.
    """

    def __init__(self, client: IBKRClient, order_manager: OrderManager) -> None:
        self.client = client
        self.om = order_manager

    @abstractmethod
    def on_start(self) -> None:
        """Initialize state, subscribe to data, etc."""

    @abstractmethod
    def on_tick(self) -> None:
        """Evaluate conditions and act on each data update."""

    @abstractmethod
    def on_stop(self) -> None:
        """Clean up — cancel open orders, unsubscribe data, etc."""

    @property
    def name(self) -> str:
        return self.__class__.__name__
