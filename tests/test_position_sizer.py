"""Section 12: Position sizer tests — no IBKR connection needed."""

import math

import pytest

from risk.position_sizer import PositionSizer


def test_ps01_fixed_returns_share_count():
    assert PositionSizer.fixed(10) == 10
    assert PositionSizer.fixed(1) == 1


def test_ps02_fixed_enforces_minimum_one():
    assert PositionSizer.fixed(0) == 1


def test_ps03_percent_of_equity_floor_division():
    # $50,000 × 2% = $1,000 / $150 = 6.66 → 6
    assert PositionSizer.percent_of_equity(equity=50_000, price=150.0, pct=0.02) == 6


def test_ps04_percent_of_equity_minimum_one():
    assert PositionSizer.percent_of_equity(equity=100, price=10_000.0, pct=0.001) == 1


def test_ps05_percent_of_equity_invalid_inputs_raise():
    with pytest.raises(ValueError):
        PositionSizer.percent_of_equity(equity=0, price=100.0, pct=0.05)
    with pytest.raises(ValueError):
        PositionSizer.percent_of_equity(equity=10_000, price=100.0, pct=1.5)


def test_ps06_kelly_positive_ev():
    # kelly_f = 0.6 - 0.4/2.0 = 0.4 → capped at 0.25 → $50,000 × 0.25 / $100 = 125
    assert (
        PositionSizer.kelly(
            win_rate=0.6, win_loss_ratio=2.0, equity=50_000, price=100.0, max_fraction=0.25
        )
        == 125
    )


def test_ps07_kelly_negative_ev_returns_one():
    # kelly_f = 0.3 - 0.7/0.5 = -1.1 (negative) → clamp to 1
    assert PositionSizer.kelly(win_rate=0.3, win_loss_ratio=0.5, equity=50_000, price=100.0) == 1


def test_ps08_risk_based_correct_math():
    equity, entry, stop = 1_000.0, 50.0, 48.0
    # risk_amount = $20, risk/share = $2, floor(20/2) = 10
    assert equity * 0.02 == 20.0
    assert entry - stop == 2.0
    assert math.floor(20.0 / 2.0) == 10
    assert PositionSizer.risk_based(equity=equity, entry_price=entry, stop_price=stop) == 10


def test_ps09_risk_based_minimum_one_share():
    # floor(2/40) = 0 → clamped to 1
    assert PositionSizer.risk_based(equity=100.0, entry_price=50.0, stop_price=10.0) == 1


def test_ps10_risk_based_stop_above_entry_raises():
    with pytest.raises(ValueError):
        PositionSizer.risk_based(equity=10_000, entry_price=50.0, stop_price=55.0)
    with pytest.raises(ValueError):
        PositionSizer.risk_based(equity=10_000, entry_price=50.0, stop_price=50.0)


def test_ps11_risk_based_zero_equity_raises():
    with pytest.raises(ValueError):
        PositionSizer.risk_based(equity=0, entry_price=50.0, stop_price=48.0)
