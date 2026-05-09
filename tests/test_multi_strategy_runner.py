"""
StrategyRunner tests — no IBKR connection needed.

Covers:
  - Registry validation (empty / duplicate names)
  - build() constructs N RiskManagers + N strategies, tags _strategy_name
  - Fill routing: a fill from strategy A bumps only A's RiskManager + TradeLog row
  - Per-strategy halt isolation: strategy A's max_daily_loss ≠ strategy B's
  - DailyAt / Interval scheduler fires on_tick at least once and stops cleanly
"""

from __future__ import annotations

import threading
from typing import Callable, List
from unittest.mock import MagicMock

import pytest

from config.strategies import DailyAt, Interval, RiskCaps, StrategyConfig
from config.validator import ConfigError
from models.order import OrderResult, OrderStatus
from runtime.strategy_runner import StrategyRunner
from strategies.base_strategy import BaseStrategy

# ── Fakes ─────────────────────────────────────────────────────────────────────


class _FakeOrderManager:
    """Stores on_fill callbacks; tests trigger fills manually."""

    def __init__(self) -> None:
        self._on_fill_callbacks: List[Callable[[OrderResult], None]] = []

    def on_fill(self, cb: Callable[[OrderResult], None]) -> None:
        self._on_fill_callbacks.append(cb)

    def fire(self, result: OrderResult) -> None:
        for cb in list(self._on_fill_callbacks):
            cb(result)


class _FakeTradeLog:
    """Records every (result, strategy_name) tuple it receives."""

    def __init__(self) -> None:
        self.rows: list[tuple[OrderResult, str]] = []

    def record(self, result: OrderResult, strategy_name: str, strategy_params=None) -> None:
        self.rows.append((result, strategy_name))


class _RecordingStrategy(BaseStrategy):
    """No-op strategy that just records lifecycle calls."""

    def __init__(self, *args, **kwargs) -> None:
        # Pop our test-only field before forwarding to BaseStrategy.
        self._test_label = kwargs.pop("test_label", "")
        super().__init__(*args, **kwargs)
        self.start_called = False
        self.stop_called = False
        self.tick_count = 0
        self._tick_event = threading.Event()
        self.fills_seen: list[OrderResult] = []

    def on_start(self) -> None:
        self.start_called = True

    def on_tick(self) -> None:
        self.tick_count += 1
        self._tick_event.set()

    def on_stop(self) -> None:
        self.stop_called = True

    def on_fill(self, result: OrderResult) -> None:
        # Records every fill the dispatcher forwards. With the strategy_name
        # filter active, this list should only contain matching fills.
        self.fills_seen.append(result)


def _result(symbol: str, strategy_name: str | None, order_id: int = 1) -> OrderResult:
    """Build a minimal FILLED OrderResult with a strategy_name tag."""
    return OrderResult(
        order_id=order_id,
        symbol=symbol,
        action="BUY",
        quantity=10,
        order_type="MKT",
        tif="GTC",
        status=OrderStatus.FILLED,
        filled=10,
        remaining=0,
        avg_fill_price=100.0,
        limit_price=None,
        stop_price=None,
        strategy_name=strategy_name,
    )


def _basic_caps(daily_loss: float = -2_000.0) -> RiskCaps:
    return RiskCaps(
        max_order_value=120_000.0,
        max_position_value=100_000.0,
        max_daily_loss=daily_loss,
    )


def _make_runner(*, configs: list[StrategyConfig]):
    client = MagicMock()
    om = _FakeOrderManager()
    reconnect = MagicMock()
    feed = MagicMock()
    trade_log = _FakeTradeLog()
    runner = StrategyRunner(
        client=client,
        order_manager=om,
        reconnect=reconnect,
        feed=feed,
        trade_log=trade_log,
        registry=configs,
    )
    return runner, om, trade_log


# ── MS-01: registry validation ────────────────────────────────────────────────


def test_ms01_empty_registry_rejected():
    with pytest.raises(ConfigError, match="empty"):
        _make_runner(configs=[])


def test_ms02_duplicate_names_rejected():
    cfg = StrategyConfig(
        name="dup",
        strategy_class=_RecordingStrategy,
        symbol="AAPL",
        params={"test_label": "x"},
        schedule=Interval(seconds=60),
        risk_caps=_basic_caps(),
    )
    with pytest.raises(ConfigError, match="[Dd]uplicate strategy name"):
        _make_runner(configs=[cfg, cfg])


# ── MS-12: shared-symbol guard (MS-D) ─────────────────────────────────────────


