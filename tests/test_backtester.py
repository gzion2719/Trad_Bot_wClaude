"""Section 17: Backtester tests — no IBKR connection needed."""

import math

import pandas as pd
import pytest

from backtester.engine import BacktestEngine, MockOrderManager
from backtester.metrics import max_drawdown, sharpe_ratio
from backtester.portfolio import BacktestPortfolio
from models.order import OrderAction, OrderRequest, OrderStatus, TimeInForce
from strategies.base_strategy import BaseStrategy


class _BuyHoldStrategy(BaseStrategy):
    """Minimal strategy: buys on first bar, sells on last bar."""

    def __init__(
        self, client, order_manager, risk_manager=None, reconnect=None, feed=None, symbol="TEST"
    ):
        super().__init__(client, order_manager, risk_manager, reconnect, feed=feed, symbol=symbol)
        self._bought = False
        self._bar_count = 0
        self._total_bars = 0

    def on_start(self):
        pass

    def on_tick(self):
        self._bar_count += 1
        if not self._bought:
            r = OrderRequest(
                symbol=self.symbol, action=OrderAction.BUY, quantity=10, tif=TimeInForce.GTC
            )
            self.om.place_order(r)
            self._bought = True
        elif self._bar_count >= self._total_bars:
            r = OrderRequest(
                symbol=self.symbol, action=OrderAction.SELL, quantity=10, tif=TimeInForce.GTC
            )
            self.om.place_order(r)

    def on_stop(self):
        pass


def _make_df(prices):
    dates = pd.date_range("2024-01-01", periods=len(prices), freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "open": prices,
            "high": [p * 1.01 for p in prices],
            "low": [p * 0.99 for p in prices],
            "close": prices,
            "volume": [100_000] * len(prices),
        },
        index=dates,
    )


def test_bt01_engine_runs_on_simple_data():
    df = _make_df([100, 102, 105, 103, 108, 110, 107, 112])
    engine = BacktestEngine(
        strategy_class=_BuyHoldStrategy,
        data=df,
        symbol="TEST",
        initial_capital=10_000,
    )
    result = engine.run()
    assert result is not None
    assert len(result.equity_curve) == 8


def test_bt02_mock_order_manager_returns_valid_result():
    portfolio = BacktestPortfolio(initial_capital=10_000)
    mock_om = MockOrderManager(portfolio)
    r = OrderRequest(symbol="TEST", action=OrderAction.BUY, quantity=5, tif=TimeInForce.GTC)
    result = mock_om.place_order(r)
    assert result.order_id > 0
    assert result.status == OrderStatus.SUBMITTED


def test_bt03_portfolio_fill_reduces_cash():
    p = BacktestPortfolio(initial_capital=10_000, commission=0)
    p.fill("TEST", OrderAction.BUY, quantity=10, price=100.0, order_id=1)
    assert abs(p.cash - 9_000.0) < 0.01


def test_bt04_portfolio_sell_increases_cash():
    p = BacktestPortfolio(initial_capital=10_000, commission=0)
    p.fill("TEST", OrderAction.BUY, quantity=10, price=100.0, order_id=1)
    p.fill("TEST", OrderAction.SELL, quantity=10, price=110.0, order_id=2)
    assert abs(p.cash - 10_100.0) < 0.01


def test_bt05_sharpe_ratio_returns_float():
    curve = pd.Series([100_000, 101_000, 100_500, 102_000, 103_000])
    sr = sharpe_ratio(curve)
    assert not math.isnan(sr)


def test_bt06_max_drawdown_is_negative_fraction():
    curve = pd.Series([100_000, 110_000, 95_000, 105_000])
    dd = max_drawdown(curve)
    assert dd < 0
    assert dd > -1.0


def test_bt07_engine_raises_on_empty_dataframe():
    with pytest.raises(ValueError):
        BacktestEngine(
            strategy_class=_BuyHoldStrategy,
            data=pd.DataFrame(),
            symbol="TEST",
        )


def test_bt08_portfolio_skips_sell_with_no_position():
    p = BacktestPortfolio(initial_capital=10_000, commission=0)
    result = p.fill("TEST", OrderAction.SELL, quantity=5, price=100.0, order_id=1)
    assert result.status == OrderStatus.INACTIVE
    assert abs(p.cash - 10_000.0) < 0.01
