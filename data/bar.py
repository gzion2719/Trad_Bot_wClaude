from __future__ import annotations

"""
Bar — Task 3.1

A single OHLCV bar (candlestick). The common currency between live data feeds,
historical data loaders, and the backtesting engine.

All data sources (IBKR, yfinance, CSV) normalize their output to this format
so strategies never need to know where data came from.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Bar:
    """
    One OHLCV price bar for a symbol over a time period.

    Frozen (immutable) — bars are facts, not mutable state.

    Attributes:
        symbol:     Ticker symbol, e.g. "AAPL"
        timestamp:  Bar open time (timezone-aware recommended)
        open:       Opening price
        high:       Highest price during the bar
        low:        Lowest price during the bar
        close:      Closing price
        volume:     Number of shares traded
        is_delayed: True if data is 15-min delayed (paper account).
                    Strategies can use this to add safeguards.
    """

    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    is_delayed: bool = False

    @property
    def mid(self) -> float:
        """Midpoint of the bar's range."""
        return (self.high + self.low) / 2.0

    @property
    def range(self) -> float:
        """High minus low."""
        return self.high - self.low

    def __repr__(self) -> str:
        return (
            f"Bar({self.symbol} {self.timestamp:%Y-%m-%d %H:%M} "
            f"O={self.open:.2f} H={self.high:.2f} L={self.low:.2f} "
            f"C={self.close:.2f} V={self.volume:,}"
            f"{' [delayed]' if self.is_delayed else ''})"
        )
