from __future__ import annotations

"""
TradeLog — Task 3.5

Persistent trade history backed by SQLite (Python stdlib — no extra dependency).

Wired to OrderManager's on_fill callback so every fill is automatically recorded.
Provides daily summary and full history queries.

Usage:
    log = TradeLog()                                 # opens/creates trades.db
    om.on_fill(lambda r: log.record(r, "MyStrategy"))

    history = log.get_history(symbol="AAPL")
    summary = log.daily_summary()
"""

import logging
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from models.order import OrderResult

logger = logging.getLogger(__name__)

_DEFAULT_DB = Path(__file__).parent.parent / "data" / "trades.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name   TEXT    NOT NULL,
    symbol          TEXT    NOT NULL,
    action          TEXT    NOT NULL,       -- BUY or SELL
    quantity        REAL    NOT NULL,
    fill_price      REAL    NOT NULL,
    fill_value      REAL    NOT NULL,       -- quantity × fill_price
    filled_at       TEXT    NOT NULL,       -- ISO-8601 UTC datetime
    order_id        INTEGER,
    account         TEXT
);
"""


class TradeLog:
    """
    Append-only SQLite trade journal.

    Thread-safe: each write opens its own connection with WAL journal mode.

    Args:
        db_path: Path to the SQLite database file. Created if it doesn't exist.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        logger.info("TradeLog initialized at %s", self._db_path)

    # ------------------------------------------------------------------
    # Public: write
    # ------------------------------------------------------------------

    def record(self, result: OrderResult, strategy_name: str) -> None:
        """
        Record a filled order. Call this from an on_fill callback.

        Args:
            result:        OrderResult from the fill event.
            strategy_name: Name of the strategy that placed the order.
        """
        if result.avg_fill_price is None or result.filled == 0:
            return   # not actually filled — nothing to record

        fill_value = result.filled * result.avg_fill_price
        filled_at = (
            result.submitted_at.isoformat()
            if result.submitted_at
            else datetime.now(timezone.utc).isoformat()
        )

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trades
                    (strategy_name, symbol, action, quantity, fill_price,
                     fill_value, filled_at, order_id, account)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    strategy_name,
                    result.symbol,
                    result.action,
                    result.filled,
                    result.avg_fill_price,
                    fill_value,
                    filled_at,
                    result.order_id,
                    None,   # account populated in a future sprint via position data
                ),
            )

        logger.debug(
            "TradeLog: recorded %s %s x%.0f @ %.4f (strategy=%s)",
            result.action, result.symbol, result.filled,
            result.avg_fill_price, strategy_name,
        )

    # ------------------------------------------------------------------
    # Public: read
    # ------------------------------------------------------------------

    def get_history(
        self,
        symbol: Optional[str] = None,
        strategy: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 500,
    ) -> List[Dict]:
        """
        Return trade history as a list of dicts, newest first.

        Args:
            symbol:   Filter by symbol (case-insensitive). None = all symbols.
            strategy: Filter by strategy name. None = all strategies.
            since:    Return only trades after this datetime. None = all time.
            limit:    Maximum number of rows returned (default 500).

        Returns:
            List of dicts with keys: id, strategy_name, symbol, action,
            quantity, fill_price, fill_value, filled_at, order_id, account.
        """
        query = "SELECT * FROM trades WHERE 1=1"
        params: List = []

        if symbol:
            query += " AND UPPER(symbol) = ?"
            params.append(symbol.upper())
        if strategy:
            query += " AND strategy_name = ?"
            params.append(strategy)
        if since:
            query += " AND filled_at >= ?"
            params.append(since.isoformat())

        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()

        return [dict(r) for r in rows]

    def daily_summary(self, date: Optional[datetime] = None) -> Dict:
        """
        Aggregate P&L and trade count for a given day (default: today UTC).

        Returns a dict with:
            date:         Date string (YYYY-MM-DD)
            total_trades: Number of fills
            buys:         Number of BUY fills
            sells:        Number of SELL fills
            gross_buy:    Total USD value of BUY trades
            gross_sell:   Total USD value of SELL trades
            net_flow:     gross_sell - gross_buy (positive = net seller)
        """
        if date is None:
            date = datetime.now(timezone.utc)
        day_str = date.strftime("%Y-%m-%d")

        query = """
            SELECT
                COUNT(*)                              AS total_trades,
                SUM(action = 'BUY')                   AS buys,
                SUM(action = 'SELL')                  AS sells,
                SUM(CASE WHEN action='BUY'  THEN fill_value ELSE 0 END) AS gross_buy,
                SUM(CASE WHEN action='SELL' THEN fill_value ELSE 0 END) AS gross_sell
            FROM trades
            WHERE filled_at LIKE ?
        """

        with self._connect() as conn:
            row = conn.execute(query, (f"{day_str}%",)).fetchone()

        total   = row[0] or 0
        buys    = row[1] or 0
        sells   = row[2] or 0
        g_buy   = row[3] or 0.0
        g_sell  = row[4] or 0.0

        return {
            "date":         day_str,
            "total_trades": total,
            "buys":         buys,
            "sells":        sells,
            "gross_buy":    round(g_buy, 2),
            "gross_sell":   round(g_sell, 2),
            "net_flow":     round(g_sell - g_buy, 2),
        }

    def count(self) -> int:
        """Return total number of recorded trades."""
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")   # safe for concurrent readers
        return conn
