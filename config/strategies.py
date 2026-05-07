"""
Strategy registry — single source of truth for all strategies the bot runs.

Each `StrategyConfig` declares one strategy with:
  - a unique `name` (used as the strategy_name on every fill / TradeLog row)
  - the strategy class + symbol + constructor params
  - a per-strategy `RiskCaps` block (independent caps, Decision B 2026-05-06)
  - a `Schedule` (`DailyAt` for wall-clock daily ticks, `Interval` for periodic)

main.py iterates `REGISTRY` and hands it to `StrategyRunner`. Add a new
strategy by appending one entry; do not modify main.py wiring.

NOTE on `max_daily_loss` per strategy: the PnLPoller currently feeds every
RiskManager the SAME account-level realized P&L (we don't yet attribute fills
per strategy in live mode — see CLAUDE.md "Known limitations"). So the
`max_daily_loss` cap on a per-strategy RiskManager fires when the ACCOUNT
total breaches the cap, not when that strategy alone has lost that much.
With one strategy registered this is identical to today's behavior.
With N>1 registered, set the cap to a value that won't trip on other
strategies' losses, OR wait for per-strategy P&L attribution (BACKLOG).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Type, Union

from strategies.base_strategy import BaseStrategy
from strategies.sma_crossover import SMACrossover


@dataclass(frozen=True)
class RiskCaps:
    """Per-strategy RiskManager configuration. All caps are independent."""

    max_order_value: float
    max_position_value: float
    max_daily_loss: float  # negative number; see module docstring
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
class StrategyConfig:
    """One strategy entry in the registry."""

    name: str  # unique label used as strategy_name on fills / TradeLog
    strategy_class: Type[BaseStrategy]
    symbol: str
    params: dict[str, Any] = field(default_factory=dict)
    schedule: Schedule = field(default_factory=lambda: DailyAt(16, 10))
    risk_caps: RiskCaps = field(
        default_factory=lambda: RiskCaps(
            max_order_value=120_000.0,
            max_position_value=100_000.0,
            max_daily_loss=-2_000.0,
        )
    )


REGISTRY: list[StrategyConfig] = [
    StrategyConfig(
        name="SMACrossover-QQQ",
        strategy_class=SMACrossover,
        symbol="QQQ",
        params={"sma_fast": 10, "sma_slow": 30},
        schedule=DailyAt(hour=16, minute=10),
        risk_caps=RiskCaps(
            max_order_value=120_000.0,
            max_position_value=100_000.0,
            max_daily_loss=-2_000.0,
            max_open_orders=10,
            max_risk_per_trade_pct=0.02,
            min_reward_risk_ratio=3.0,
        ),
    ),
]
