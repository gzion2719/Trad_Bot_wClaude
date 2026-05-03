"""Section 11: Risk manager tests — requires IBKR TWS."""

import os

import pytest

from models.order import OrderAction, OrderRequest, TimeInForce
from risk.risk_manager import RiskManager, RiskViolationError

IS_CI = bool(os.getenv("GITHUB_ACTIONS"))
pytestmark = pytest.mark.skipif(IS_CI, reason="requires IBKR TWS connection")


def _make_rm(c, o, **kwargs):
    defaults = dict(
        max_order_value=1_000.0,
        max_position_value=2_000.0,
        max_daily_loss=-200.0,
        max_open_orders=5,
    )
    defaults.update(kwargs)
    return RiskManager(client=c, order_manager=o, **defaults)


def test_rm01_order_within_limits_passes(live_client):
    c, o = live_client
    rm = _make_rm(c, o)
    r = OrderRequest(symbol="GE", action=OrderAction.BUY, quantity=1, tif=TimeInForce.GTC)
    rm.check(r, current_price=10.0)


def test_rm02_exceeds_max_order_value_raises(live_client):
    c, o = live_client
    rm = _make_rm(c, o, max_order_value=500.0)
    r = OrderRequest(symbol="GE", action=OrderAction.BUY, quantity=100, tif=TimeInForce.GTC)
    with pytest.raises(RiskViolationError):
        rm.check(r, current_price=10.0)


def test_rm03_daily_loss_ceiling_halts_trading(live_client):
    c, o = live_client
    rm = _make_rm(c, o, max_daily_loss=-100.0)
    rm.update_daily_pnl(-150.0)
    assert rm.is_halted() is True
    r = OrderRequest(symbol="GE", action=OrderAction.BUY, quantity=1, tif=TimeInForce.GTC)
    with pytest.raises(RiskViolationError):
        rm.check(r, current_price=10.0)


def test_rm04_reset_daily_clears_halted(live_client):
    c, o = live_client
    rm = _make_rm(c, o, max_daily_loss=-100.0)
    rm.update_daily_pnl(-150.0)
    assert rm.is_halted() is True
    rm.reset_daily()
    assert rm.is_halted() is False


def test_rm05_too_many_open_orders_raises(live_client):
    c, o = live_client
    o._clear_callbacks()
    rm = _make_rm(c, o, max_open_orders=0)
    r = OrderRequest(symbol="GE", action=OrderAction.BUY, quantity=1, tif=TimeInForce.GTC)
    with pytest.raises(RiskViolationError):
        rm.check(r, current_price=10.0)


def test_rm06_negative_max_daily_loss_required(live_client):
    c, o = live_client
    with pytest.raises(ValueError):
        RiskManager(client=c, order_manager=o, max_daily_loss=100.0)


def test_rm07_validate_setup_valid_long(live_client):
    c, o = live_client
    rm = _make_rm(c, o)
    # entry=150, stop=145, target=165 → R/R=3.0, risk/share=$5 < $200 (2% of $10k)
    assert (165.0 - 150.0) / (150.0 - 145.0) == 3.0
    rm.validate_setup(entry_price=150.0, stop_price=145.0, take_profit_price=165.0, equity=10_000.0)


def test_rm08_validate_setup_low_rr_raises(live_client):
    c, o = live_client
    rm = _make_rm(c, o)
    # R/R = (160-150)/(150-145) = 2.0 < 3.0
    assert (160.0 - 150.0) / (150.0 - 145.0) == 2.0
    with pytest.raises(RiskViolationError):
        rm.validate_setup(
            entry_price=150.0, stop_price=145.0, take_profit_price=160.0, equity=10_000.0
        )


def test_rm09_validate_setup_risk_exceeds_2pct_raises(live_client):
    c, o = live_client
    rm = _make_rm(c, o)
    # equity=$100, max_risk=$2, risk/share=$50 → fails Rule B; R/R=3.0 passes
    assert (300.0 - 150.0) / (150.0 - 100.0) == 3.0
    with pytest.raises(RiskViolationError):
        rm.validate_setup(
            entry_price=150.0, stop_price=100.0, take_profit_price=300.0, equity=100.0
        )


def test_rm10_validate_setup_zero_equity_raises(live_client):
    c, o = live_client
    rm = _make_rm(c, o)
    with pytest.raises(ValueError):
        rm.validate_setup(entry_price=150.0, stop_price=145.0, take_profit_price=165.0, equity=0.0)


def test_rm11_validate_setup_stop_equals_entry_raises(live_client):
    c, o = live_client
    rm = _make_rm(c, o)
    with pytest.raises(ValueError):
        rm.validate_setup(
            entry_price=150.0, stop_price=150.0, take_profit_price=165.0, equity=10_000.0
        )


def test_rm12_validate_setup_valid_short(live_client):
    c, o = live_client
    rm = _make_rm(c, o)
    # Short: entry=100, stop=105, target=85 → R/R=3.0
    assert (100.0 - 85.0) / (105.0 - 100.0) == 3.0
    rm.validate_setup(
        entry_price=100.0,
        stop_price=105.0,
        take_profit_price=85.0,
        equity=10_000.0,
        order_action=OrderAction.SELL,
    )


def test_rm13_plan_trade_sizes_correctly(live_client):
    c, o = live_client
    rm = _make_rm(c, o, max_risk_per_trade_pct=0.02)
    # risk_amount=$200, risk/share=$5 → floor(200/5) = 40 shares
    shares = rm.plan_trade(
        entry_price=150.0, stop_price=145.0, take_profit_price=165.0, equity=10_000.0
    )
    assert shares == 40


def test_rm14_plan_trade_bad_setup_raises(live_client):
    c, o = live_client
    rm = _make_rm(c, o)
    # R/R=2.0 < 3.0 → raises before sizing
    with pytest.raises(RiskViolationError):
        rm.plan_trade(entry_price=150.0, stop_price=145.0, take_profit_price=160.0, equity=10_000.0)
