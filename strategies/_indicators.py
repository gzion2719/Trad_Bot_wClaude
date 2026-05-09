"""
Pure indicator functions for RSI2-MR strategy.

All functions are stateless: given a list of prices/bars, return a scalar.
No look-ahead: functions only use data up to and including the last element.

Hand-verification reference (used in tests):
  closes = [10, 11, 10, 9, 10, 11, 12, 11, 10, 11]
  sma(closes, 3) → (10+11+12)/3 ... wait, last 3 = [11, 10, 11] → 10.667
  sma([10, 11, 12], 3) → 11.0

  rsi_wilder([10,11,10,9,10], 2):
    changes = [+1, -1, -1, +1]
    first avg_gain = (1+0)/2 = 0.5, first avg_loss = (0+1)/2 = 0.5
    → RS=1, RSI=50.0

  ATR hand-check: see test_at01 in test_rsi2_mr.py
"""

from __future__ import annotations

from typing import Sequence


def sma(closes: Sequence[float], period: int) -> float:
    """
    Simple Moving Average of the last `period` values.

    Args:
        closes: Price series (oldest first).
        period: Look-back window.

    Returns:
        SMA of closes[-period:].

    Raises:
        ValueError: If fewer than `period` values are available.
    """
    if len(closes) < period:
        raise ValueError(f"sma: need {period} values, got {len(closes)}")
    window = closes[-period:]
    return sum(window) / period


def rsi_wilder(closes: Sequence[float], period: int = 2) -> float:
    """
    Wilder's RSI.

    Uses Wilder's smoothing (SMMA / RMA) as in Connors' original RSI(2).
    NOT the same as EMA-based RSI used by some charting platforms.

    Algorithm:
      1. Compute price changes: delta[i] = closes[i] - closes[i-1]
      2. Seed: avg_gain and avg_loss = simple average of first `period` abs changes
      3. Subsequent bars: avg_gain = (prev_avg_gain * (period-1) + gain) / period

    Args:
        closes: Price series (oldest first). Needs at least period+1 values.
        period: RSI look-back. Default 2 (Connors RSI2).

    Returns:
        RSI value in [0, 100].

    Raises:
        ValueError: If fewer than period+1 values are available.
    """
    if len(closes) < period + 1:
        raise ValueError(f"rsi_wilder: need {period + 1} values, got {len(closes)}")

    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    # Seed: simple average of first `period` bars
    seed_gains = [max(c, 0.0) for c in changes[:period]]
    seed_losses = [abs(min(c, 0.0)) for c in changes[:period]]
    avg_gain = sum(seed_gains) / period
    avg_loss = sum(seed_losses) / period

    # Wilder smoothing for remaining bars
    for c in changes[period:]:
        gain = max(c, 0.0)
        loss = abs(min(c, 0.0))
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def atr_wilder(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> float:
    """
    Wilder's Average True Range (ATR).

    True Range = max(high-low, |high-prev_close|, |low-prev_close|)
    Seeded with a simple average of the first `period` TRs, then Wilder-smoothed.

    Args:
        highs:   High prices (oldest first).
        lows:    Low prices (oldest first).
        closes:  Close prices (oldest first). Previous close is closes[i-1].
        period:  ATR look-back. Default 14 (Wilder's original).

    Returns:
        ATR value as a positive float.

    Raises:
        ValueError: If fewer than period+1 bars are available.
    """
    n = len(closes)
    if n < period + 1 or len(highs) < period + 1 or len(lows) < period + 1:
        raise ValueError(
            f"atr_wilder: need {period + 1} bars, got closes={n} highs={len(highs)} lows={len(lows)}"
        )

    # Compute True Ranges starting from bar 1 (needs prev close)
    trs: list[float] = []
    for i in range(1, n):
        h, lo, pc = highs[i], lows[i], closes[i - 1]
        tr = max(h - lo, abs(h - pc), abs(lo - pc))
        trs.append(tr)

    # Seed: simple average of first `period` TRs
    atr = sum(trs[:period]) / period

    # Wilder smoothing for remaining TRs
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period

    return atr
