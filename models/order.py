from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

_log = logging.getLogger(__name__)


class OrderAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MKT"
    LIMIT = "LMT"
    STOP = "STP"
    STOP_LIMIT = "STP LMT"


class TimeInForce(str, Enum):
    DAY = "DAY"
    GTC = "GTC"   # Good Till Cancelled
    IOC = "IOC"   # Immediate or Cancel
    FOK = "FOK"   # Fill or Kill


class OrderStatus(str, Enum):
    PENDING_SUBMIT  = "PendingSubmit"
    PRE_SUBMITTED   = "PreSubmitted"
    SUBMITTED       = "Submitted"
    FILLED          = "Filled"
    PENDING_CANCEL  = "PendingCancel"   # cancellation sent, waiting for TWS confirmation
    CANCELLED       = "Cancelled"
    INACTIVE        = "Inactive"
    ERROR           = "Error"


@dataclass
class OrderRequest:
    """Validated input for placing an order."""
    symbol: str
    action: OrderAction
    quantity: float
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    tif: TimeInForce = TimeInForce.GTC
    exchange: str = "SMART"
    currency: str = "USD"

    def __post_init__(self):
        if self.quantity <= 0:
            raise ValueError(f"Quantity must be positive, got {self.quantity}")
        if self.limit_price is not None and self.limit_price <= 0:
            raise ValueError(f"limit_price must be positive, got {self.limit_price}")
        if self.stop_price is not None and self.stop_price <= 0:
            raise ValueError(f"stop_price must be positive, got {self.stop_price}")
        if self.order_type == OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit_price is required for LIMIT orders")
        if self.order_type == OrderType.STOP and self.stop_price is None:
            raise ValueError("stop_price is required for STOP orders")
        if self.order_type == OrderType.STOP_LIMIT and (
            self.limit_price is None or self.stop_price is None
        ):
            raise ValueError("Both limit_price and stop_price are required for STOP_LIMIT orders")
        self.symbol = self.symbol.upper().strip()
        # Warn if fractional quantity on a standard stock order
        if self.quantity != int(self.quantity):
            _log.warning(
                "Fractional quantity %.4f for %s — "
                "ensure this symbol supports fractional shares.",
                self.quantity, self.symbol,
            )


@dataclass
class OrderResult:
    """Snapshot of an order's current state."""
    order_id: int
    symbol: str
    action: str
    quantity: float
    order_type: str
    tif: str
    status: OrderStatus
    filled: float
    remaining: float
    avg_fill_price: Optional[float]   # None until at least partially filled
    limit_price: Optional[float]
    stop_price: Optional[float]
    submitted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_active(self) -> bool:
        return self.status in (
            OrderStatus.PENDING_SUBMIT,
            OrderStatus.PRE_SUBMITTED,
            OrderStatus.SUBMITTED,
        )

    @property
    def is_filled(self) -> bool:
        return self.status == OrderStatus.FILLED


@dataclass
class Position:
    """Current holding in a symbol."""
    symbol: str
    quantity: float              # positive = long, negative = short
    avg_cost: float
    market_price: Optional[float]    # None if IBKR hasn't pushed data yet
    market_value: Optional[float]
    unrealized_pnl: Optional[float]
    realized_pnl: Optional[float]
    account: str
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        return self.quantity < 0
