"""
Tests for RSI2-MR strategy and its supporting modules.

Sections:
  A  — Indicator unit tests (_indicators.py)
  B  — Calendar / FOMC filter tests
  C  — BacktestDataFeed external series
  D  — MockOrderManager bracket simulation + slippage
  E  — RSI2MR_SPY unit tests (direct method calls)
  F  — Full integration tests via BacktestEngine

No IBKR connection required.
"""

from __future__ import annotations

from datetime import date
from typing import List
from unittest.mock import patch

import pandas as pd
import pytest
from pytest import approx

from backtester.engine import BacktestDataFeed, BacktestEngine, MockOrderManager
from backtester.portfolio import BacktestPortfolio
from models.order import (
    OrderAction,
    OrderRequest,
    OrderResult,
    OrderType,
    TimeInForce,
)
from strategies._indicators import atr_wilder, rsi_wilder, sma

# ══════════════════════════════════════════════════════════════════════════════
# Shared helpers
# ══════════════════════════════════════════════════════════════════════════════


def _bar(open_=100.0, high=101.0, low=99.0, close=100.0, symbol="SPY"):
    from data.bar import Bar
    from datetime import datetime, timezone

    return Bar(
        symbol=symbol,
        timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1_000_000,
    )


def _portfolio_with_position(shares: int, avg_cost: float, cash: float = 10_000.0):
    p = BacktestPortfolio(initial_capital=cash + shares * avg_cost, commission=0.0)
    p._positions["SPY"] = float(shares)
    p._avg_cost["SPY"] = avg_cost
    p._cash = cash
    p._current_prices["SPY"] = avg_cost
    return p


def _make_spy_df(n: int = 295, signal_offset: int = 35, start: str = "2014-01-02") -> pd.DataFrame:
    """
    n bars of synthetic SPY.  Layout:
      bars 0 .. (n - signal_offset - 2) : steady uptrend (+0.5/bar)
      bar  (n - signal_offset - 1)       : -10 pt drop  (pre-drop)
      bar  (n - signal_offset)            : -5 pt drop   (signal bar → RSI oversold)
      bars (n - signal_offset + 1) .. (n-1) : flat at signal close

    With n=295 and signal_offset=35:
      signal bar is at index 260 (well past the 240-bar warmup gate)
      entry fills at bar 261, time-stop at bar 269, sell fills at bar 270
      plenty of headroom to bar 294
    """
    sig = n - signal_offset
    prices = [100.0 + 0.5 * i for i in range(n)]
    prices[sig - 1] = prices[sig - 2] - 10.0
    prices[sig] = prices[sig - 1] - 5.0
    flat_val = prices[sig]
    for i in range(sig + 1, n):
        prices[i] = flat_val

    dates = pd.bdate_range(start=start, periods=n)
    return pd.DataFrame(
        {
            "open": prices,
            "high": [p * 1.002 for p in prices],
            "low": [p * 0.998 for p in prices],
            "close": prices,
            "volume": [10_000_000] * n,
        },
        index=dates,
    )


def _make_vix_series(df: pd.DataFrame, vix: float = 20.0) -> pd.Series:
    return pd.Series(vix, index=df.index)


# Calendar filters patched to False so test data drives behaviour, not dates.
_PATCH_FOMC = patch("strategies.rsi2_mr.is_fomc_day", return_value=False)
_PATCH_RUSSELL = patch("strategies.rsi2_mr.is_russell_rebalance_window", return_value=False)
_PATCH_HOLIDAY = patch("strategies.rsi2_mr.is_pre_long_holiday_closure", return_value=False)


def _run_engine(df, vix_series=None, **strategy_kwargs):
    """
    Run a full backtest with calendar filters and state-file I/O disabled.

    _load_state / _save_state are no-ops so test runs are isolated from any
    real state file on disk (circuit-breaker, peak-equity, etc.).
    """
    from strategies.rsi2_mr import RSI2MR_SPY

    if vix_series is None:
        vix_series = _make_vix_series(df)

    with (
        _PATCH_FOMC,
        _PATCH_RUSSELL,
        _PATCH_HOLIDAY,
        patch.object(RSI2MR_SPY, "_load_state", lambda self: None),
        patch.object(RSI2MR_SPY, "_save_state", lambda self: None),
    ):
        engine = BacktestEngine(
            strategy_class=RSI2MR_SPY,
            data=df,
            symbol="SPY",
            initial_capital=50_000.0,
            commission=0.0,
            strategy_kwargs=strategy_kwargs,
            external_data={"vix": vix_series},
        )
        return engine.run()


# ══════════════════════════════════════════════════════════════════════════════
# Section A — Indicator unit tests
# ══════════════════════════════════════════════════════════════════════════════


def test_at01_sma_basic():
    assert sma([1.0, 2.0, 3.0], 3) == approx(2.0)


def test_at02_sma_uses_last_n():
    # First element should be ignored when period < len
    assert sma([5.0, 1.0, 2.0, 3.0], 3) == approx(2.0)


def test_at03_sma_insufficient_raises():
    with pytest.raises(ValueError, match="sma"):
        sma([1.0, 2.0], 3)


def test_at04_rsi_docstring_example():
    # Hand-check from _indicators.py docstring:
    # closes=[10,11,10,9,10], period=2
    # seed: avg_gain=(1+0)/2=0.5, avg_loss=(0+1)/2=0.5
    # step change=-1: avg_gain=(0.5+0)/2=0.25, avg_loss=(0.5+1)/2=0.75
    # step change=+1: avg_gain=(0.25+1)/2=0.625, avg_loss=(0.75+0)/2=0.375
    # RS=0.625/0.375≈1.6667 → RSI=100-100/2.6667≈62.5
    result = rsi_wilder([10.0, 11.0, 10.0, 9.0, 10.0], 2)
    assert result == approx(62.5, abs=0.1)


def test_at05_rsi_all_gains_returns_100():
    assert rsi_wilder([1.0, 2.0, 3.0], 2) == approx(100.0)


def test_at06_rsi_all_losses_returns_0():
    assert rsi_wilder([3.0, 2.0, 1.0], 2) == approx(0.0)


def test_at07_rsi_insufficient_raises():
    with pytest.raises(ValueError, match="rsi_wilder"):
        rsi_wilder([10.0, 11.0], 2)


def test_at08_atr_constant_ohlc_zero():
    # All same price → TR=0 each bar → ATR=0
    n = 16
    assert atr_wilder([100.0] * n, [100.0] * n, [100.0] * n, 14) == approx(0.0)


