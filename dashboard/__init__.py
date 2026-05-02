"""Read-only mission control dashboard for TradeBot.

Phase 1 of ROADMAP 5.7. Exposes telemetry only — no kill/restart and no IB Gateway
login surface. Those are tracked separately in BACKLOG.

The dashboard runs as a separate process (`tradebot-dashboard.service`) so a crash
in the dashboard cannot affect the live bot, and vice versa. All data comes from
sources the bot already trusts:

  * TradeLog (SQLite WAL — safe for concurrent readers across processes)
  * data/health.txt (UTC ISO timestamp written by SMACrossover.on_tick)
"""