def _cfg(name: str, symbol: str) -> StrategyConfig:
    return StrategyConfig(
        name=name,
        strategy_class=_RecordingStrategy,
        symbol=symbol,
        params={"test_label": name},
        schedule=Interval(seconds=60),
        risk_caps=_basic_caps(),
    )


def test_ms12a_shared_symbol_distinct_names_raises_config_error():
    """Two strategies, same symbol, different names → ConfigError (MS-D)."""
    a = _cfg("A", "AAPL")
    b = _cfg("B", "AAPL")
    with pytest.raises(ConfigError, match="both target symbol"):
        _make_runner(configs=[a, b])


def test_ms12b_shared_name_distinct_symbols_still_raises():
    """Regression for unified exception model: shared name → ConfigError, not ValueError."""
    a = _cfg("same", "AAPL")
    b = _cfg("same", "MSFT")
    with pytest.raises(ConfigError, match="[Dd]uplicate strategy name"):
        _make_runner(configs=[a, b])


def test_ms12c_three_entry_collision_between_first_and_third():
    """Sliding-window detection: collision is #1↔#3, not adjacent."""
    a = _cfg("A", "AAPL")
    b = _cfg("B", "MSFT")
    c = _cfg("C", "AAPL")
    with pytest.raises(ConfigError, match="both target symbol"):
        _make_runner(configs=[a, b, c])


def test_ms12d_case_insensitive_symbol_collision():
    """SPY vs spy must be treated as the same symbol."""
    a = _cfg("A", "SPY")
    b = _cfg("B", "spy")
    with pytest.raises(ConfigError, match="both target symbol"):
        _make_runner(configs=[a, b])


def test_ms12e_single_entry_passes():
    """Sanity: a one-entry registry validates and builds without error."""
    runner, _, _ = _make_runner(configs=[_cfg("solo", "AAPL")])
    runner.build()
    assert len(runner.handles) == 1


# ── MS-03: build() wires N strategies with independent RMs ────────────────────


def test_ms03_build_creates_per_strategy_risk_manager():
    cfg_a = StrategyConfig(
        name="A",
        strategy_class=_RecordingStrategy,
        symbol="AAPL",
        params={"test_label": "A"},
        schedule=Interval(seconds=60),
        risk_caps=_basic_caps(daily_loss=-1_000.0),
    )
    cfg_b = StrategyConfig(
        name="B",
        strategy_class=_RecordingStrategy,
        symbol="MSFT",
        params={"test_label": "B"},
        schedule=Interval(seconds=60),
        risk_caps=_basic_caps(daily_loss=-3_000.0),
    )
    runner, _, _ = _make_runner(configs=[cfg_a, cfg_b])
    runner.build()

    assert len(runner.handles) == 2
    rm_a = runner.handles[0].risk_manager
    rm_b = runner.handles[1].risk_manager
    assert rm_a is not rm_b
    assert rm_a.max_daily_loss == -1_000.0
    assert rm_b.max_daily_loss == -3_000.0
    # Each strategy is tagged with its registry name for fill routing.
    assert runner.handles[0].strategy._strategy_name == "A"
    assert runner.handles[1].strategy._strategy_name == "B"


# ── MS-04: fill routing — A's fill bumps only A's TradeLog rows ──────────────


def test_ms04_fill_routes_to_originating_strategy_only():
    cfg_a = StrategyConfig(
        name="A",
        strategy_class=_RecordingStrategy,
        symbol="AAPL",
        params={"test_label": "A"},
        schedule=Interval(seconds=60),
        risk_caps=_basic_caps(),
    )
    cfg_b = StrategyConfig(
        name="B",
        strategy_class=_RecordingStrategy,
        symbol="MSFT",
        params={"test_label": "B"},
        schedule=Interval(seconds=60),
        risk_caps=_basic_caps(),
    )
    runner, om, trade_log = _make_runner(configs=[cfg_a, cfg_b])
    runner.build()

    # Fire two fills, one per strategy.
    om.fire(_result("AAPL", strategy_name="A", order_id=1))
    om.fire(_result("MSFT", strategy_name="B", order_id=2))

    names = [name for _, name in trade_log.rows]
    assert names == ["A", "B"]
    # An untagged fill must NOT be recorded under either strategy.
    om.fire(_result("GOOG", strategy_name=None, order_id=3))
    assert [name for _, name in trade_log.rows] == ["A", "B"]


# ── MS-05: halt isolation — A's losses don't halt B ──────────────────────────