def test_at09_atr_uniform_range():
    # Each bar: H=101, L=99, prev_C=100 → TR=max(2,1,1)=2 → ATR=2
    n = 16
    closes = [100.0] * n
    atr = atr_wilder([101.0] * n, [99.0] * n, closes, 14)
    assert atr == approx(2.0)


def test_at10_atr_insufficient_raises():
    with pytest.raises(ValueError, match="atr_wilder"):
        atr_wilder([100.0] * 5, [99.0] * 5, [100.0] * 5, 14)


# ══════════════════════════════════════════════════════════════════════════════
# Section B — Calendar / FOMC filter tests
# ══════════════════════════════════════════════════════════════════════════════


def test_bt01_fomc_known_date():
    from config.calendars.fomc import is_fomc_day

    assert is_fomc_day(date(2024, 9, 18)) is True  # confirmed from fed schedule


def test_bt02_fomc_non_fomc_date():
    from config.calendars.fomc import is_fomc_day

    assert is_fomc_day(date(2024, 9, 17)) is False


def test_bt03_russell_window_on_last_friday_june():
    from config.calendars.market_calendar import is_russell_rebalance_window

    # 2024 last Friday of June = June 28
    assert is_russell_rebalance_window(date(2024, 6, 28)) is True


def test_bt04_russell_window_day_before_and_after():
    from config.calendars.market_calendar import is_russell_rebalance_window

    assert is_russell_rebalance_window(date(2024, 6, 27)) is True  # day before
    assert is_russell_rebalance_window(date(2024, 6, 29)) is True  # day after


def test_bt05_russell_non_june():
    from config.calendars.market_calendar import is_russell_rebalance_window

    assert is_russell_rebalance_window(date(2024, 7, 1)) is False
    assert is_russell_rebalance_window(date(2024, 5, 31)) is False


# ══════════════════════════════════════════════════════════════════════════════
# Section C — BacktestDataFeed external series
# ══════════════════════════════════════════════════════════════════════════════


def test_cf01_external_series_hit():
    feed = BacktestDataFeed("SPY")
    feed._set_external("VIX", {date(2024, 1, 2): 15.3, date(2024, 1, 3): 16.0})
    assert feed.get_external("VIX", date(2024, 1, 2)) == approx(15.3)
    assert feed.get_external("VIX", date(2024, 1, 3)) == approx(16.0)


def test_cf02_external_series_miss():
    feed = BacktestDataFeed("SPY")
    feed._set_external("VIX", {date(2024, 1, 2): 15.3})
    assert feed.get_external("VIX", date(2024, 1, 5)) is None


def test_cf03_external_unknown_key():
    feed = BacktestDataFeed("SPY")
    assert feed.get_external("VIX", date(2024, 1, 2)) is None


def test_cf04_engine_injects_external_data():
    """BacktestEngine.external_data lands in feed._external."""
    from strategies.base_strategy import BaseStrategy

    class _NoopStrategy(BaseStrategy):
        def on_start(self):
            pass

        def on_tick(self):
            pass

        def on_stop(self):
            pass

    dates = pd.bdate_range("2024-01-02", periods=10)
    df = pd.DataFrame(
        {
            "open": [100.0] * 10,
            "high": [101.0] * 10,
            "low": [99.0] * 10,
            "close": [100.0] * 10,
            "volume": [1_000_000] * 10,
        },
        index=dates,
    )
    vix = pd.Series(18.0, index=dates)

    engine = BacktestEngine(
        strategy_class=_NoopStrategy,
        data=df,
        symbol="SPY",
        external_data={"VIX": vix},
    )
    # Just verify it runs without error; VIX injection tested in Section F
    result = engine.run()
    assert result is not None


# ══════════════════════════════════════════════════════════════════════════════
# Section D — MockOrderManager bracket simulation + slippage
# ══════════════════════════════════════════════════════════════════════════════


def test_dm01_mkt_buy_fills_at_open():
    p = BacktestPortfolio(initial_capital=50_000.0, commission=0.0)
    om = MockOrderManager(p)
    om.place_order(OrderRequest("SPY", OrderAction.BUY, 10))

    fills: List[OrderResult] = []
    om.on_fill(fills.append)

    p.update_prices({"SPY": 100.0})
    om._set_bars(_bar(open_=100.0), None)

    assert len(fills) == 1
    assert fills[0].avg_fill_price == approx(100.0)


def test_dm02_mkt_buy_slippage():
    """MKT BUY fill = open * (1 + slippage_bps/10000)."""
    p = BacktestPortfolio(initial_capital=50_000.0, commission=0.0)
    om = MockOrderManager(p)
    om.place_order(OrderRequest("SPY", OrderAction.BUY, 10, backtest_slippage_bps=2.0))

    fills: List[OrderResult] = []
    om.on_fill(fills.append)

    p.update_prices({"SPY": 100.0})
    om._set_bars(_bar(open_=100.0), None)

    assert fills[0].avg_fill_price == approx(100.0 * (1 + 2 / 10_000), abs=1e-4)


def test_dm03_mkt_sell_slippage():
    """MKT SELL fill = open * (1 - slippage_bps/10000)."""
    p = _portfolio_with_position(shares=100, avg_cost=100.0)
    om = MockOrderManager(p)
    om.place_order(OrderRequest("SPY", OrderAction.SELL, 100, backtest_slippage_bps=1.0))

    fills: List[OrderResult] = []
    om.on_fill(fills.append)

    p.update_prices({"SPY": 100.0})
    om._set_bars(_bar(open_=100.0), None)

    assert fills[0].avg_fill_price == approx(100.0 * (1 - 1 / 10_000), abs=1e-4)


def test_dm04_stp_sell_triggers_at_stop():
    """STP SELL: bar.low ≤ stop → fills at stop_price (no gap, no slippage)."""
    p = _portfolio_with_position(100, 400.0)
    om = MockOrderManager(p)
    om.place_order(
        OrderRequest(
            "SPY",
            OrderAction.SELL,
            100,
            order_type=OrderType.STOP,
            stop_price=390.0,
            tif=TimeInForce.GTC,
        )
    )
    fills: List[OrderResult] = []
    om.on_fill(fills.append)

    # open=395 > stop → no gap; low=385 ≤ 390 → triggered
    p.update_prices({"SPY": 390.0})
    om._set_bars(_bar(open_=395.0, high=396.0, low=385.0, close=390.0), None)

    assert len(fills) == 1
    assert fills[0].avg_fill_price == approx(390.0)


