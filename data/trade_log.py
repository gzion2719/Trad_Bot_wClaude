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
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Union

from models.order import OrderResult

logger = logging.getLogger(__name__)

_DEFAULT_DB = Path(__file__).parent.parent / "data" / "trades.db"


def _round_profit_factor(pf: Optional[float]) -> Optional[Union[float, str]]:
    """Round profit_factor for JSON output.

    FastAPI's default JSONResponse silently converts non-finite floats
    (`+inf`, `-inf`, `nan`) to `null` on the wire, which would render as
    "—" in the dashboard — losing the only-wins ("∞") signal entirely.
    Emit string sentinels for non-finite values; the dashboard renderer at
    dashboard.js _fmtProfitFactor() string-compares for "Infinity".

    Branches:
      None           → None              (no data)
      +inf           → "Infinity"        (only-wins; producer-reachable)
      -inf           → "-Infinity"       (forward-defensive; producer cannot reach)
      nan            → None              (forward-defensive; producer cannot reach)
      finite float   → round(pf, 3)
    """
    if pf is None:
        return None
    if math.isnan(pf):
        return None
    if math.isinf(pf):
        return "Infinity" if pf > 0 else "-Infinity"
    return round(pf, 3)


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

    def lifetime_summary(self, strategy_name: str) -> Dict:
        """
        Aggregate lifetime KPIs for a single strategy.

        Returns a dict with:
            strategy_name:           Echoed for convenience.
            total_fills:             Total BUY + SELL rows for this strategy.
            sells:                   Number of SELL rows.
            sells_with_basis:        SELL rows with non-NULL cost_basis (count
                                     that contributes to win-rate / PF / P&L).
            legacy_null_basis_sells: SELL rows with NULL cost_basis. Surfaces
                                     pre-MS-A1 (2026-05-09) fills excluded
                                     from realized_pnl aggregates.
            realized_pnl_lifetime:   SUM(realized_pnl) across all SELL rows.
                                     None when no SELL rows have realized_pnl.
            wins:                    SELL rows with realized_pnl > 0.
            losses:                  SELL rows with realized_pnl < 0.
            gross_profit:            SUM(realized_pnl) for winners. 0.0 if none.
            gross_loss:              ABS(SUM(realized_pnl)) for losers. 0.0 if none.
            win_rate:                wins / (wins + losses), or None when 0 closed.
            profit_factor:           gross_profit / gross_loss, None when no
                                     losses AND no wins; the string "Infinity"
                                     when wins exist but no losses (string
                                     sentinel — FastAPI's default JSON encoder
                                     converts float('inf') to null on the wire,
                                     so the dashboard would render "—" instead
                                     of "∞"; see _round_profit_factor).
            avg_r_multiple:          AVG(real_r_multiple) over non-NULL rows.
                                     None when no rows have real_r_multiple set.
            r_multiple_count:        Denominator for `avg_r_multiple`.
            last_fill_at:            ISO timestamp of the most recent fill, or None.
        """
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_fills,
                    SUM(CASE WHEN action='SELL' THEN 1 ELSE 0 END) AS sells,
                    SUM(CASE WHEN action='SELL' AND cost_basis IS NOT NULL THEN 1 ELSE 0 END)
                        AS sells_with_basis,
                    SUM(CASE WHEN action='SELL' AND cost_basis IS NULL THEN 1 ELSE 0 END)
                        AS legacy_null_basis_sells,
                    SUM(CASE WHEN action='SELL' THEN realized_pnl ELSE NULL END)
                        AS realized_pnl_lifetime,
                    SUM(CASE WHEN action='SELL' AND realized_pnl > 0 THEN 1 ELSE 0 END)
                        AS wins,
                    SUM(CASE WHEN action='SELL' AND realized_pnl < 0 THEN 1 ELSE 0 END)
                        AS losses,
                    SUM(CASE WHEN action='SELL' AND realized_pnl > 0 THEN realized_pnl ELSE 0 END)
                        AS gross_profit,
                    SUM(CASE WHEN action='SELL' AND realized_pnl < 0 THEN realized_pnl ELSE 0 END)
                        AS gross_loss_negative,
                    AVG(real_r_multiple) AS avg_r_multiple,
                    SUM(CASE WHEN real_r_multiple IS NOT NULL THEN 1 ELSE 0 END)
                        AS r_multiple_count,
                    MAX(filled_at) AS last_fill_at
                FROM trades
                WHERE strategy_name = ?
                """,
                (strategy_name,),
            ).fetchone()

        total_fills = int(row[0] or 0)
        sells = int(row[1] or 0)
        sells_with_basis = int(row[2] or 0)
        legacy_null = int(row[3] or 0)
        realized_pnl_lifetime = float(row[4]) if row[4] is not None else None
        wins = int(row[5] or 0)
        losses = int(row[6] or 0)
        gross_profit = float(row[7] or 0.0)
        gross_loss = abs(float(row[8] or 0.0))
        avg_r = float(row[9]) if row[9] is not None else None
        r_count = int(row[10] or 0)
        last_fill_at = row[11]

        closed = wins + losses
        win_rate = (wins / closed) if closed > 0 else None
        # profit_factor branches:
        #   no closed trades            → None (no data)
        #   only winners (gross_loss=0) → +inf, rewritten to the string "Infinity"
        #                                  by _round_profit_factor before going
        #                                  on the wire (FastAPI's default encoder
        #                                  turns float('inf') into null).
        #   only losers  (gross_profit=0) → None (NOT 0.0 — 0.00 in the UI is
        #                                  indistinguishable from "no data";
        #                                  None forces an explicit "—" render)
        #   mixed                       → gross_profit / gross_loss
        if closed == 0:
            profit_factor: Optional[float] = None
        elif gross_loss == 0.0:
            profit_factor = float("inf") if gross_profit > 0 else None
        elif gross_profit == 0.0:
            profit_factor = None
        else:
            profit_factor = gross_profit / gross_loss

        return {
            "strategy_name": strategy_name,
            "total_fills": total_fills,
            "sells": sells,
            "sells_with_basis": sells_with_basis,
            "legacy_null_basis_sells": legacy_null,
            "realized_pnl_lifetime": (
                round(realized_pnl_lifetime, 2) if realized_pnl_lifetime is not None else None
            ),
            "wins": wins,
            "losses": losses,
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "win_rate": round(win_rate, 4) if win_rate is not None else None,
            "profit_factor": _round_profit_factor(profit_factor),
            "avg_r_multiple": round(avg_r, 3) if avg_r is not None else None,
            "r_multiple_count": r_count,
            "last_fill_at": last_fill_at,
        }

    def realized_pnl_today(self, strategy_name: str, day_utc: Optional[datetime] = None) -> float:
        """
        Sum of realized_pnl for one strategy on a given UTC day (default today).

        Uses `filled_at LIKE 'YYYY-MM-DD%'` — same boundary as `daily_summary`.
        Returns 0.0 when no rows match or all matching rows have NULL pnl.
        """
        d = (day_utc or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(realized_pnl), 0) FROM trades "
                "WHERE strategy_name = ? AND action = 'SELL' AND filled_at LIKE ?",
                (strategy_name, f"{d}%"),
            ).fetchone()
        return round(float(row[0]) if row and row[0] is not None else 0.0, 2)

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
        # WAL is a database-level persistent mode; setting it here is idempotent
        # and only takes effect on the first connection in a fresh DB. The real
        # per-connection setting is busy_timeout — without it, a concurrent
        # writer raises `OperationalError: database is locked` immediately
        # instead of waiting. 5s is generous enough to absorb the longest
        # observed write (~ms) but short enough that a true deadlock surfaces.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @contextmanager
    def connection(self, *, row_factory: bool = False) -> Iterator[sqlite3.Connection]:
        """Public context-managed read connection with WAL + busy_timeout.

        Use this from any other process (e.g. the dashboard) that reads the
        TradeLog DB. Avoids reaching into the private `_db_path` attribute or
        opening raw `sqlite3.connect` calls that skip the busy_timeout pragma.

        Args:
            row_factory: When True, set `sqlite3.Row` so columns are addressable
                         by name. Default False for raw tuples.

        Example:
            with trade_log.connection(row_factory=True) as conn:
                rows = conn.execute("SELECT * FROM trades LIMIT 10").fetchall()
        """
        conn = self._connect()
        if row_factory:
            conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
