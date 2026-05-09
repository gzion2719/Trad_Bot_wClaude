"""
MS-A1 tests — cost_basis pipeline (strategies → OrderResult → TradeLog).

A1 is the data-pipeline half of MS-A. It does NOT change PnLPoller, RiskManager,
or halt logic. It guarantees that:

  1. Strategies stamp OrderResult.cost_basis on SELL fills from their own
     internal _entry_price.
  2. TradeLog.record() persists cost_basis and computes realized_pnl from it
     for live fills (previously only backtest fills).
  3. Strategy entry price survives a restart between BUY and SELL via per-
     strategy JSON state files (with broker avg_cost fallback for clean installs).
  4. The om.on_fill callback order contract holds: strategy.on_fill MUST run
     before the trade_log hook so the strategy's cost_basis mutation is visible.

No IBKR connection required.
"""

from __future__ import annotations

import json
from typing import Callable, List, Optional

import pytest

from data.trade_log import TradeLog
from models.order import (
    OrderResult,
    OrderStatus,
    Position,
)
from strategies.rsi2_mr import RSI2MR_SPY
from strategies.sma_crossover import SMACrossover

# ══════════════════════════════════════════════════════════════════════════════
# Shared helpers
# ══════════════════════════════════════════════════════════════════════════════


def _result(
    *,
    action: str,
    symbol: str = "QQQ",
    filled: float = 100.0,
    avg_fill_price: float = 450.0,
    cost_basis: Optional[float] = None,
    order_id: int = 1,
) -> OrderResult:
    return OrderResult(
        order_id=order_id,
        symbol=symbol,
        action=action,
        quantity=filled,
        order_type="MKT",
        tif="GTC",
        status=OrderStatus.FILLED,
        filled=filled,
        remaining=0.0,
        avg_fill_price=avg_fill_price,
        limit_price=None,
        stop_price=None,
        cost_basis=cost_basis,
    )


class _FakeOrderManager:
    """Captures on_fill callbacks; tests trigger fills manually."""

    def __init__(self) -> None:
        self._on_fill_callbacks: List[Callable[[OrderResult], None]] = []
        self._positions: List[Position] = []

    def on_fill(self, cb: Callable[[OrderResult], None]) -> None:
        self._on_fill_callbacks.append(cb)

    def on_error(self, cb) -> None:  # SMA on_start expects this
        pass

    def get_positions(self) -> List[Position]:
        return list(self._positions)

    def get_open_orders(self, symbol=None):
        return []

    def cancel_order(self, order_id: int) -> None:
        pass

    def fire(self, result: OrderResult) -> None:
        for cb in list(self._on_fill_callbacks):
            cb(result)


def _make_sma(tmp_path, **kwargs) -> SMACrossover:
    """Construct SMA Crossover with isolated state file path (no broker)."""
    om = kwargs.pop("om", _FakeOrderManager())
    s = SMACrossover(
        client=None,
        order_manager=om,
        risk_manager=None,
        reconnect=None,
        feed=None,
        symbol="QQQ",
        sma_fast=10,
        sma_slow=30,
        state_file_path=tmp_path / "sma_state.json",
        **kwargs,
    )
    return s


def _make_rsi(tmp_path, **kwargs) -> RSI2MR_SPY:
    om = kwargs.pop("om", _FakeOrderManager())
    s = RSI2MR_SPY(
        client=None,
        order_manager=om,
        risk_manager=None,
        reconnect=None,
        feed=None,
        symbol="SPY",
        state_file_path=tmp_path / "rsi_state.json",
        **kwargs,
    )
    return s


# ══════════════════════════════════════════════════════════════════════════════
# A1.01 — SMA persists entry_price to JSON on BUY fill
# ══════════════════════════════════════════════════════════════════════════════


def test_a1_01_sma_persists_entry_price_on_buy(tmp_path):
    s = _make_sma(tmp_path)
    s.on_fill(_result(action="BUY", filled=100, avg_fill_price=450.0))

    state_path = tmp_path / "sma_state.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text())
    assert state["in_position"] is True
    assert state["entry_price"] == pytest.approx(450.0)
    assert state["position_shares"] == 100