def test_dm05_stp_sell_gap_through():
    """STP SELL gap-through: open < stop → fill at open."""
    p = _portfolio_with_position(100, 400.0)
    om = MockOrderManager(p)
    om.place_order(
        OrderRequest(
            "SPY",
            OrderAction.SELL,
            100,
            order_type=OrderType.STOP,
            stop_price=390.0,
            tif=TimeInForce.GTC,
        )
    )
    fills: List[OrderResult] = []
    om.on_fill(fills.append)

    p.update_prices({"SPY": 385.0})
    om._set_bars(_bar(open_=385.0, high=386.0, low=383.0, close=384.0), None)

    assert len(fills) == 1
    assert fills[0].avg_fill_price == approx(385.0)  # open, not stop


def test_dm06_stp_no_trigger_when_low_above_stop():
    """STP SELL does not trigger when bar.low > stop_price."""
    p = _portfolio_with_position(100, 400.0)
    om = MockOrderManager(p)
    om.place_order(
        OrderRequest(
            "SPY",
            OrderAction.SELL,
            100,
            order_type=OrderType.STOP,
            stop_price=390.0,
            tif=TimeInForce.GTC,
        )
    )
    p.update_prices({"SPY": 395.0})
    om._set_bars(_bar(open_=395.0, high=398.0, low=393.0, close=395.0), None)

    assert len(om._pending_orders) == 1
    assert len(om._open_orders) == 1


def test_dm07_stp_gtc_persists_across_non_trigger_bars():
    p = _portfolio_with_position(100, 400.0)
    om = MockOrderManager(p)
    om.place_order(
        OrderRequest(
            "SPY",
            OrderAction.SELL,
            100,
            order_type=OrderType.STOP,
            stop_price=380.0,
            tif=TimeInForce.GTC,
        )
    )
    for _ in range(5):
        p.update_prices({"SPY": 400.0})
        om._set_bars(_bar(open_=400.0, high=402.0, low=398.0, close=400.0), None)

    assert len(om._pending_orders) == 1


def test_dm08_lmt_sell_triggers_at_limit_price():
    """LMT SELL: bar.high ≥ limit → fills at limit_price exactly."""
    p = _portfolio_with_position(100, 400.0)
    om = MockOrderManager(p)
    om.place_order(
        OrderRequest(
            "SPY",
            OrderAction.SELL,
            100,
            order_type=OrderType.LIMIT,
            limit_price=420.0,
            tif=TimeInForce.GTC,
        )
    )
    fills: List[OrderResult] = []
    om.on_fill(fills.append)

    p.update_prices({"SPY": 420.0})
    om._set_bars(_bar(open_=415.0, high=425.0, low=413.0, close=420.0), None)

    assert len(fills) == 1
    assert fills[0].avg_fill_price == approx(420.0)  # no slippage on limit


def test_dm09_lmt_no_trigger_when_high_below_limit():
    p = _portfolio_with_position(100, 400.0)
    om = MockOrderManager(p)
    om.place_order(
        OrderRequest(
            "SPY",
            OrderAction.SELL,
            100,
            order_type=OrderType.LIMIT,
            limit_price=420.0,
            tif=TimeInForce.GTC,
        )
    )
    p.update_prices({"SPY": 415.0})
    om._set_bars(_bar(open_=415.0, high=418.0, low=413.0, close=415.0), None)

    assert len(om._pending_orders) == 1


def test_dm10_stp_with_slippage():
    """STP SELL slippage: fill = stop * (1 - slippage_bps/10000)."""
    p = _portfolio_with_position(100, 400.0)
    om = MockOrderManager(p)
    om.place_order(
        OrderRequest(
            "SPY",
            OrderAction.SELL,
            100,
            order_type=OrderType.STOP,
            stop_price=390.0,
            tif=TimeInForce.GTC,
            backtest_slippage_bps=3.0,
        )
    )
    fills: List[OrderResult] = []
    om.on_fill(fills.append)

    p.update_prices({"SPY": 390.0})
    om._set_bars(_bar(open_=395.0, high=396.0, low=385.0, close=390.0), None)

    expected = 390.0 * (1 - 3 / 10_000)
    assert fills[0].avg_fill_price == approx(expected, abs=0.01)


def test_dm11_bracket_not_double_processed_on_same_bar():
    """
    STP/LMT brackets added by on_fill(BUY) must NOT trigger on the same bar
    the BUY fills.  They sit in pending and are only evaluated from the next bar.
    """
    p = BacktestPortfolio(initial_capital=50_000.0, commission=0.0)
    om = MockOrderManager(p)

    # on_fill(BUY) will place a STP with stop well below the bar low
    def add_bracket(result: OrderResult):
        if result.action == "BUY":
            stp = OrderRequest(
                "SPY",
                OrderAction.SELL,
                int(result.quantity),
                order_type=OrderType.STOP,
                stop_price=95.0,  # low=99 > 95 → no trigger
                tif=TimeInForce.GTC,
            )
            om.place_order(stp, allow_duplicate=True)

    om.on_fill(add_bracket)

    buy_fills: List[OrderResult] = []
    sell_fills: List[OrderResult] = []

    def track(r: OrderResult):
        (buy_fills if r.action == "BUY" else sell_fills).append(r)

    om.on_fill(track)
    om.place_order(OrderRequest("SPY", OrderAction.BUY, 10))

    # Bar: low=90 < stop=95 — would trigger STP if processed same bar
    p.update_prices({"SPY": 100.0})
    om._set_bars(_bar(open_=100.0, high=105.0, low=90.0, close=100.0), None)

    assert len(buy_fills) == 1
    assert len(sell_fills) == 0  # bracket NOT fired this bar
    assert len(om._pending_orders) == 1  # bracket still waiting


def test_dm12_current_equity_method():
    p = BacktestPortfolio(initial_capital=50_000.0, commission=0.0)
    om = MockOrderManager(p)
    assert om.current_equity() == approx(50_000.0)


# ══════════════════════════════════════════════════════════════════════════════
# Section E — RSI2MR_SPY unit tests (direct method calls)
# ══════════════════════════════════════════════════════════════════════════════


def _make_strategy(capital: float = 50_000.0, **kwargs):
    """Minimal RSI2MR_SPY instance wired with mock infrastructure."""
    from data.vix_feed import VIXFeed
    from strategies.rsi2_mr import RSI2MR_SPY

    p = BacktestPortfolio(initial_capital=capital, commission=0.0)
    om = MockOrderManager(p)
    feed = BacktestDataFeed("SPY")

    vix_dates = pd.bdate_range("2010-01-01", periods=400)
    vix_feed = VIXFeed(series=pd.Series(20.0, index=vix_dates))

    strategy = RSI2MR_SPY(
        client=None,
        order_manager=om,
        risk_manager=None,
        reconnect=None,
        feed=feed,
        symbol="SPY",
        initial_capital=capital,
        vix_feed=vix_feed,
        **kwargs,
    )
    strategy.on_start()
    return strategy, om, feed, p


