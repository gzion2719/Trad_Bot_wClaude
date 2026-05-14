"""
Strategy registry — single source of truth for all strategies the bot runs.

Each `StrategyConfig` declares one strategy with:
  - a unique `name` (used as the strategy_name on every fill / TradeLog row)
  - the strategy class + symbol + constructor params
  - a per-strategy `RiskCaps` block (independent caps, Decision B 2026-05-06)
  - a `Schedule` (`DailyAt` for wall-clock daily ticks, `Interval` for periodic)

main.py iterates `REGISTRY` and hands it to `StrategyRunner`. Add a new
strategy by appending one entry; do not modify main.py wiring.

The non-code attributes (name/symbol/schedule/caps/params/state_file_path)
are defined in `config/strategy_metadata.py` so the dashboard process can
read them without importing any strategy class. `_STRATEGY_CLASSES` below
binds each metadata entry to its concrete class — keeping the two maps in
lockstep is enforced by `tests/test_multi_strategy_runner.py::test_ms_*`.

Backward-compat: `RiskCaps`, `DailyAt`, `Interval`, `Schedule` are re-
exported from this module so existing `from config.strategies import ...`
imports keep working. New consumers should import the types directly from
`config.strategy_metadata`.

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

from typing import Any, Iterable, Optional, Type

from config.strategy_metadata import (
    STRATEGY_METADATA,
    DailyAt,
    Interval,
    RiskCaps,
    Schedule,
    StrategyMetadata,
)
from config.validator import ConfigError
from strategies.base_strategy import BaseStrategy
from strategies.rsi2_mr import RSI2MR_SPY
from strategies.sma_crossover import SMACrossover
from strategies.test_pingpong import PingPongTest

# Re-export moved types so `from config.strategies import RiskCaps, DailyAt, ...`
# keeps working for existing callers (runtime.strategy_runner, tests).
__all__ = [
    "DailyAt",
    "Interval",
    "RiskCaps",
    "Schedule",
    "StrategyConfig",
    "StrategyMetadata",
    "REGISTRY",
    "validate_registry",
]


class StrategyConfig:
    """One strategy entry in the registry.

    Composes a `StrategyMetadata` (the data) with a concrete strategy class
    (the code). Accepts either:

      StrategyConfig(metadata=meta, strategy_class=cls)       # new form
      StrategyConfig(name=, strategy_class=, symbol=, params=,
                     schedule=, risk_caps=, state_file_path=)  # legacy form

    The legacy form is preserved for tests and any external caller that
    builds configs in-line. New code should construct a `StrategyMetadata`
    and pass it explicitly.
    """

    metadata: StrategyMetadata
    strategy_class: Type[BaseStrategy]

    __slots__ = ("metadata", "strategy_class")

    def __init__(
        self,
        *,
        metadata: Optional[StrategyMetadata] = None,
        strategy_class: Type[BaseStrategy],
        name: Optional[str] = None,
        symbol: Optional[str] = None,
        schedule: Optional[Schedule] = None,
        risk_caps: Optional[RiskCaps] = None,
        params: Optional[dict[str, Any]] = None,
        state_file_path: Optional[str] = None,
    ) -> None:
        if metadata is None:
            if name is None or symbol is None or schedule is None or risk_caps is None:
                raise TypeError(
                    "StrategyConfig requires either `metadata=` or "
                    "(`name=`, `symbol=`, `schedule=`, `risk_caps=`)."
                )
            metadata = StrategyMetadata(
                name=name,
                symbol=symbol,
                schedule=schedule,
                risk_caps=risk_caps,
                params=params or {},
                state_file_path=state_file_path,
            )
        object.__setattr__(self, "metadata", metadata)
        object.__setattr__(self, "strategy_class", strategy_class)

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def symbol(self) -> str:
        return self.metadata.symbol

    @property
    def schedule(self) -> Schedule:
        return self.metadata.schedule

    @property
    def risk_caps(self) -> RiskCaps:
        return self.metadata.risk_caps

    @property
    def params(self) -> dict[str, Any]:
        return self.metadata.params

    def __repr__(self) -> str:
        return (
            f"StrategyConfig(name={self.name!r}, symbol={self.symbol!r}, "
            f"strategy_class={self.strategy_class.__name__})"
        )


# name -> strategy class. Updated in lockstep with STRATEGY_METADATA.
# A sync test asserts every key in this map appears in STRATEGY_METADATA
# and vice versa, so a half-added strategy fails at test time, not runtime.
_STRATEGY_CLASSES: dict[str, Type[BaseStrategy]] = {
    "SMACrossover-QQQ": SMACrossover,
    "RSI2MR-SPY": RSI2MR_SPY,
    "PingPongTest-AAPL": PingPongTest,  # TEST-ONLY — see config/strategy_metadata.py
}


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


def _build_registry() -> list[StrategyConfig]:
    """Compose StrategyConfigs from STRATEGY_METADATA + _STRATEGY_CLASSES.

    Raises ConfigError if any metadata entry has no class binding (catches
    the half-added-strategy failure mode at import time, not runtime).
    """
    built: list[StrategyConfig] = []
    for meta in STRATEGY_METADATA:
        klass = _STRATEGY_CLASSES.get(meta.name)
        if klass is None:
            raise ConfigError(
                f"STRATEGY_METADATA has entry {meta.name!r} but no class binding "
                f"in _STRATEGY_CLASSES. Add it to config/strategies.py."
            )
        built.append(StrategyConfig(metadata=meta, strategy_class=klass))
    # Surface orphaned class bindings too (class added but metadata missing).
    extra = set(_STRATEGY_CLASSES) - {m.name for m in STRATEGY_METADATA}
    if extra:
        raise ConfigError(
            f"_STRATEGY_CLASSES has entries with no STRATEGY_METADATA: {sorted(extra)}. "
            f"Add metadata in config/strategy_metadata.py."
        )
    return built


REGISTRY: list[StrategyConfig] = _build_registry()


# MS-D: validate at module load so any importer of REGISTRY (main.py, tests,
# scripts, dashboards) gets the same guard. Re-validated by StrategyRunner
# for callers that pass a custom config list.
validate_registry(REGISTRY)
