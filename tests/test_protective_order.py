"""F-BR-01a — RiskManager.check_protective + BaseStrategy.safe_place_protective_order.

Locks the safety invariant that bracket-leg orders (STP, LMT, STP LMT) cannot
bypass the halt + value cap, while still allowing a halted strategy to set a
protective stop on an already-open position (the carve-out caught by pre-impl
CR: blocking reduce-only legs would leave naked positions on halt — worse than
the bug being fixed).
"""

from __future__ import annotations

import ast
import math
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from broker.order_manager import OrderManager
from models.order import (
    OrderAction,
    OrderRequest,
    OrderType,
    Position,
    TimeInForce,
)
from risk.risk_manager import RiskManager, RiskViolationError

# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


def _make_rm(
    *,
    max_order_value: float = 100_000.0,
    max_daily_loss: float = -1_000.0,
    positions: list[Position] | None = None,
    get_positions_raises: bool = False,
) -> RiskManager:
    """RiskManager with a MagicMock OrderManager that returns the given positions."""
    om = MagicMock(spec=OrderManager)
    if get_positions_raises:
        om.get_positions.side_effect = RuntimeError("simulated broker read error")
    else:
        om.get_positions.return_value = positions or []
    om.get_open_orders.return_value = []
    client = MagicMock()
    return RiskManager(
        client=client,
        order_manager=om,
        max_order_value=max_order_value,
        max_position_value=500_000.0,
        max_daily_loss=max_daily_loss,
        max_open_orders=50,
    )


def _long_position(symbol: str = "AAPL", qty: float = 100.0) -> Position:
    return Position(
        symbol=symbol,
        quantity=qty,
        avg_cost=150.0,
        market_price=155.0,
        market_value=qty * 155.0,
        unrealized_pnl=qty * 5.0,
        realized_pnl=0.0,
        account="DU000000",
    )


def _short_position(symbol: str = "AAPL", qty: float = 100.0) -> Position:
    return Position(
        symbol=symbol,
        quantity=-qty,
        avg_cost=150.0,
        market_price=145.0,
        market_value=-qty * 145.0,
        unrealized_pnl=qty * 5.0,
        realized_pnl=0.0,
        account="DU000000",
    )


def _stop_req(symbol: str = "AAPL", qty: int = 100, stop: float = 145.0) -> OrderRequest:
    return OrderRequest(
        symbol=symbol,
        action=OrderAction.SELL,
        quantity=qty,
        order_type=OrderType.STOP,
        stop_price=stop,
        tif=TimeInForce.GTC,
    )


# ──────────────────────────────────────────────────────────────────────────────
# test_br_01 — invalid effective_price (NaN is the load-bearing case)
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), 0.0, -1.0])
def test_br_01_protective_invalid_price_rejected(bad: float):
    rm = _make_rm()
    req = _stop_req()
    with pytest.raises(RiskViolationError, match="invalid effective_price"):
        rm.check_protective(req, bad)


# ──────────────────────────────────────────────────────────────────────────────
# test_br_02 — order value cap enforced even on protective legs
# ──────────────────────────────────────────────────────────────────────────────


def test_br_02_protective_value_cap_rejected():
    rm = _make_rm(max_order_value=10_000.0, positions=[_long_position(qty=100)])
    # 100 × 145.0 = $14,500 > $10,000 cap
    req = _stop_req(qty=100, stop=145.0)
    with pytest.raises(RiskViolationError, match="exceeds max_order_value"):
        rm.check_protective(req, 145.0)


# ──────────────────────────────────────────────────────────────────────────────
# test_br_03 — reduce-only protective passes under halt (the safety invariant)
# ──────────────────────────────────────────────────────────────────────────────


def test_br_03_reduce_only_protective_passes_under_halt():
    rm = _make_rm(positions=[_long_position(qty=100)])
    rm._daily_realized_pnl = -2_000.0  # below cap
    rm._halted_today = True
    req = _stop_req(qty=100, stop=145.0)
    # Must NOT raise — closing a long with SELL is reduce-only.
    rm.check_protective(req, 145.0)


def test_br_03b_buy_to_cover_short_passes_under_halt():
    rm = _make_rm(positions=[_short_position(qty=100)])
    rm._halted_today = True
    req = OrderRequest(
        symbol="AAPL",
        action=OrderAction.BUY,
        quantity=100,
        order_type=OrderType.STOP,
        stop_price=160.0,
        tif=TimeInForce.GTC,
    )
    rm.check_protective(req, 160.0)


# ──────────────────────────────────────────────────────────────────────────────
# test_br_04 — add-risk protective IS blocked when halted
# ──────────────────────────────────────────────────────────────────────────────


def test_br_04_add_risk_protective_blocked_under_halt():
    """SELL protective with NO matching position is treated as reduce-only
    (on_fill race window — fail-OPEN). To trigger the halt-block path the
    order must affirmatively ADD risk: same-sign as an existing position."""
    rm = _make_rm(positions=[_long_position(qty=100)])
    rm._halted_today = True
    # SELL on a long is reduce-only and bypasses halt. A same-sign add (BUY
    # adding to a long) should be blocked.
    add_req = OrderRequest(
        symbol="AAPL",
        action=OrderAction.BUY,
        quantity=50,
        order_type=OrderType.STOP,
        stop_price=160.0,
        tif=TimeInForce.GTC,
    )
    with pytest.raises(RiskViolationError, match="halt active"):
        rm.check_protective(add_req, 160.0)