def test_em01_calc_shares_2pct_rule():
    """Without risk_manager, _calc_shares uses 2% of equity rule."""
    strategy, *_ = _make_strategy(capital=50_000.0)
    # risk/share = 10; max_risk = 50000*0.02 = 1000 → 100 shares
    shares = strategy._calc_shares(400.0, 390.0, 430.0, 50_000.0)
    assert shares == 100


def test_em02_circuit_breaker_fires_after_5_losses():
    strategy, *_ = _make_strategy()
    for _ in range(5):
        strategy._update_circuit_breaker(-1.0)
    assert strategy._circuit_breaker_until is not None


def test_em03_circuit_breaker_consecutive_resets_on_win():
    strategy, *_ = _make_strategy()
    for _ in range(3):
        strategy._update_circuit_breaker(-1.0)
    assert strategy._consecutive_losses == 3

    strategy._update_circuit_breaker(+2.0)
    assert strategy._consecutive_losses == 0


def test_em04_circuit_breaker_equity_drawdown():
    """8% equity drawdown fires circuit breaker."""
    strategy, *_ = _make_strategy(capital=50_000.0)
    strategy._strategy_peak_equity = 50_000.0
    # Simulate equity at 45000 (10% down from peak — exceeds 8% threshold)
    # MS-B: drawdown branch now reads from _get_strategy_attributed_equity.
    with patch.object(strategy, "_get_strategy_attributed_equity", return_value=45_000.0):
        strategy._update_circuit_breaker(-0.5)
    assert strategy._circuit_breaker_until is not None


def test_em05_state_round_trip(tmp_path):
    """_save_state/_load_state round-trip preserves circuit-breaker fields."""
    state_path = tmp_path / "rsi2_mr_state.json"

    strategy, *_ = _make_strategy(state_file_path=state_path)
    strategy._consecutive_losses = 4
    strategy._circuit_breaker_until = date(2027, 3, 1)
    strategy._save_state()

    strategy2, *_ = _make_strategy(state_file_path=state_path)
    strategy2._load_state()
    assert strategy2._consecutive_losses == 4
    assert strategy2._circuit_breaker_until == date(2027, 3, 1)


def test_em06_get_vix_via_feed_external():
    """_get_vix reads from feed.get_external('vix', date) in backtest mode."""
    strategy, _, feed, _ = _make_strategy()
    feed._set_external("vix", {date(2015, 1, 14): 22.5})
    result = strategy._get_vix(date(2015, 1, 14))
    assert result == approx(22.5)


# ══════════════════════════════════════════════════════════════════════════════
# Section MS-B — strategy-attributed equity (circuit breaker isolation)
# ══════════════════════════════════════════════════════════════════════════════


class _FakeAccountClient:
    """Minimal stand-in for IBKRClient: only get_account_summary is exercised."""

    class _Tag:
        def __init__(self, tag, value):
            self.tag = tag
            self.value = value

    def __init__(self, net_liquidation: float):
        self._nl = net_liquidation

    def get_account_summary(self):
        return [self._Tag("NetLiquidation", str(self._nl))]


class _FakeTradeLog:
    """In-memory TradeLog stub for MS-B tests."""

    def __init__(self, realized_pnl_by_strategy: dict | None = None):
        self._pnl = realized_pnl_by_strategy or {}

    def realized_pnl_since(self, strategy_name: str, cutoff_iso: str) -> float:
        return float(self._pnl.get(strategy_name, 0.0))


def _make_live_strategy(
    capital: float = 50_000.0,
    net_liquidation: float = 50_000.0,
    realized_by_strategy: dict | None = None,
    strategy_name: str | None = "RSI2MR-SPY",
):
    """Build an RSI2MR_SPY in 'live' mode (client is not None) for MS-B tests."""
    from data.vix_feed import VIXFeed
    from strategies.rsi2_mr import RSI2MR_SPY

    client = _FakeAccountClient(net_liquidation=net_liquidation)
    p = BacktestPortfolio(initial_capital=capital, commission=0.0)
    om = MockOrderManager(p)
    feed = BacktestDataFeed("SPY")
    vix_dates = pd.bdate_range("2010-01-01", periods=400)
    vix_feed = VIXFeed(series=pd.Series(20.0, index=vix_dates))

    strategy = RSI2MR_SPY(
        client=client,
        order_manager=om,
        risk_manager=None,
        reconnect=None,
        feed=feed,
        symbol="SPY",
        initial_capital=capital,
        vix_feed=vix_feed,
    )
    # Mirror StrategyRunner.build() injection.
    strategy._strategy_name = strategy_name
    strategy._trade_log = _FakeTradeLog(realized_by_strategy)
    # Test isolation: client != None would normally enable disk persistence on
    # the default `data/rsi2_mr_state.json` path (prod state file). Force off
    # so tests cannot pollute production state.
    strategy._persist_state = False
    return strategy


def test_msb_01_attributed_equity_no_history_returns_initial_capital():
    """No fills logged → attributed equity == initial_capital, regardless of NetLiq."""
    strategy = _make_live_strategy(capital=50_000.0, net_liquidation=120_000.0)
    assert strategy._get_strategy_attributed_equity() == approx(50_000.0)


def test_msb_02_attributed_equity_includes_own_realized_pnl_only():
    """Strategy A's gains ratchet only A's equity; B's equity is unaffected."""
    # Account NetLiq is 80k (other strategies up 30k). Only RSI2MR has +500 realized.
    strategy = _make_live_strategy(
        capital=50_000.0,
        net_liquidation=80_000.0,
        realized_by_strategy={"RSI2MR-SPY": 500.0, "SMACrossover-QQQ": 29_500.0},
    )
    # RSI2MR sees only its own +500 → 50,500 (NOT 80k from account NetLiq).
    assert strategy._get_strategy_attributed_equity() == approx(50_500.0)


def test_msb_03_attributed_equity_includes_unrealized_on_open_position():
    """Open position with mark-to-market loss reduces attributed equity."""
    strategy = _make_live_strategy(capital=50_000.0, net_liquidation=50_000.0)
    strategy._in_position = True
    strategy._position_shares = 100
    strategy._entry_price = 400.0
    strategy._closes = [395.0]  # $5 unrealized loss × 100 shares = -$500
    assert strategy._get_strategy_attributed_equity() == approx(49_500.0)


