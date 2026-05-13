"""
Strategy metadata — class-free description of every registered strategy.

This module is the *source of truth* for non-code strategy attributes
(name, symbol, schedule, risk caps, params, optional state-file path).
`config/strategies.py` composes the full `REGISTRY` by zipping each
metadata entry with its concrete strategy class.

The split exists so the dashboard process can read strategy metadata
without importing the strategy classes themselves (and their transitive
dependencies — yfinance, file I/O at import, etc.). Importing this
module is side-effect-free: no validation, no I/O, no class loading.

Bot side  : `from config.strategies import REGISTRY` (loads classes too)
Dashboard : `from config.strategy_metadata import STRATEGY_METADATA`

Adding a new strategy = two-step:
  1. Append a `StrategyMetadata(...)` entry to `STRATEGY_METADATA` below.
  2. Add the matching `name -> class` entry to `_STRATEGY_CLASSES` in
     `config/strategies.py`.
A synchronization test in `tests/test_multi_strategy_runner.py` asserts
the two maps stay in lockstep.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Union


@dataclass(frozen=True)
class RiskCaps:
    """Per-strategy RiskManager configuration. All caps are independent."""

    max_order_value: float
    max_position_value: float
    max_daily_loss: float  # negative number; see config/strategies.py docstring
    max_open_orders: int = 10
    max_risk_per_trade_pct: float = 0.02
    min_reward_risk_ratio: float = 3.0


@dataclass(frozen=True)
class DailyAt:
    """Fire `on_tick()` once per day at a wall-clock time in the given tz."""

    hour: int
    minute: int
    tz: str = "America/New_York"


@dataclass(frozen=True)
class Interval:
    """Fire `on_tick()` every `seconds`. Stops after 5 consecutive errors."""

    seconds: int


Schedule = Union[DailyAt, Interval]


@dataclass(frozen=True)
class StrategyMetadata:
    """Class-free description of one registered strategy.

    `state_file_path` is the path (relative to repo root) of the strategy's
    persisted-state JSON, when one exists. SMA Crossover has no state file
    (it derives in_position from broker positions); RSI2MR-SPY persists to
    `data/rsi2_mr_state.json`. The dashboard reads these files directly
    via this explicit map — never builds a path from the `name` field, so
    URL-path traversal is structurally impossible.
    """

    name: str
    symbol: str
    schedule: Schedule
    risk_caps: RiskCaps
    params: dict[str, Any] = field(default_factory=dict)
    state_file_path: Optional[str] = None


# ---------------------------------------------------------------------------
# Source-of-truth registry
# ---------------------------------------------------------------------------


STRATEGY_METADATA: list[StrategyMetadata] = [
    StrategyMetadata(
        name="SMACrossover-QQQ",
        symbol="QQQ",
        schedule=DailyAt(hour=16, minute=10),
        risk_caps=RiskCaps(
            max_order_value=120_000.0,
            max_position_value=100_000.0,
            max_daily_loss=-2_000.0,
            max_open_orders=10,
            max_risk_per_trade_pct=0.02,
            min_reward_risk_ratio=3.0,
        ),
        params={"sma_fast": 10, "sma_slow": 30},
        state_file_path=None,
    ),
    StrategyMetadata(
        name="RSI2MR-SPY",
        symbol="SPY",
        schedule=DailyAt(hour=16, minute=10),
        risk_caps=RiskCaps(
            max_order_value=120_000.0,
            max_position_value=100_000.0,
            max_daily_loss=-2_000.0,
            max_open_orders=10,
            max_risk_per_trade_pct=0.02,
            min_reward_risk_ratio=3.0,
        ),
        params={
            "sma_period": 200,
            "rsi_period": 2,
            "rsi_oversold": 10.0,
            "rsi_overbought": 70.0,
            "vix_upper": 35.0,
            "atr_multiplier": 1.5,
        },
        state_file_path="data/rsi2_mr_state.json",
    ),
]


def get_metadata(name: str) -> Optional[StrategyMetadata]:
    """Return the `StrategyMetadata` for `name`, or None if not registered."""
    for m in STRATEGY_METADATA:
        if m.name == name:
            return m
    return None
