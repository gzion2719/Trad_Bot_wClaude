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
from typing import Any, Iterable, Type, Union

from config.validator import ConfigError
from strategies.base_strategy import BaseStrategy
from strategies.rsi2_mr import RSI2MR_SPY
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


def _normalize_symbol(symbol: str) -> str:
    """Canonical form for shared-symbol detection.

    Trim + uppercase only. When `StrategyConfig` grows an `exchange` or
    `contract_type` field, the key here must include them so e.g. SPY-stock
    and SPY-option are not flagged as the same instrument (BACKLOG: MS-D ext).
    """
    return symbol.strip().upper()


def validate_registry(configs: Iterable[StrategyConfig]) -> None:
    """Validate a registry of StrategyConfigs. Raises ConfigError on any defect.

    Checks (ordered for clearest error messages):
      1. Non-empty.
      2. Every entry has a non-empty name.
      3. Names are unique.
      4. Symbols are unique (case-insensitive).

    Note: blocking shared symbols narrows but does not close the MS-A1
    `avg_cost` ambiguity — manual same-symbol trades outside the bot still
    confound an account-level cost basis lookup.
    """
    seq = list(configs)
    if not seq:
        raise ConfigError("REGISTRY is empty: at least one StrategyConfig is required.")

    seen_names: set[str] = set()
    for cfg in seq:
        if not cfg.name:
            raise ConfigError("Every StrategyConfig needs a non-empty name.")
        if cfg.name in seen_names:
            raise ConfigError(f"Duplicate strategy name in REGISTRY: {cfg.name!r}.")
        seen_names.add(cfg.name)

    seen_symbols: dict[str, str] = {}  # normalized symbol -> first owner name
    for cfg in seq:
        key = _normalize_symbol(cfg.symbol)
        if key in seen_symbols:
            raise ConfigError(
                f"Strategies {seen_symbols[key]!r} and {cfg.name!r} both target "
                f"symbol {cfg.symbol!r} (normalized: {key!r}). Sharing a symbol "
                f"across strategies is unsafe with the current per-strategy P&L "
                f"attribution (MS-A1 avg_cost fallback becomes ambiguous). Pick "
                f"distinct symbols or wait for MS-G/H."
            )
        seen_symbols[key] = cfg.name


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
    StrategyConfig(
        name="RSI2MR-SPY",
        strategy_class=RSI2MR_SPY,
        symbol="SPY",
        params={
            "sma_period": 200,
            "rsi_period": 2,
            "rsi_oversold": 10.0,
            "rsi_overbought": 70.0,
            "vix_upper": 35.0,
            "atr_multiplier": 1.5,
        },
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


# MS-D: validate at module load so any importer of REGISTRY (main.py, tests,
# scripts, dashboards) gets the same guard. Re-validated by StrategyRunner
# for callers that pass a custom config list.
validate_registry(REGISTRY)