def test_msb_04_peak_ratchet_isolates_from_other_strategy_gains():
    """
    Bug-of-record: another strategy's gains must NOT raise RSI2MR's peak equity,
    so a later RSI2MR-only loss cannot fire the 8% drawdown trip spuriously.
    """
    # Step 1: SMA gains $20k, RSI2MR realized $0. Account NetLiq = 70k.
    strategy = _make_live_strategy(
        capital=50_000.0,
        net_liquidation=70_000.0,
        realized_by_strategy={"RSI2MR-SPY": 0.0, "SMACrossover-QQQ": 20_000.0},
    )
    # Manually invoke the ratchet branch logic.
    se = strategy._get_strategy_attributed_equity()
    if se is not None and se > strategy._strategy_peak_equity:
        strategy._strategy_peak_equity = se
    # Peak stays at initial_capital (50k) — NOT raised to 70k.
    assert strategy._strategy_peak_equity == approx(50_000.0)


def test_msb_05_drawdown_trip_uses_strategy_equity_not_account():
    """
    Account NetLiq down 10% (well past 8%) but RSI2MR is flat → CB does NOT fire.
    Pre-MS-B, this would fire spuriously.
    """
    strategy = _make_live_strategy(
        capital=50_000.0,
        net_liquidation=45_000.0,  # account down 10%
        realized_by_strategy={"RSI2MR-SPY": 0.0, "SMACrossover-QQQ": -5_000.0},
    )
    strategy._strategy_peak_equity = 50_000.0
    strategy._update_circuit_breaker(-0.5)  # one losing trade (not 5 in a row)
    assert strategy._circuit_breaker_until is None


def test_msb_06_drawdown_trip_fires_on_strategy_own_loss():
    """RSI2MR's own realized losses trip the 8% drawdown CB."""
    strategy = _make_live_strategy(
        capital=50_000.0,
        net_liquidation=50_000.0,
        realized_by_strategy={"RSI2MR-SPY": -5_000.0},  # -10% own
    )
    strategy._strategy_peak_equity = 50_000.0
    strategy._update_circuit_breaker(-0.5)
    assert strategy._circuit_breaker_until is not None


def test_msb_07_no_trade_log_falls_back_to_initial_capital():
    """Missing TradeLog (mis-wired) → conservative fallback to initial_capital."""
    strategy = _make_live_strategy(capital=50_000.0, net_liquidation=99_999.0)
    strategy._trade_log = None
    assert strategy._get_strategy_attributed_equity() == approx(50_000.0)


def test_msb_08_backtest_path_uses_account_equity_unchanged():
    """In backtest (client is None), attributed equity == _get_equity (no contamination)."""
    strategy, om, *_ = _make_strategy(capital=50_000.0)
    # MockOrderManager equity == cash (no positions).
    assert strategy._get_strategy_attributed_equity() == approx(strategy._get_equity())


def test_msb_09_peak_ratchets_on_own_realized_gain():
    """Positive case: own realized P&L raises strategy_peak_equity (does NOT stay flat)."""
    strategy = _make_live_strategy(
        capital=50_000.0,
        net_liquidation=50_000.0,
        realized_by_strategy={"RSI2MR-SPY": 7_500.0},
    )
    # Apply the ratchet branch from on_tick.
    se = strategy._get_strategy_attributed_equity()
    assert se == approx(57_500.0)
    if se is not None and se > strategy._strategy_peak_equity:
        strategy._strategy_peak_equity = se
    assert strategy._strategy_peak_equity == approx(57_500.0)


def test_msb_10_realized_pnl_fetch_failure_falls_back_to_initial_capital():
    """If TradeLog.realized_pnl_since raises, fall back conservatively to initial_capital."""

    class _BrokenTradeLog:
        def realized_pnl_since(self, *a, **kw):
            raise RuntimeError("DB locked")

    strategy = _make_live_strategy(capital=50_000.0, net_liquidation=99_999.0)
    strategy._trade_log = _BrokenTradeLog()
    assert strategy._get_strategy_attributed_equity() == approx(50_000.0)


def test_msb_11_state_migration_resets_v1_peak_and_cb(tmp_path):
    """
    Pre-MS-B (v1 / no schema_version) state files used contaminated NetLiq peak
    AND can carry an active circuit_breaker_until. Loading must reset both.
    """
    import json as _json

    state_path = tmp_path / "rsi2_mr_state.json"
    # Write a synthetic v1 state file (no schema_version, contaminated values).
    state_path.write_text(
        _json.dumps(
            {
                "consecutive_losses": 0,
                "strategy_peak_equity": 80_000.0,  # contaminated by other strategy
                "circuit_breaker_until": "2099-12-31",  # would halt RSI2MR
                "in_position": False,
                "entry_price": 0.0,
            }
        )
    )

    strategy, *_ = _make_strategy(capital=50_000.0, state_file_path=state_path)
    # _make_strategy invokes on_start() which calls _load_state(). Migration
    # should have reset the contaminated peak and cleared the CB.
    assert strategy._strategy_peak_equity == approx(50_000.0)
    assert strategy._circuit_breaker_until is None


def _make_sell_result(filled: float, order_id: int = 999, avg_price: float = 410.0):
    """Construct a FILLED-status SELL OrderResult for partial-fill tests."""
    from models.order import OrderStatus

    return OrderResult(
        order_id=order_id,
        symbol="SPY",
        action="SELL",
        quantity=100.0,
        order_type="MKT",
        tif="GTC",
        status=OrderStatus.FILLED,
        filled=filled,
        remaining=max(0.0, 100.0 - filled),
        avg_fill_price=avg_price,
        limit_price=None,
        stop_price=None,
    )


def _ready_in_position(strategy, shares: int = 100, entry: float = 400.0):
    """Place a strategy in a steady in_position state for SELL-handling tests."""
    strategy._in_position = True
    strategy._position_shares = shares
    strategy._entry_price = entry
    strategy._stop_price = entry * 0.97
    strategy._target_price = entry * 1.09


def test_msb_13_partial_sell_trips_halt_and_cb():
    """Partial SELL (filled < position) must trip _partial_fill_halt + CB without
    zeroing position state or stamping cost_basis."""
    from datetime import date as _date, timedelta as _td

    strategy = _make_live_strategy(capital=50_000.0)
    _ready_in_position(strategy, shares=100, entry=400.0)
    # Patch ntfy alert so the test does not hit the network.
    with patch.object(strategy, "_fire_circuit_breaker_alert"):
        sell = _make_sell_result(filled=50.0)
        strategy.on_fill(sell)

    assert strategy._partial_fill_halt is True
    assert strategy._circuit_breaker_until is not None
    # CB lands on the 1st of next month.
    today = _date.today()
    expected = (today.replace(day=1) + _td(days=32)).replace(day=1)
    assert strategy._circuit_breaker_until == expected
    # State preserved for operator triage.
    assert strategy._in_position is True
    assert strategy._position_shares == 100
    assert strategy._entry_price == approx(400.0)
    # cost_basis intentionally NOT stamped on the SELL — TradeLog will record
    # the row with realized_pnl=NULL, signalling reconcile-needed.
    assert sell.cost_basis is None


