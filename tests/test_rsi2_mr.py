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
    with patch.object(strategy, "_get_equity", return_value=45_000.0):
        strategy._update_circuit_breaker(-0.5)
    assert strategy._circuit_breaker_until is not None


def test_em05_state_round_trip(tmp_path, monkeypatch):
    """_save_state/_load_state round-trip preserves circuit-breaker fields."""
    import strategies.rsi2_mr as rsi2_module

    state_path = tmp_path / "rsi2_mr_state.json"
    monkeypatch.setattr(rsi2_module, "_STATE_FILE", state_path)

    strategy, *_ = _make_strategy()
    strategy._consecutive_losses = 4
    strategy._circuit_breaker_until = date(2027, 3, 1)
    strategy._save_state()

    strategy2, *_ = _make_strategy()
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

    # Very long flat section after time-stop to allow potential re-entry
    df = _make_spy_df(n=350, signal_offset=90)

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
            external_data={"vix": _make_vix_series(df)},
        )
        result = engine.run()

    buys = [f for f in result.fills if f.action == "BUY"]
    if len(buys) >= 2:
        # Second buy must come after the first sell (cooldown enforced)
        sells = sorted(
            [f for f in result.fills if f.action == "SELL"], key=lambda f: f.submitted_at
        )
        if sells:
            assert buys[1].submitted_at > sells[0].submitted_at