# ══════════════════════════════════════════════════════════════════════════════
# A1.02 — SMA SELL fill carries cost_basis = entry_price
# ══════════════════════════════════════════════════════════════════════════════


def test_a1_02_sma_sell_fill_stamps_cost_basis(tmp_path):
    s = _make_sma(tmp_path)
    s.on_fill(_result(action="BUY", filled=100, avg_fill_price=450.0))
    sell = _result(action="SELL", filled=100, avg_fill_price=460.0)
    s.on_fill(sell)
    assert sell.cost_basis == pytest.approx(450.0)
    # entry_price is cleared post-stamp; the result still carries it.
    assert s._entry_price == 0.0


# ══════════════════════════════════════════════════════════════════════════════
# A1.03 — RSI2MR SELL fill carries cost_basis = entry_price
# ══════════════════════════════════════════════════════════════════════════════


def test_a1_03_rsi2mr_sell_fill_stamps_cost_basis(tmp_path):
    s = _make_rsi(tmp_path)
    # Pre-set context the way on_fill BUY would, then drive the SELL.
    s._stop_price = 380.0
    s._target_price = 480.0
    s.on_fill(_result(action="BUY", symbol="SPY", filled=10, avg_fill_price=400.0))
    sell = _result(action="SELL", symbol="SPY", filled=10, avg_fill_price=410.0)
    s.on_fill(sell)
    assert sell.cost_basis == pytest.approx(400.0)


def test_a1_03b_rsi2mr_full_buy_sell_flow_via_callbacks(tmp_path):
    """End-to-end via om.fire(): BUY then SELL through the actual callback chain.
    Exercises BaseStrategy._dispatch_on_fill, the no-pyramiding assert path is
    cleared by the SELL, and cost_basis lands on the SELL OrderResult."""
    om = _FakeOrderManager()
    s = _make_rsi(tmp_path, om=om)
    # Pre-set bracket prices the way on_tick would; in the real flow on_fill
    # uses these to place stop/target. Tests the on_fill chain only.
    s._stop_price = 380.0
    s._target_price = 480.0

    om.fire(_result(action="BUY", symbol="SPY", filled=10, avg_fill_price=400.0))
    assert s._in_position is True
    assert s._entry_price == pytest.approx(400.0)

    sell = _result(action="SELL", symbol="SPY", filled=10, avg_fill_price=412.5, order_id=2)
    om.fire(sell)
    assert sell.cost_basis == pytest.approx(400.0)
    assert s._in_position is False
    assert s._entry_price == 0.0  # cleared after SELL


# ══════════════════════════════════════════════════════════════════════════════
# A1.04 — TradeLog persists cost_basis for live-style fills
# ══════════════════════════════════════════════════════════════════════════════


def test_a1_04_trade_log_persists_cost_basis(tmp_path):
    log = TradeLog(db_path=tmp_path / "trades.db")
    sell = _result(action="SELL", filled=100, avg_fill_price=460.0, cost_basis=450.0)
    log.record(sell, strategy_name="SMACrossover-QQQ")
    rows = log.get_history(symbol="QQQ")
    assert len(rows) == 1
    assert rows[0]["cost_basis"] == pytest.approx(450.0)


# ══════════════════════════════════════════════════════════════════════════════
# A1.05 — TradeLog computes realized_pnl = (sell_price - cost_basis) * qty
# ══════════════════════════════════════════════════════════════════════════════


def test_a1_05_trade_log_computes_realized_pnl(tmp_path):
    log = TradeLog(db_path=tmp_path / "trades.db")
    sell = _result(action="SELL", filled=100, avg_fill_price=460.0, cost_basis=450.0)
    log.record(sell, strategy_name="SMACrossover-QQQ")
    rows = log.get_history(symbol="QQQ")
    assert rows[0]["realized_pnl"] == pytest.approx(1000.0)  # (460 - 450) * 100