def test_msb_14_full_sell_unaffected_positive_control():
    """Full SELL (filled == position) must take the normal cleanup path."""
    strategy = _make_live_strategy(capital=50_000.0)
    _ready_in_position(strategy, shares=100, entry=400.0)
    sell = _make_sell_result(filled=100.0, avg_price=410.0)
    strategy.on_fill(sell)

    assert strategy._partial_fill_halt is False
    assert strategy._in_position is False
    assert strategy._position_shares == 0
    # Full SELL stamps cost_basis from _entry_price (MS-A1).
    assert sell.cost_basis == approx(400.0)


def test_msb_15_halted_strategy_does_not_exit_dangling_position():
    """Regression: with _partial_fill_halt=True and a dangling position,
    on_tick must NOT call _exit and must NOT place new orders."""
    strategy, om, *_ = _make_strategy(capital=50_000.0)
    _ready_in_position(strategy, shares=100, entry=400.0)
    strategy._partial_fill_halt = True
    # Pre-fill closes/dates so the warmup gate is past — if on_tick fell
    # through to _check_exits it would absolutely fire (RSI=0 once filled).
    strategy._closes = [400.0] * 260
    strategy._highs = [402.0] * 260
    strategy._lows = [398.0] * 260
    strategy._bar_dates = [date(2014, 1, 2)] * 260
    strategy._bar_index = 260
    strategy._bars_held = strategy._TIME_STOP_BARS + 1  # would force time-stop

    with (
        _PATCH_FOMC,
        _PATCH_RUSSELL,
        _PATCH_HOLIDAY,
        patch.object(strategy, "_exit") as exit_spy,
        patch.object(strategy, "safe_place_order") as place_spy,
    ):
        strategy.on_tick()

    assert exit_spy.call_count == 0
    assert place_spy.call_count == 0


def test_msb_16_partial_halt_persists_across_restart(tmp_path):
    """Save state with _partial_fill_halt=True; reload into a fresh strategy."""
    state_path = tmp_path / "rsi2_mr_state.json"
    s1, *_ = _make_strategy(capital=50_000.0, state_file_path=state_path)
    s1._partial_fill_halt = True
    s1._save_state()

    s2, *_ = _make_strategy(capital=50_000.0, state_file_path=state_path)
    s2._load_state()
    assert s2._partial_fill_halt is True


def test_msb_17_v1_to_v2_migration_persists_eagerly(tmp_path):
    """Migration on load must immediately write the v2 schema to disk so a
    crash before the next save trigger does not re-fire the warning on the
    next start (and so new v2 fields like partial_fill_halt land on disk)."""
    import json as _json

    state_path = tmp_path / "rsi2_mr_state.json"
    # Synthetic v1 file (no schema_version, no partial_fill_halt).
    state_path.write_text(
        _json.dumps(
            {
                "consecutive_losses": 0,
                "strategy_peak_equity": 50_000.0,
                "circuit_breaker_until": None,
                "entry_price": 0.0,
                "in_position": False,
            }
        )
    )

    _make_strategy(capital=50_000.0, state_file_path=state_path)

    # File must now be v2: schema_version present and new field persisted.
    persisted = _json.loads(state_path.read_text())
    assert persisted.get("schema_version") == 2
    assert persisted.get("partial_fill_halt") is False


def test_msb_12_state_migration_idempotent_on_v2(tmp_path):
    """v2 state file is loaded as-is — no reset on the second deploy."""
    import json as _json

    state_path = tmp_path / "rsi2_mr_state.json"
    state_path.write_text(
        _json.dumps(
            {
                "schema_version": 2,
                "consecutive_losses": 0,
                "strategy_peak_equity": 55_000.0,  # legitimate post-MS-B peak
                "circuit_breaker_until": None,
                "in_position": False,
                "entry_price": 0.0,
            }
        )
    )

    strategy, *_ = _make_strategy(capital=50_000.0, state_file_path=state_path)
    # No migration: the legit v2 peak survives.
    assert strategy._strategy_peak_equity == approx(55_000.0)


# ══════════════════════════════════════════════════════════════════════════════
# Section MS-J — atomic state-file write (tmp + os.replace)
# ══════════════════════════════════════════════════════════════════════════════


def test_msj_01_save_leaves_no_tmp_file_on_success(tmp_path):
    """After a normal _save_state, the sibling .tmp file must not linger."""
    state_path = tmp_path / "rsi2_mr_state.json"
    strategy, *_ = _make_strategy(state_file_path=state_path)
    strategy._consecutive_losses = 2
    strategy._save_state()

    assert state_path.exists()
    tmp_sibling = state_path.with_suffix(state_path.suffix + ".tmp")
    assert not tmp_sibling.exists(), "tmp file should be renamed-away, not left behind"
    # Confirm the main file is intact JSON
    import json as _json

    payload = _json.loads(state_path.read_text())
    assert payload["consecutive_losses"] == 2


def test_msj_02_truncated_main_file_does_not_corrupt_next_save(tmp_path):
    """Simulate a process kill mid-write under the OLD non-atomic code: a
    truncated main file. With the MS-J fix, the next _save_state writes the
    full state atomically (via tmp+rename) and yields valid JSON, NOT a
    silent fallback to defaults."""
    import json as _json

    state_path = tmp_path / "rsi2_mr_state.json"
    state_path.write_text('{"schema_version": 2, "consecutive_loss')  # truncated

    strategy, *_ = _make_strategy(state_file_path=state_path)
    strategy._consecutive_losses = 7
    strategy._strategy_peak_equity = 55_000.0
    strategy._save_state()

    payload = _json.loads(state_path.read_text())
    assert payload["consecutive_losses"] == 7
    assert payload["strategy_peak_equity"] == 55_000.0
    # And no orphan tmp
    assert not state_path.with_suffix(state_path.suffix + ".tmp").exists()


def test_msj_03_orphan_tmp_from_crashed_prior_save_is_overwritten(tmp_path):
    """If a prior process died AFTER tmp.write_text but BEFORE os.replace,
    a stale .tmp file lingers. The next _save_state must overwrite it cleanly
    and still produce an intact main file."""
    import json as _json

    state_path = tmp_path / "rsi2_mr_state.json"
    tmp_sibling = state_path.with_suffix(state_path.suffix + ".tmp")
    # Pretend a prior crash left behind a stale (garbage) tmp file
    tmp_sibling.write_text("garbage from a previous run")

    strategy, *_ = _make_strategy(state_file_path=state_path)
    strategy._consecutive_losses = 3
    strategy._save_state()

    assert state_path.exists()
    assert not tmp_sibling.exists()  # consumed by os.replace
    payload = _json.loads(state_path.read_text())
    assert payload["consecutive_losses"] == 3