def test_ms05_halt_isolation_between_strategies():
    cfg_a = StrategyConfig(
        name="A",
        strategy_class=_RecordingStrategy,
        symbol="AAPL",
        params={"test_label": "A"},
        schedule=Interval(seconds=60),
        risk_caps=_basic_caps(daily_loss=-100.0),
    )
    cfg_b = StrategyConfig(
        name="B",
        strategy_class=_RecordingStrategy,
        symbol="MSFT",
        params={"test_label": "B"},
        schedule=Interval(seconds=60),
        risk_caps=_basic_caps(daily_loss=-10_000.0),
    )
    runner, _, _ = _make_runner(configs=[cfg_a, cfg_b])
    runner.build()

    # Account-level realized loss of $-500 — exceeds A's cap, well within B's.
    runner.update_daily_pnl_all(-500.0)

    assert runner.handles[0].risk_manager.is_halted() is True
    assert runner.handles[1].risk_manager.is_halted() is False


# ── MS-06: Interval scheduler fires on_tick and stops cleanly ────────────────


def test_ms06_interval_scheduler_fires_and_stops():
    cfg = StrategyConfig(
        name="interval-test",
        strategy_class=_RecordingStrategy,
        symbol="AAPL",
        params={"test_label": "i"},
        schedule=Interval(seconds=1),
        risk_caps=_basic_caps(),
    )
    runner, _, _ = _make_runner(configs=[cfg])
    runner.build()
    handle = runner.handles[0]
    runner.start_all()

    # Wait up to 3s for at least one tick.
    fired = handle.strategy._tick_event.wait(timeout=3.0)
    runner.stop_all()
    handle.thread.join(timeout=2.0)

    assert fired, "Interval scheduler did not fire on_tick within 3s"
    assert handle.strategy.start_called is True
    assert handle.strategy.stop_called is True
    assert handle.thread.is_alive() is False


# ── MS-07: DailyAt scheduler thread starts and stops cleanly without firing ──


def test_ms07_daily_at_scheduler_starts_and_stops_cleanly():
    # Schedule far in the future so on_tick won't fire before we stop.
    cfg = StrategyConfig(
        name="daily-test",
        strategy_class=_RecordingStrategy,
        symbol="AAPL",
        params={"test_label": "d"},
        schedule=DailyAt(hour=23, minute=59),
        risk_caps=_basic_caps(),
    )
    runner, _, _ = _make_runner(configs=[cfg])
    runner.build()
    handle = runner.handles[0]
    runner.start_all()

    # Give the thread a moment to enter the wait, then stop.
    runner.stop_all()
    handle.thread.join(timeout=2.0)

    assert handle.strategy.start_called is True
    assert handle.strategy.stop_called is True
    assert handle.thread.is_alive() is False


# ── MS-08: reset_all_daily resets every strategy's RiskManager ───────────────


def test_ms08_reset_all_daily_resets_every_strategy():
    cfg_a = StrategyConfig(
        name="A",
        strategy_class=_RecordingStrategy,
        symbol="AAPL",
        params={"test_label": "A"},
        schedule=Interval(seconds=60),
        risk_caps=_basic_caps(daily_loss=-100.0),
    )
    cfg_b = StrategyConfig(
        name="B",
        strategy_class=_RecordingStrategy,
        symbol="MSFT",
        params={"test_label": "B"},
        schedule=Interval(seconds=60),
        risk_caps=_basic_caps(daily_loss=-200.0),
    )
    runner, _, _ = _make_runner(configs=[cfg_a, cfg_b])
    runner.build()

    runner.update_daily_pnl_all(-1_000.0)
    assert runner.handles[0].risk_manager.is_halted() is True
    assert runner.handles[1].risk_manager.is_halted() is True

    runner.reset_all_daily()
    assert runner.handles[0].risk_manager.is_halted() is False
    assert runner.handles[1].risk_manager.is_halted() is False


# ── MS-09: on_fill isolation — strategy A's fill must NOT trigger B.on_fill ──


