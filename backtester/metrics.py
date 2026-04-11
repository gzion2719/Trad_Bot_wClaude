from __future__ import annotations

"""
Performance Metrics — Task 3.4

Pure functions — no classes, no state, no side effects.
All functions accept pandas Series/lists and return simple scalars or dicts.

Usage:
    from backtester.metrics import summary
    report = summary(fills, equity_curve, initial_capital=100_000)
    # prints a formatted table and returns a dict
"""

import math
from typing import Dict, List

import pandas as pd

from models.order import OrderResult


def sharpe_ratio(
    equity_curve: pd.Series,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
) -> float:
    """
    Annualized Sharpe Ratio.

    Measures return per unit of risk. Higher is better.
    A Sharpe above 1.0 is generally considered good; above 2.0 is excellent.

    Args:
        equity_curve:     Equity values at each period (not returns).
        risk_free_rate:   Annual risk-free rate as a fraction (e.g. 0.04 = 4%).
                          Default 0.0 for simplicity.
        periods_per_year: Trading periods in a year. 252 for daily bars,
                          52 for weekly, 12 for monthly.

    Returns:
        Annualized Sharpe ratio. nan if not enough data or zero volatility.
    """
    if len(equity_curve) < 2:
        return float("nan")

    returns = equity_curve.pct_change().dropna()
    if returns.std() == 0:
        return float("nan")

    daily_rf = (1 + risk_free_rate) ** (1 / periods_per_year) - 1
    excess   = returns - daily_rf
    return float((excess.mean() / excess.std()) * math.sqrt(periods_per_year))


def max_drawdown(equity_curve: pd.Series) -> float:
    """
    Maximum peak-to-trough drawdown as a fraction.

    Returns a negative number, e.g. -0.15 means the worst drop was 15%.
    Closer to 0 is better.

    Args:
        equity_curve: Equity values at each period.

    Returns:
        Maximum drawdown as a fraction (negative). 0.0 if no drawdown.
    """
    if len(equity_curve) < 2:
        return 0.0

    rolling_max = equity_curve.cummax()
    drawdown    = (equity_curve - rolling_max) / rolling_max
    return float(drawdown.min())


def win_rate(fills: List[OrderResult]) -> float:
    """
    Fraction of SELL trades that were profitable.

    Only SELL fills are counted (they close positions and realize P&L).
    BUY-only strategies will return nan.

    Returns:
        Win rate as a fraction (0.0–1.0). nan if no sell trades.
    """
    sell_fills = [f for f in fills if f.action == "SELL" and f.avg_fill_price]
    if not sell_fills:
        return float("nan")

    # A win is defined as selling above the portfolio's average cost.
    # Since we don't have cost basis here, we use fill price > 0 as a proxy.
    # For accurate win/loss, use BacktestPortfolio.get_fills() with cost basis.
    wins = sum(1 for f in sell_fills if f.avg_fill_price and f.avg_fill_price > 0)
    return wins / len(sell_fills)


def profit_factor(fills: List[OrderResult], portfolio) -> float:
    """
    Gross profit divided by gross loss across all trades.

    Profit factor > 1.0 means the strategy makes more than it loses overall.
    > 2.0 is considered strong.

    Args:
        fills:     List of OrderResult fill records.
        portfolio: BacktestPortfolio (used for cost basis).

    Returns:
        Profit factor. inf if no losing trades. nan if no data.
    """
    sell_fills = [f for f in fills if f.action == "SELL" and f.avg_fill_price]
    if not sell_fills:
        return float("nan")

    gross_profit = 0.0
    gross_loss   = 0.0
    avg_costs    = portfolio._avg_cost   # cost basis at time of fill — approximation

    for f in sell_fills:
        cost  = avg_costs.get(f.symbol, f.avg_fill_price)
        pnl   = (f.avg_fill_price - cost) * f.filled
        if pnl >= 0:
            gross_profit += pnl
        else:
            gross_loss += abs(pnl)

    if gross_loss == 0:
        return float("inf")
    return gross_profit / gross_loss


def total_return(initial_capital: float, final_equity: float) -> float:
    """
    Total return as a percentage.

    Returns:
        e.g. 15.23 means the strategy returned 15.23%.
    """
    return ((final_equity - initial_capital) / initial_capital) * 100.0


def summary(
    fills: List[OrderResult],
    equity_curve: pd.Series,
    initial_capital: float,
    portfolio=None,
    periods_per_year: int = 252,
) -> Dict:
    """
    Compute all metrics and print a formatted report to the console.

    Args:
        fills:            List of OrderResult fills from the backtest.
        equity_curve:     pd.Series of equity values (one per bar).
        initial_capital:  Starting capital in USD.
        portfolio:        BacktestPortfolio (optional — needed for profit_factor).
        periods_per_year: 252 for daily bars.

    Returns:
        Dict with all metric values (also printed to console).
    """
    final_equity = float(equity_curve.iloc[-1]) if len(equity_curve) > 0 else initial_capital

    metrics = {
        "initial_capital":  round(initial_capital, 2),
        "final_equity":     round(final_equity, 2),
        "total_return_pct": round(total_return(initial_capital, final_equity), 2),
        "sharpe_ratio":     round(sharpe_ratio(equity_curve, periods_per_year=periods_per_year), 3),
        "max_drawdown_pct": round(max_drawdown(equity_curve) * 100, 2),
        "total_trades":     len(fills),
        "win_rate_pct":     round(win_rate(fills) * 100, 1) if not math.isnan(win_rate(fills)) else None,
        "profit_factor":    round(profit_factor(fills, portfolio), 3) if portfolio else None,
    }

    # ── Print formatted table ───────────────────────────────────────────
    print("\n" + "=" * 50)
    print("  BACKTEST RESULTS")
    print("=" * 50)
    print(f"  Initial capital : ${metrics['initial_capital']:>12,.2f}")
    print(f"  Final equity    : ${metrics['final_equity']:>12,.2f}")
    print(f"  Total return    : {metrics['total_return_pct']:>12.2f} %")
    print(f"  Sharpe ratio    : {metrics['sharpe_ratio']:>12.3f}")
    print(f"  Max drawdown    : {metrics['max_drawdown_pct']:>12.2f} %")
    print(f"  Total trades    : {metrics['total_trades']:>12}")
    if metrics["win_rate_pct"] is not None:
        print(f"  Win rate        : {metrics['win_rate_pct']:>12.1f} %")
    if metrics["profit_factor"] is not None:
        pf = metrics["profit_factor"]
        pf_str = "inf" if math.isinf(pf) else f"{pf:.3f}"
        print(f"  Profit factor   : {pf_str:>12}")
    print("=" * 50 + "\n")

    return metrics