# ──────────────────────────────────────────────────────────────────────────────
# test_br_05 — STOP_LIMIT uses stop_price as effective_price (CR B2)
# ──────────────────────────────────────────────────────────────────────────────


def test_br_05_strategy_helper_stop_limit_uses_stop_price():
    from strategies.base_strategy import BaseStrategy

    class _Probe(BaseStrategy):
        def on_start(self):
            pass

        def on_tick(self):
            pass

        def on_stop(self):
            pass

    captured = {}

    class _RM:
        def check_protective(self, req, effective_price):
            captured["effective_price"] = effective_price
            captured["order_type"] = req.order_type

    om = MagicMock()
    placed = MagicMock()
    placed.order_id = 999
    om.place_order.return_value = placed
    strat = _Probe(client=MagicMock(), order_manager=om, risk_manager=_RM())

    req = OrderRequest(
        symbol="AAPL",
        action=OrderAction.SELL,
        quantity=10,
        order_type=OrderType.STOP_LIMIT,
        stop_price=145.0,
        limit_price=144.0,
        tif=TimeInForce.GTC,
    )
    strat.safe_place_protective_order(req)
    assert captured["effective_price"] == 145.0  # stop_price (trigger), NOT limit_price
    assert captured["order_type"] == OrderType.STOP_LIMIT


# ──────────────────────────────────────────────────────────────────────────────
# test_br_06 — get_positions failure → fail-OPEN under halt
# ──────────────────────────────────────────────────────────────────────────────


def test_br_06_protective_fails_open_when_get_positions_raises():
    """A broker read failure during a halt must NOT block a protective stop —
    that would leave the open position naked."""
    rm = _make_rm(get_positions_raises=True)
    rm._halted_today = True
    req = _stop_req()
    # Must NOT raise — fail-OPEN protects the position.
    rm.check_protective(req, 145.0)


# ──────────────────────────────────────────────────────────────────────────────
# test_br_07 — strategy helper stamps strategy_name
# ──────────────────────────────────────────────────────────────────────────────


def test_br_07_helper_stamps_strategy_name():
    from strategies.base_strategy import BaseStrategy

    class _Probe(BaseStrategy):
        def on_start(self):
            pass

        def on_tick(self):
            pass

        def on_stop(self):
            pass

    om = MagicMock()
    placed = MagicMock()
    placed.order_id = 42
    om.place_order.return_value = placed
    strat = _Probe(client=MagicMock(), order_manager=om, risk_manager=None)
    strat._strategy_name = "TestStrat"

    req = _stop_req()
    assert req.strategy_name is None
    strat.safe_place_protective_order(req)
    assert req.strategy_name == "TestStrat"


# ──────────────────────────────────────────────────────────────────────────────
# test_br_08 — AST grep tripwire (regression shield)
# ──────────────────────────────────────────────────────────────────────────────


def test_br_08_grep_tripwire_no_direct_place_order_in_strategies():
    """No file under strategies/ may call self.om.place_order directly.

    Use safe_place_order (entry orders) or safe_place_protective_order
    (bracket legs). The grep is AST-based so docstrings and comments mentioning
    'self.om.place_order' don't trip it (M2/M3 from pre-impl CR).

    Allowlist: only `strategies/base_strategy.py` may contain the literal call,
    inside the two helpers. New files under `strategies/` enter the deny-list
    automatically (fail-closed on additions).
    """
    project_root = Path(__file__).resolve().parent.parent
    strategies_dir = project_root / "strategies"
    allowlist = {strategies_dir / "base_strategy.py"}
    offenders: list[str] = []
    for path in sorted(strategies_dir.glob("*.py")):
        if path in allowlist or path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr != "place_order":
                continue
            receiver = func.value
            if (
                isinstance(receiver, ast.Attribute)
                and receiver.attr == "om"
                and isinstance(receiver.value, ast.Name)
                and receiver.value.id == "self"
            ):
                offenders.append(
                    f"{path.relative_to(project_root)}:{node.lineno}: self.om.place_order(...)"
                )

    assert not offenders, (
        "Strategies must not call self.om.place_order directly. Use "
        "self.safe_place_order(...) for entries or "
        "self.safe_place_protective_order(...) for bracket legs (F-BR-01a). "
        "Offenders:\n  " + "\n  ".join(offenders)
    )


# ──────────────────────────────────────────────────────────────────────────────
# test_br_09 — math.isfinite catches NaN that OrderRequest.__post_init__ misses
# ──────────────────────────────────────────────────────────────────────────────


def test_br_09_nan_effective_price_documented_load_bearing_check():
    """OrderRequest.__post_init__ rejects stop_price <= 0 but `nan <= 0` is
    False, so NaN slips past the model layer. check_protective's
    math.isfinite() is the load-bearing rejection. Locks the M4 finding."""
    rm = _make_rm()
    req = _stop_req()
    assert math.isnan(float("nan"))
    # Direct call with NaN — OrderRequest construction was already validated.
    with pytest.raises(RiskViolationError, match="invalid effective_price"):
        rm.check_protective(req, float("nan"))
