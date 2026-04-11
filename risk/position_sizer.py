from __future__ import annotations

"""
PositionSizer — Task 2.3

A stateless utility for calculating how many shares to buy.

Usage:
    from risk.position_sizer import PositionSizer

    shares = PositionSizer.percent_of_equity(equity=50_000, price=150.0, pct=0.02)
    # → 6 shares (2% of $50,000 = $1,000 / $150 = 6.66 → 6)
"""

import math
import logging

logger = logging.getLogger(__name__)


class PositionSizer:
    """
    Static methods for position sizing. No state, no side effects.

    All methods return an integer number of shares (minimum 1).
    All methods log the result at DEBUG level so sizing decisions are auditable.
    """

    @staticmethod
    def fixed(shares: int) -> int:
        """
        Return a fixed share count unchanged.

        Use this when you want a hard-coded quantity — useful for
        testing or simple strategies where size doesn't vary.

        Args:
            shares: Number of shares to trade.

        Returns:
            shares (unchanged, minimum 1).
        """
        result = max(1, int(shares))
        logger.debug("PositionSizer.fixed → %d shares", result)
        return result

    @staticmethod
    def percent_of_equity(equity: float, price: float, pct: float) -> int:
        """
        Size a position as a percentage of total account equity.

        Example:
            equity=$50,000, price=$150, pct=0.02 (2%)
            → $1,000 / $150 = 6.66 → 6 shares

        Args:
            equity: Total account value in USD.
            price:  Current price per share in USD.
            pct:    Fraction of equity to deploy, e.g. 0.02 = 2%.

        Returns:
            Number of shares (floor division, minimum 1).

        Raises:
            ValueError: If equity or price are non-positive, or pct is out of range.
        """
        if equity <= 0:
            raise ValueError(f"equity must be positive, got {equity}")
        if price <= 0:
            raise ValueError(f"price must be positive, got {price}")
        if not (0 < pct <= 1.0):
            raise ValueError(f"pct must be between 0 and 1.0, got {pct}")

        dollar_amount = equity * pct
        result = max(1, int(math.floor(dollar_amount / price)))
        logger.debug(
            "PositionSizer.percent_of_equity | equity=%.2f | price=%.2f | pct=%.1f%% "
            "→ $%.2f / $%.2f = %d shares",
            equity, price, pct * 100, dollar_amount, price, result,
        )
        return result

    @staticmethod
    def kelly(
        win_rate: float,
        win_loss_ratio: float,
        equity: float,
        price: float,
        max_fraction: float = 0.25,
    ) -> int:
        """
        Size a position using the Kelly Criterion, capped at max_fraction.

        The Kelly formula determines the theoretically optimal fraction of equity
        to bet based on historical win rate and the ratio of average win to average loss.

        Formula:
            kelly_f = win_rate - (1 - win_rate) / win_loss_ratio

        Capped at max_fraction ("half-Kelly" or better) to limit risk.

        Example:
            win_rate=0.55, win_loss_ratio=1.5, equity=$50,000, price=$100
            kelly_f = 0.55 - 0.45/1.5 = 0.55 - 0.30 = 0.25
            capped at 0.25 → $12,500 / $100 = 125 shares

        Args:
            win_rate:       Historical win rate (0.0–1.0). e.g. 0.55 = 55% wins.
            win_loss_ratio: avg_win / avg_loss. e.g. 1.5 means wins are 1.5× losses.
            equity:         Total account value in USD.
            price:          Current price per share in USD.
            max_fraction:   Cap on Kelly fraction. Default 0.25 (25% of equity max).
                            Set lower (e.g., 0.1) for more conservative sizing.

        Returns:
            Number of shares (floor division, minimum 1).

        Raises:
            ValueError: If inputs are out of valid range.
        """
        if not (0 < win_rate < 1):
            raise ValueError(f"win_rate must be between 0 and 1, got {win_rate}")
        if win_loss_ratio <= 0:
            raise ValueError(f"win_loss_ratio must be positive, got {win_loss_ratio}")
        if equity <= 0:
            raise ValueError(f"equity must be positive, got {equity}")
        if price <= 0:
            raise ValueError(f"price must be positive, got {price}")
        if not (0 < max_fraction <= 1.0):
            raise ValueError(f"max_fraction must be between 0 and 1.0, got {max_fraction}")

        kelly_f = win_rate - (1 - win_rate) / win_loss_ratio

        if kelly_f <= 0:
            logger.warning(
                "PositionSizer.kelly → negative Kelly fraction (%.4f). "
                "This strategy has negative expected value — returning minimum 1 share.",
                kelly_f,
            )
            return 1

        fraction = min(kelly_f, max_fraction)
        dollar_amount = equity * fraction
        result = max(1, int(math.floor(dollar_amount / price)))

        logger.debug(
            "PositionSizer.kelly | win_rate=%.2f | W/L=%.2f | kelly_f=%.4f "
            "| capped_f=%.4f | equity=%.2f | price=%.2f → %d shares",
            win_rate, win_loss_ratio, kelly_f, fraction, equity, price, result,
        )
        return result