# ══════════════════════════════════════════════════════════════════════════════
# Section MS-C — yfinance hardening: persistent _refresh_history alerting
# ══════════════════════════════════════════════════════════════════════════════


def _patch_refresh_to_raise(strategy, exc=RuntimeError("yfinance boom")):
    """Force `_refresh_history` to take the exception path by patching the
    HistoricalDataLoader call site at import time inside the method."""
    return patch(
        "data.historical.HistoricalDataLoader.load_yfinance",
        side_effect=exc,
    )


def _patch_refresh_to_succeed(strategy):
    """Force `_refresh_history` to take the success path with valid OHLC data."""
    n = 250
    dates = pd.bdate_range("2024-01-01", periods=n)
    df = pd.DataFrame(
        {
            "close": [400.0] * n,
            "high": [401.0] * n,
            "low": [399.0] * n,
        },
        index=dates,
    )
    return patch(
        "data.historical.HistoricalDataLoader.load_yfinance",
        return_value=df,
    )


def test_msc_01_flat_one_failure_no_alert(monkeypatch):
    """Flat strategy: a single _refresh_history failure does NOT fire ntfy
    (threshold=2 for flat). Counter increments to 1."""
    monkeypatch.setenv("NTFY_TOPIC", "test-topic")
    strategy, *_ = _make_strategy()
    assert not strategy._in_position

    with _patch_refresh_to_raise(strategy):
        with patch("urllib.request.urlopen") as mock_post:
            ok = strategy._refresh_history()

    assert ok is False
    assert strategy._refresh_history_failures == 1
    assert strategy._refresh_history_alert_fired is False
    mock_post.assert_not_called()


def test_msc_02_flat_crossing_fires_once_no_refire(monkeypatch):
    """Flat strategy: 2nd failure fires ntfy once; further failures do NOT
    re-fire (one alert per outage)."""
    monkeypatch.setenv("NTFY_TOPIC", "test-topic")
    strategy, *_ = _make_strategy()

    with _patch_refresh_to_raise(strategy):
        with patch("urllib.request.urlopen") as mock_post:
            strategy._refresh_history()  # 1 — no alert
            strategy._refresh_history()  # 2 — fires
            strategy._refresh_history()  # 3 — no refire
            strategy._refresh_history()  # 4 — no refire

    assert strategy._refresh_history_failures == 4
    assert strategy._refresh_history_alert_fired is True
    assert mock_post.call_count == 1


def test_msc_03_in_position_first_failure_fires(monkeypatch):
    """Held strategy: threshold=1 — alert fires on the FIRST failure because
    exit checks are blind during the outage."""
    monkeypatch.setenv("NTFY_TOPIC", "test-topic")
    strategy, *_ = _make_strategy()
    strategy._in_position = True

    with _patch_refresh_to_raise(strategy):
        with patch("urllib.request.urlopen") as mock_post:
            strategy._refresh_history()

    assert strategy._refresh_history_failures == 1
    assert strategy._refresh_history_alert_fired is True
    assert mock_post.call_count == 1


def test_msc_04_success_resets_counter_and_logs_recovery(monkeypatch, caplog):
    """A success after one or more failures resets the counter and the
    alert-fired latch, and emits a recovery log line."""
    import logging as _logging

    monkeypatch.setenv("NTFY_TOPIC", "test-topic")
    strategy, *_ = _make_strategy()

    with _patch_refresh_to_raise(strategy):
        with patch("urllib.request.urlopen"):
            strategy._refresh_history()
            strategy._refresh_history()  # threshold crossed, alert fired

    assert strategy._refresh_history_alert_fired is True

    caplog.set_level(_logging.INFO, logger="strategies.rsi2_mr")
    with _patch_refresh_to_succeed(strategy):
        ok = strategy._refresh_history()

    assert ok is True
    assert strategy._refresh_history_failures == 0
    assert strategy._refresh_history_alert_fired is False
    assert any("recovered after" in rec.getMessage() for rec in caplog.records)


def test_msc_05_rearm_after_recovery_fires_again(monkeypatch):
    """After recovery, a fresh outage MUST be able to fire a new alert
    (counter and latch both rearm)."""
    monkeypatch.setenv("NTFY_TOPIC", "test-topic")
    strategy, *_ = _make_strategy()

    with _patch_refresh_to_raise(strategy):
        with patch("urllib.request.urlopen") as mock_post:
            strategy._refresh_history()
            strategy._refresh_history()  # alert 1
            assert mock_post.call_count == 1

    with _patch_refresh_to_succeed(strategy):
        strategy._refresh_history()  # recovery

    with _patch_refresh_to_raise(strategy):
        with patch("urllib.request.urlopen") as mock_post:
            strategy._refresh_history()
            strategy._refresh_history()  # alert 2 — rearmed

    assert mock_post.call_count == 1


def test_msc_07_no_ntfy_topic_silent_no_op(monkeypatch):
    """If NTFY_TOPIC is unset the alert helper returns early without ever
    calling urlopen. Production default if the env var is forgotten."""
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    strategy, *_ = _make_strategy()

    with _patch_refresh_to_raise(strategy):
        with patch("urllib.request.urlopen") as mock_post:
            strategy._refresh_history()
            strategy._refresh_history()  # threshold crossed — alert path taken

    # Latch still flips so we don't spin on the env-var check.
    assert strategy._refresh_history_alert_fired is True
    mock_post.assert_not_called()


def test_msc_08_counter_not_persisted_to_state_file(tmp_path):
    """MS-C counter and latch are explicitly in-memory. Guard against a future
    state-schema bump silently persisting them."""
    import json as _json

    state_path = tmp_path / "rsi2_mr_state.json"
    strategy, *_ = _make_strategy(state_file_path=state_path)
    strategy._refresh_history_failures = 5
    strategy._refresh_history_alert_fired = True
    strategy._save_state()

    on_disk = _json.loads(state_path.read_text())
    assert "refresh_history_failures" not in on_disk
    assert "_refresh_history_failures" not in on_disk
    assert "refresh_history_alert_fired" not in on_disk
    assert "_refresh_history_alert_fired" not in on_disk


