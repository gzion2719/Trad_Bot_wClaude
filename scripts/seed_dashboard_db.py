"""One-shot seed script for local dashboard verification.

Populates `data/paper_trades.db` with synthetic fills across both registered
strategies, exercising every Strategies-tab KPI render branch:

  - winners + losers       → win_rate, profit_factor mixed
  - one legacy NULL-basis  → legacy_null_basis_sells warning row
  - one row with real_r    → avg_r_multiple non-null

Run from project root:
  python -m scripts.seed_dashboard_db

Idempotent: clears the `trades` table before inserting so re-running produces
the same fixed seed state. NOT for use on the VPS — local dev only.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "paper_trades.db"

now = datetime.now(timezone.utc)


def _iso(offset_hours: float) -> str:
    return (now - timedelta(hours=offset_hours)).isoformat()


_SMA_PARAMS = json.dumps({"sma_fast": 10, "sma_slow": 30})
_RSI_PARAMS = json.dumps({"rsi_period": 2, "rsi_oversold": 10.0})

# (strategy, symbol, action, qty, price, filled_at, order_id, cost_basis,
#  realized_pnl, strategy_params, real_r_multiple)
ROWS = [
    # SMACrossover-QQQ: BUY, then SELL at +5% (win)
    ("SMACrossover-QQQ", "QQQ", "BUY", 10, 400.00, _iso(48), 1001, None, None, _SMA_PARAMS, None),
    (
        "SMACrossover-QQQ",
        "QQQ",
        "SELL",
        10,
        420.00,
        _iso(24),
        1002,
        400.00,
        200.00,
        _SMA_PARAMS,
        None,
    ),
    # SMACrossover-QQQ: BUY, then SELL at -2.5% (loss)
    ("SMACrossover-QQQ", "QQQ", "BUY", 10, 410.00, _iso(20), 1003, None, None, _SMA_PARAMS, None),
    (
        "SMACrossover-QQQ",
        "QQQ",
        "SELL",
        10,
        400.00,
        _iso(6),
        1004,
        410.00,
        -100.00,
        _SMA_PARAMS,
        None,
    ),
    # SMACrossover-QQQ: legacy NULL-basis SELL (pre-MS-A1 fill — surfaces warn row)
    ("SMACrossover-QQQ", "QQQ", "SELL", 5, 415.00, _iso(72), 999, None, None, None, None),
    # RSI2MR-SPY: BUY, then SELL at +3% with real R-multiple (win)
    ("RSI2MR-SPY", "SPY", "BUY", 8, 500.00, _iso(30), 2001, None, None, _RSI_PARAMS, None),
    ("RSI2MR-SPY", "SPY", "SELL", 8, 515.00, _iso(2), 2002, 500.00, 120.00, _RSI_PARAMS, 1.50),
]


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("DELETE FROM trades")
        conn.executemany(
            """
            INSERT INTO trades (
                strategy_name, symbol, action, quantity, fill_price, fill_value,
                filled_at, order_id, cost_basis, realized_pnl, strategy_params,
                real_r_multiple
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    strategy,
                    symbol,
                    action,
                    qty,
                    price,
                    qty * price,
                    filled_at,
                    order_id,
                    cost_basis,
                    realized_pnl,
                    strategy_params,
                    real_r,
                )
                for (
                    strategy,
                    symbol,
                    action,
                    qty,
                    price,
                    filled_at,
                    order_id,
                    cost_basis,
                    realized_pnl,
                    strategy_params,
                    real_r,
                ) in ROWS
            ],
        )
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        print(f"Seeded {count} rows into {DB_PATH}")
        for strat, n in conn.execute(
            "SELECT strategy_name, COUNT(*) FROM trades GROUP BY strategy_name"
        ):
            print(f"  {strat}: {n}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