def test_ms09_on_fill_isolation_between_strategies():
    """Regression: BaseStrategy auto-wires on_fill on om — without the
    strategy_name filter every strategy would see every fill, corrupting
    state. MS-D now blocks shared-symbol configs at registry validation;
    isolation is verified via strategy_name filtering on distinct symbols."""
    cfg_a = StrategyConfig(
        name="A",
        strategy_class=_RecordingStrategy,
        symbol="AAPL",
        params={"test_label": "A"},
        schedule=Interval(seconds=60),
        risk_caps=_basic_caps(),
    )
    cfg_b = StrategyConfig(
        name="B",
        strategy_class=_RecordingStrategy,
        symbol="MSFT",
        params={"test_label": "B"},
        schedule=Interval(seconds=60),
        risk_caps=_basic_caps(),
    )
    runner, om, _ = _make_runner(configs=[cfg_a, cfg_b])
    runner.build()

    om.fire(_result("AAPL", strategy_name="A", order_id=1))
    om.fire(_result("MSFT", strategy_name="B", order_id=2))
    om.fire(_result("AAPL", strategy_name=None, order_id=3))  # untagged

    strat_a = runner.handles[0].strategy
    strat_b = runner.handles[1].strategy
    a_fills = [r.order_id for r in strat_a.fills_seen]
    b_fills = [r.order_id for r in strat_b.fills_seen]
    assert a_fills == [1], f"A saw {a_fills}, expected [1]"
    assert b_fills == [2], f"B saw {b_fills}, expected [2]"


# ── MS-10: OrderManager._strategy_name_by_order_id cleanup on terminal events ─


def test_ms10_strategy_name_cleanup_on_fill_and_cancel():
    """Regression: _strategy_name_by_order_id grew unbounded in Phase A v1.
    Verify entries are dropped on terminal status events."""
    from unittest.mock import MagicMock

    from broker.order_manager import OrderManager

    # Bypass __init__ wiring; we don't need a live IB instance for this test.
    om = OrderManager.__new__(OrderManager)
    om._client = MagicMock()
    om._ib = MagicMock()
    om._orders = {}
    om._lock = threading.Lock()
    om._strategy_name_by_order_id = {}
    om._seen_exec_ids = set()
    om._on_fill_callbacks = []
    om._on_cancel_callbacks = []
    om._on_error_callbacks = []

    # Seed two orders, both tagged.
    om._strategy_name_by_order_id[100] = "A"
    om._strategy_name_by_order_id[101] = "B"

    # Build a fake Trade with status="Filled" and drive _handle_order_status.
    trade_filled = MagicMock()
    trade_filled.order.orderId = 100
    trade_filled.order.action = "BUY"
    trade_filled.order.totalQuantity = 10
    trade_filled.order.orderType = "MKT"
    trade_filled.order.tif = "GTC"
    trade_filled.order.lmtPrice = 0
    trade_filled.order.auxPrice = 0
    trade_filled.orderStatus.status = "Filled"
    trade_filled.orderStatus.filled = 10
    trade_filled.orderStatus.remaining = 0
    trade_filled.orderStatus.avgFillPrice = 100.0
    trade_filled.contract.symbol = "QQQ"
    om._handle_order_status(trade_filled)

    # Drive a Cancelled event for the second order.
    trade_cancelled = MagicMock()
    trade_cancelled.order.orderId = 101
    trade_cancelled.order.action = "BUY"
    trade_cancelled.order.totalQuantity = 5
    trade_cancelled.order.orderType = "MKT"
    trade_cancelled.order.tif = "GTC"
    trade_cancelled.order.lmtPrice = 0
    trade_cancelled.order.auxPrice = 0
    trade_cancelled.orderStatus.status = "Cancelled"
    trade_cancelled.orderStatus.filled = 0
    trade_cancelled.orderStatus.remaining = 5
    trade_cancelled.orderStatus.avgFillPrice = 0.0
    trade_cancelled.contract.symbol = "MSFT"
    om._handle_order_status(trade_cancelled)

    assert 100 not in om._strategy_name_by_order_id
    assert 101 not in om._strategy_name_by_order_id
    assert om._strategy_name_by_order_id == {}


# ── MS-11: real REGISTRY smoke test ───────────────────────────────────────────


def test_ms11_real_registry_builds_cleanly():
    """The REGISTRY in config/strategies.py constructs cleanly via StrategyRunner.

    Catches constructor-signature drift between a registered strategy and
    its `StrategyConfig.params` BEFORE VPS deploy. build() does not call
    on_start(), so no yfinance / broker side-effects.
    """
    from config.strategies import REGISTRY

    runner, _, _ = _make_runner(configs=list(REGISTRY))
    runner.build()
    assert len(runner.handles) == len(REGISTRY)
    names = [h.config.name for h in runner.handles]
    assert len(set(names)) == len(names), "REGISTRY has duplicate strategy names"
    # Each strategy got its own independent RiskManager instance.
    rms = [h.risk_manager for h in runner.handles]
    assert len(set(id(rm) for rm in rms)) == len(rms)