# ══════════════════════════════════════════════════════════════════════════════
# A1.06 — cost_basis None → realized_pnl None, no crash
# ══════════════════════════════════════════════════════════════════════════════


def test_a1_06_no_cost_basis_no_realized_pnl(tmp_path):
    log = TradeLog(db_path=tmp_path / "trades.db")
    sell = _result(action="SELL", filled=100, avg_fill_price=460.0, cost_basis=None)
    log.record(sell, strategy_name="SMACrossover-QQQ")
    rows = log.get_history(symbol="QQQ")
    assert rows[0]["cost_basis"] is None
    assert rows[0]["realized_pnl"] is None


# ══════════════════════════════════════════════════════════════════════════════
# A1.07 — Callback order contract: strategy.on_fill runs before trade_log
# ══════════════════════════════════════════════════════════════════════════════


def test_a1_07_callback_order_strategy_before_trade_log(tmp_path):
    """Verify the wiring contract: TradeLog reads the OrderResult AFTER the
    strategy mutates cost_basis. If someone refactors the registration order
    in strategy_runner.py, this test fails — by design."""
    om = _FakeOrderManager()
    _make_sma(tmp_path, om=om)  # constructor side-effect: registers on_fill
    log = TradeLog(db_path=tmp_path / "trades.db")

    # Mirror StrategyRunner.build()'s registration order. Strategy on_fill is
    # already registered by BaseStrategy.__init__ above. Now register trade_log.
    om.on_fill(lambda r: log.record(r, strategy_name="SMACrossover-QQQ"))

    om.fire(_result(action="BUY", filled=100, avg_fill_price=450.0))
    om.fire(_result(action="SELL", filled=100, avg_fill_price=460.0))

    rows = log.get_history(symbol="QQQ")
    sells = [r for r in rows if r["action"] == "SELL"]
    assert len(sells) == 1
    assert sells[0]["cost_basis"] == pytest.approx(450.0)
    assert sells[0]["realized_pnl"] == pytest.approx(1000.0)


def test_a1_07b_reversed_order_loses_cost_basis(tmp_path):
    """Negative case for the contract: if trade_log runs BEFORE strategy on_fill,
    cost_basis is None when persisted. Documents the invariant by exhibiting
    the failure mode."""
    om = _FakeOrderManager()
    log = TradeLog(db_path=tmp_path / "trades.db")
    # Register trade_log FIRST (wrong order).
    om.on_fill(lambda r: log.record(r, strategy_name="SMACrossover-QQQ"))
    # Then the strategy attaches its own callback (BaseStrategy.__init__).
    _make_sma(tmp_path, om=om)
    om.fire(_result(action="BUY", filled=100, avg_fill_price=450.0))
    om.fire(_result(action="SELL", filled=100, avg_fill_price=460.0))

    rows = log.get_history(symbol="QQQ")
    sells = [r for r in rows if r["action"] == "SELL"]
    assert len(sells) == 1
    # Strategy ran AFTER trade_log → cost_basis was None when persisted.
    assert sells[0]["cost_basis"] is None
    assert sells[0]["realized_pnl"] is None


# ══════════════════════════════════════════════════════════════════════════════
# A1.08 — reconcile_fills path also routes cost_basis correctly
# ══════════════════════════════════════════════════════════════════════════════


def test_a1_08_reconcile_fills_carries_cost_basis(tmp_path):
    """A missed-fill replay (om.reconcile_fills → om._on_fill_callbacks) must
    invoke the same callback chain. The strategy's on_fill mutates cost_basis
    on the SELL OrderResult exactly as in the live path."""
    om = _FakeOrderManager()
    _make_sma(tmp_path, om=om)
    log = TradeLog(db_path=tmp_path / "trades.db")
    om.on_fill(lambda r: log.record(r, strategy_name="SMACrossover-QQQ"))

    # Drive a BUY then a missed SELL replay.
    om.fire(_result(action="BUY", filled=100, avg_fill_price=450.0))
    # Simulate reconcile_fills firing a fresh OrderResult for the missed SELL.
    replay = _result(action="SELL", filled=100, avg_fill_price=455.0, order_id=2)
    om.fire(replay)

    rows = log.get_history(symbol="QQQ")
    sells = [r for r in rows if r["action"] == "SELL"]
    assert len(sells) == 1
    assert sells[0]["cost_basis"] == pytest.approx(450.0)
    assert sells[0]["realized_pnl"] == pytest.approx(500.0)