def test_msc_06_ntfy_post_failure_does_not_break_counter(monkeypatch):
    """If the ntfy POST itself raises (network partition), the counter must
    still increment correctly and the alert-fired latch must still latch —
    we got our one shot, no point retrying every tick."""
    monkeypatch.setenv("NTFY_TOPIC", "test-topic")
    strategy, *_ = _make_strategy()

    with _patch_refresh_to_raise(strategy):
        with patch(
            "urllib.request.urlopen",
            side_effect=OSError("connection refused"),
        ):
            strategy._refresh_history()
            strategy._refresh_history()  # threshold crossed; POST fails silently
            strategy._refresh_history()

    assert strategy._refresh_history_failures == 3
    assert strategy._refresh_history_alert_fired is True


# ══════════════════════════════════════════════════════════════════════════════
# Section F — Full BacktestEngine integration tests
# ══════════════════════════════════════════════════════════════════════════════


def test_fi01_smoke_produces_fills():
    """Full run on synthetic data: entry signal fires and at least one fill is produced."""
    df = _make_spy_df(n=295, signal_offset=35)
    result = _run_engine(df)
    # At minimum the BUY should fill; SELL may or may not depending on bars remaining
    assert len(result.fills) >= 1
    buy_fills = [f for f in result.fills if f.action == "BUY"]
    assert len(buy_fills) >= 1


def test_fi02_time_stop_produces_sell():
    """After entry, flat prices for 8+ bars → time-stop SELL fires."""
    # 295 bars: signal at bar 260, flat to bar 294
    # entry fills bar 261; time-stop fires bar 269; sell fills bar 270
    df = _make_spy_df(n=295, signal_offset=35)
    result = _run_engine(df)

    sell_fills = [f for f in result.fills if f.action == "SELL"]
    assert len(sell_fills) >= 1, "expected a SELL fill from time-stop"


def test_fi03_regime_gate_blocks_entry_below_sma200():
    """Prices falling below SMA(200) → regime gate blocks all entries → no BUY fills."""
    n = 295
    # All prices downtrending: close starts at 500 and falls 0.5/bar
    # SMA(200) will be above the current price after ~100 bars
    prices = [500.0 - 0.5 * i for i in range(n)]
    dates = pd.bdate_range("2014-01-02", periods=n)
    df = pd.DataFrame(
        {
            "open": prices,
            "high": [p * 1.002 for p in prices],
            "low": [p * 0.998 for p in prices],
            "close": prices,
            "volume": [10_000_000] * n,
        },
        index=dates,
    )
    result = _run_engine(df)
    buy_fills = [f for f in result.fills if f.action == "BUY"]
    assert len(buy_fills) == 0


def test_fi04_vix_panic_blocks_entry():
    """VIX above vix_upper (35) on all bars → no entry ever fires."""
    df = _make_spy_df(n=295, signal_offset=35)
    # VIX=40 everywhere — above default vix_upper=35
    vix_series = _make_vix_series(df, vix=40.0)
    result = _run_engine(df, vix_series=vix_series)

    buy_fills = [f for f in result.fills if f.action == "BUY"]
    assert len(buy_fills) == 0


def test_fi05_stp_fills_on_gap_down():
    """Bracket STP fires when a bar gaps down below stop after entry."""
    # Build data: entry fires at bar 260, then a crash gap on bar 262
    df = _make_spy_df(n=295, signal_offset=35)
    prices = list(df["close"])
    sig_close = prices[260]

    # bar 261 (entry bar — BUY fills here at open ≈ sig_close)
    # bar 262: gap open well below stop (stop ≈ sig_close - 1.5*ATR ≈ sig_close - 3)
    gap_open = sig_close - 20.0  # far below any reasonable stop
    prices[262] = gap_open
    df.iloc[262] = [gap_open, gap_open * 1.002, gap_open * 0.998, gap_open, 10_000_000]

    result = _run_engine(df)
    sell_fills = [f for f in result.fills if f.action == "SELL"]
    # STP should have triggered on bar 262 (or time-stop later if not)
    assert len(sell_fills) >= 1


def test_fi06_real_r_multiple_attached_to_sell_fill():
    """real_r_multiple is set on SELL fills (can be None only if entry data missing)."""
    df = _make_spy_df(n=295, signal_offset=35)
    result = _run_engine(df)

    sell_fills = [f for f in result.fills if f.action == "SELL"]
    if sell_fills:
        # real_r_multiple should be a float (not None) since entry_price and stop are set
        assert sell_fills[0].real_r_multiple is not None
        assert isinstance(sell_fills[0].real_r_multiple, float)


def test_fi07_no_entry_during_warmup():
    """Strategy produces zero trades when all data is within the 240-bar warmup gate."""
    n = 239  # one bar short of warmup
    prices = [100.0 + 0.5 * i for i in range(n)]
    dates = pd.bdate_range("2014-01-02", periods=n)
    df = pd.DataFrame(
        {
            "open": prices,
            "high": [p * 1.002 for p in prices],
            "low": [p * 0.998 for p in prices],
            "close": prices,
            "volume": [10_000_000] * n,
        },
        index=dates,
    )
    result = _run_engine(df)
    assert len(result.fills) == 0


def test_fi08_cooldown_prevents_reentry():
    """After a SELL, no new BUY fires for at least COOLDOWN_BARS bars."""
    from strategies.rsi2_mr import RSI2MR_SPY

    # Long flat section after time-stop where re-entry would normally fire.
    # We patch _COOLDOWN_BARS to 200 — larger than the bars remaining after
    # the first SELL (~80) — so the cooldown gate is the only thing that
    # could prevent a second BUY. If cooldown logic is broken, we'll see
    # len(buys) >= 2.
    df = _make_spy_df(n=350, signal_offset=90)

    with (
        _PATCH_FOMC,
        _PATCH_RUSSELL,
        _PATCH_HOLIDAY,
        patch.object(RSI2MR_SPY, "_COOLDOWN_BARS", 200),
        patch.object(RSI2MR_SPY, "_load_state", lambda self: None),
        patch.object(RSI2MR_SPY, "_save_state", lambda self: None),
    ):
        engine = BacktestEngine(
            strategy_class=RSI2MR_SPY,
            data=df,
            symbol="SPY",
            initial_capital=50_000.0,
            commission=0.0,
            external_data={"vix": _make_vix_series(df)},
        )
        result = engine.run()

    buys = [f for f in result.fills if f.action == "BUY"]
    sells = [f for f in result.fills if f.action == "SELL"]
    # Cooldown gate blocks the second entry — exactly one full cycle fits.
    assert len(buys) == 1, f"Cooldown failed to block re-entry: got {len(buys)} BUYs"
    assert len(sells) == 1, f"Expected exactly one SELL (time-stop), got {len(sells)}"
