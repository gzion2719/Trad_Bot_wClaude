from __future__ import annotations

"""
TradeLog — Task 3.5

Persistent trade history backed by SQLite (Python stdlib — no extra dependency).

Wired to OrderManager's on_fill callback so every fill is automatically recorded.
Provides daily summary and full history queries.

Usage:
    log = TradeLog()                                 # opens/creates trades.db
    om.on_fill(lambda r: log.record(r, "MyStrategy", strategy_params={"sma": 20}))

    history = log.get_history(symbol="AAPL")
    summary = log.daily_summary()
"""

import json
import logging
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
    account         TEXT,
    cost_basis      REAL,                   -- avg cost/share at time of SELL; NULL for BUY / live
    realized_pnl    REAL,                   -- (fill_price - cost_basis) × quantity; NULL if no basis
    strategy_params TEXT,                   -- JSON blob of strategy config at trade time
    real_r_multiple REAL                    -- (exit - entry) / (entry - stop); RSI2MR only; NULL otherwise
);
"""

# Columns added in Sprint 4/Phase-B — applied via migration so existing DBs are upgraded safely.
_MIGRATIONS = [
    "ALTER TABLE trades ADD COLUMN cost_basis REAL",
    "ALTER TABLE trades ADD COLUMN realized_pnl REAL",
    "ALTER TABLE trades ADD COLUMN strategy_params TEXT",
    "ALTER TABLE trades ADD COLUMN real_r_multiple REAL",
]

# Indexes (idempotent via IF NOT EXISTS). MS-A2: per-strategy P&L queries fire
# every 60s × N strategies — without this index they would scan the full table.
_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_trades_strategy_filled " "ON trades(strategy_name, filled_at)",
]


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

    def record(
        self,
        result: OrderResult,
        strategy_name: str,
        strategy_params: Optional[dict] = None,
        real_r_multiple: Optional[float] = None,
    ) -> None:
        """
        Record a filled order. Call this from an on_fill callback.

        Args:
            result:          OrderResult from the fill event.
            strategy_name:   Name of the strategy that placed the order.
            strategy_params: Strategy configuration dict at the time of the trade.
                             Pass strategy.params here. Stored as JSON for audit trail.
        """
        if result.avg_fill_price is None or result.filled == 0:
            return  # not actually filled — nothing to record

        fill_value = result.filled * result.avg_fill_price
        filled_at = (
            result.submitted_at.isoformat()
            if result.submitted_at
            else datetime.now(timezone.utc).isoformat()
        )

        # Realized P&L: only computable for SELL fills that have a cost basis.
        # For BUY fills and live SELL fills (where cost_basis is None), store NULL.
        realized_pnl: Optional[float] = None
        if result.action == "SELL" and result.cost_basis is not None:
            realized_pnl = (result.avg_fill_price - result.cost_basis) * result.filled

        params_json: Optional[str] = None
        if strategy_params:
            try:
                params_json = json.dumps(strategy_params)
            except (TypeError, ValueError) as exc:
                logger.warning("Could not serialize strategy_params to JSON: %s", exc)

        # Prefer real_r_multiple from the explicit arg; fall back to result field
        # (RSI2MR sets result.real_r_multiple at exit time; other strategies pass None).
        r_multiple = real_r_multiple if real_r_multiple is not None else result.real_r_multiple

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trades
                    (strategy_name, symbol, action, quantity, fill_price,
                     fill_value, filled_at, order_id, account,
                     cost_basis, realized_pnl, strategy_params, real_r_multiple)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    None,  # account — populated in a future sprint via position data
                    result.cost_basis,
                    realized_pnl,
                    params_json,
                    r_multiple,
                ),
            )

        logger.debug(
            "TradeLog: recorded %s %s x%.0f @ %.4f | pnl=%s (strategy=%s)",
            result.action,
            result.symbol,
            result.filled,
            result.avg_fill_price,
            f"${realized_pnl:.2f}" if realized_pnl is not None else "N/A",
            strategy_name,
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
            quantity, fill_price, fill_value, filled_at, order_id, account,
            cost_basis, realized_pnl, strategy_params.
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
            date:           Date string (YYYY-MM-DD)
            total_trades:   Number of fills
            buys:           Number of BUY fills
            sells:          Number of SELL fills
            gross_buy:      Total USD value of BUY trades
            gross_sell:     Total USD value of SELL trades
            net_flow:       gross_sell - gross_buy (positive = net seller)
            realized_pnl:   Sum of realized_pnl for SELL fills (None if no cost data)
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
                SUM(CASE WHEN action='SELL' THEN fill_value ELSE 0 END) AS gross_sell,
                SUM(realized_pnl)                     AS realized_pnl
            FROM trades
            WHERE filled_at LIKE ?
        """

        with self._connect() as conn:
            row = conn.execute(query, (f"{day_str}%",)).fetchone()

        # fetchone() returns None when the table is empty or no rows match.
        if row is None:
            row = (0, 0, 0, 0.0, 0.0, None)

        total = row[0] or 0
        buys = row[1] or 0
        sells = row[2] or 0
        g_buy = row[3] or 0.0
        g_sell = row[4] or 0.0
        pnl = float(row[5]) if row[5] is not None else None

        return {
            "date": day_str,
            "total_trades": total,
            "buys": buys,
            "sells": sells,
            "gross_buy": round(g_buy, 2),
            "gross_sell": round(g_sell, 2),
            "net_flow": round(g_sell - g_buy, 2),
            "realized_pnl": round(pnl, 2) if pnl is not None else None,
        }

    def count(self) -> int:
        """Return total number of recorded trades."""
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]

    def realized_pnl_since(
        self,
        strategy_name: str,
        cutoff_iso: str,
    ) -> float:
        """
        Sum of `realized_pnl` for SELL fills attributed to `strategy_name`
        with `filled_at >= cutoff_iso` (lexical compare). Returns 0.0 when no
        rows match or all matching rows have NULL `realized_pnl`.

        Args:
            strategy_name: Strategy name as recorded by `record(...)`.
            cutoff_iso:    ISO-8601 timestamp (typically the most recent
                           9:30 ET market open, in UTC ISO form). Lexical
                           compare is safe because `submitted_at` is always
                           UTC-aware (`datetime.now(timezone.utc).isoformat()`)
                           — see `models/order.py:OrderResult.submitted_at`.
                           A `substr(filled_at, 1, 19)` form would be more
                           robust if non-UTC offsets ever appeared.

        Used by MS-A2: per-strategy P&L attribution for `RiskManager`.
        Pre-A1 fills with NULL `realized_pnl` are treated as 0 by `SUM`,
        which is correct — they had no cost_basis so we can't attribute.
        Caller (PnLPoller) logs a one-time WARNING if NULLs exist in the
        current trading day so the gap is observable.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(realized_pnl), 0) "
                "FROM trades "
                "WHERE strategy_name = ? AND filled_at >= ? AND action = 'SELL'",
                (strategy_name, cutoff_iso),
            ).fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0

    def count_null_pnl_since(
        self,
        strategy_name: str,
        cutoff_iso: str,
    ) -> int:
        """
        Number of SELL rows for `strategy_name` since `cutoff_iso` that have
        NULL `realized_pnl`. Used by PnLPoller to surface a one-time WARNING
        when pre-A1 fills sit inside today's window — the per-strategy sum
        will silently under-count those trades.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM trades "
                "WHERE strategy_name = ? AND filled_at >= ? "
                "AND action = 'SELL' AND realized_pnl IS NULL",
                (strategy_name, cutoff_iso),
            ).fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE)
            # Safe migrations: add Sprint 4 columns to existing databases.
            # SQLite raises OperationalError if the column already exists — we
            # catch and ignore that to make _init_db() idempotent.
            for migration in _MIGRATIONS:
                try:
                    conn.execute(migration)
                except sqlite3.OperationalError:
                    pass  # column already exists in this DB
            for idx_sql in _INDEXES:
                conn.execute(idx_sql)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")  # safe for concurrent readers
        return conn