# ══════════════════════════════════════════════════════════════════════════════
# A1.09 — cost_basis = 0.0 is not treated as missing (truthy trap)
# ══════════════════════════════════════════════════════════════════════════════


def test_a1_09_cost_basis_zero_not_missing(tmp_path):
    """Guards `cost_basis is not None` (correct) vs `if cost_basis` (wrong) at
    data/trade_log.py — a 0.0 cost basis must still produce a realized_pnl row."""
    log = TradeLog(db_path=tmp_path / "trades.db")
    sell = _result(action="SELL", filled=100, avg_fill_price=10.0, cost_basis=0.0)
    log.record(sell, strategy_name="X")
    rows = log.get_history(symbol="QQQ")
    assert rows[0]["cost_basis"] == pytest.approx(0.0)
    assert rows[0]["realized_pnl"] == pytest.approx(1000.0)


# ══════════════════════════════════════════════════════════════════════════════
# A1.10 — SMA carry-over: state file primary, broker avg_cost fallback
# ══════════════════════════════════════════════════════════════════════════════


def test_a1_10_sma_carryover_state_file_primary(tmp_path):
    """If a state file exists with in_position=True, on_start() prefers it
    over broker avg_cost — the strategy is the authority for its own price."""
    state_path = tmp_path / "sma_state.json"
    state_path.write_text(
        json.dumps(
            {
                "in_position": True,
                "entry_price": 451.5,
                "position_shares": 100,
                "stop_price": 440.0,
            }
        )
    )

    om = _FakeOrderManager()
    om._positions = [
        Position(
            symbol="QQQ",
            quantity=100.0,
            avg_cost=999.0,  # different from state — proves state wins
            market_price=None,
            market_value=None,
            unrealized_pnl=None,
            realized_pnl=None,
            account="DUE",
        )
    ]
    s = _make_sma(tmp_path, om=om)
    s.on_start()
    assert s._entry_price == pytest.approx(451.5)


def test_a1_10b_sma_carryover_broker_fallback(tmp_path):
    """No state file → fall back to broker avg_cost. Covers the day-one deploy
    case where a position exists but A1's state file does not yet."""
    om = _FakeOrderManager()
    om._positions = [
        Position(
            symbol="QQQ",
            quantity=100.0,
            avg_cost=448.25,
            market_price=None,
            market_value=None,
            unrealized_pnl=None,
            realized_pnl=None,
            account="DUE",
        )
    ]
    s = _make_sma(tmp_path, om=om)
    s.on_start()
    assert s._entry_price == pytest.approx(448.25)


# ══════════════════════════════════════════════════════════════════════════════
# A1.11 — No-pyramiding assert fires when BUY arrives while in_position
# ══════════════════════════════════════════════════════════════════════════════


def test_a1_11_sma_no_pyramiding_assert(tmp_path):
    s = _make_sma(tmp_path)
    s.on_fill(_result(action="BUY", filled=100, avg_fill_price=450.0))
    with pytest.raises(AssertionError, match="pyramiding"):
        s.on_fill(_result(action="BUY", filled=50, avg_fill_price=460.0, order_id=2))


def test_a1_11b_rsi2mr_no_pyramiding_assert(tmp_path):
    s = _make_rsi(tmp_path)
    s._stop_price = 380.0
    s._target_price = 480.0
    s.on_fill(_result(action="BUY", symbol="SPY", filled=10, avg_fill_price=400.0))
    with pytest.raises(AssertionError, match="pyramiding"):
        s.on_fill(_result(action="BUY", symbol="SPY", filled=5, avg_fill_price=410.0, order_id=2))
