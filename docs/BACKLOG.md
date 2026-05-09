# TradeBot — Backlog

Categorized list of open items. Updated every 5 sessions during the hygiene review.
For sprint-by-sprint detail, see `TODO.md`. For the phased roadmap, see `docs/ROADMAP.md`.

---

## Dashboard — Phase 4+ (UI & Analytics)

| # | Priority | Item |
|---|----------|------|
| DB-P4-1 | P1 | Account balance card — live NetLiquidation + UnrealizedPnL from `/api/system` (extend backend to query `client.get_account_summary()`) + equity curve graph |
| DB-P4-2 | P1 | Recent fills filtered per strategy — add `strategy_name` column to fills table; allow switching strategy in the UI (prep for multi-strategy) |
| DB-P4-3 | P2 | Per-strategy analytics card — W/L ratio, total realized P&L, unrealized P&L, Sharpe, max drawdown, profit factor + equity curve graph per strategy |
| DB-P4-4 | P2 | UI redesign — rethink card layout, typography, and color system for a more professional look; consider sidebar nav for multi-strategy view |

---

## Gateway Console — Phase 2 polish (post-MVP)

| # | Priority | Item |
|---|----------|------|
| GC-1 | ✅ | DONE 2026-05-04: button is always visible in Controls card; clicking opens /console.html as a sized OS popup (window.open). Static regression test guards the popup-features string. |
| GC-2 | ✅ | DONE 2026-05-04: full 2FA login rehearsal completed via browser console; gateway logged in, bot reconnected. |
| GC-3 | P0 | Security review pass: re-audit /api/console/login rate limiter, step-up token expiry, lock idle timeout, audit-log completeness, CSP scope on /console.html |
| GC-4 | P1 | TLS for the dashboard so noVNC works without an SSH tunnel — Caddy or nginx in front of 8080 with self-signed cert (Tailscale) or Let's Encrypt + tailscale-cert. Removes the localhost-only secure-context workaround. |
| GC-5 | P2 | Console UI redesign — current page is a bare canvas + minimal header. Match the Mission Control look: header with status pill, footer hint, restyled step-up card, scaling indicator. |

---

## Infrastructure & Ops

| # | Priority | Item |
|---|----------|------|
| 5.7 | P2 | Monitoring dashboard (simple web UI or Grafana) |
| 5.9 | P1 | IBKR Trusted IP — add VPS IP `2.24.222.199` in account settings → Security → Trusted IPs |
| 5.16 | P1 | Send IBKR support inquiry: (a) switch from Interactive IL Key to push-notification IB Key? (b) any unattended weekly auth path for paper accounts? |
| 6.4 | P0 | Confirm bot recovers from Sunday 2FA reset (first test: 2026-05-03 ~09:00 IL time) |
| 6.7 | P2 | Research alternative market data APIs (Polygon.io, Alpaca, FMP) |

---

## Strategy & Backtesting

| # | Priority | Item |
|---|----------|------|
| 4.5 | P1 | Tune SMA parameters after 1+ week paper results — test sma_fast=20/sma_slow=50; validate on 2008/2022 bear regimes |
| 4.6 | P2 | Implement and backtest a second strategy |
| 4.7 | P2 | Strategy parameter management (YAML/JSON config, no code changes to switch params) |
| 4.8 | P1 | Multi-strategy runner — Decision B resolved 2026-05-06: **independent, 2% per strategy, each trade is separate**. Ready to implement when second strategy design is ready. |
| 6.1 | P0 | Monitor TradeLog.daily_summary() every trading day — check realized_pnl, trade count, fill quality |
| 6.2 | P0 | Verify fills at expected prices (compare backtest vs paper fills) |
| 6.8 | P2 | Build RESOURCES.md with vetted sources for strategies, risk management, market microstructure |
| M7 | P1 | Validate strategy on 2008/2022 bear regimes before going live |

---

## Risk & Monitoring

| # | Priority | Item |
|---|----------|------|
| 6.3 | P0 | Verify daily loss ceiling triggers correctly via simulated loss |
| 6.5 | P1 | Continue weekly log review for WARNING/ERROR patterns |
| 6.6 | P1 | Adjust risk caps (max_order, max_position, max_daily_loss) based on paper results |
| Q4 | P2 | If avg_cost == 0 on reconcile, defer `_in_position=True` until stop can be computed |
| Q6a | P2 | Consider auto-re-placing STOP in `_exit()` when SELL is rejected |
| 2.7 | P2 | Alert system (email/Slack) on fill, daily loss breach, and error codes |
| MS-A | P1 | Per-strategy P&L attribution — `PnLPoller` currently feeds account-level realized P&L to every RiskManager. With N>1 strategies registered, all strategies halt when account P&L hits any cap (not just the offender). Required before live; documented in `config/strategies.py` module docstring. (Surfaced 2026-05-09 CR finding HIGH-2.) |
| MS-B | P1 | RSI2MR `_strategy_peak_equity` contamination — `_get_equity()` returns `NetLiquidation` (account-wide), so the 8% strategy-peak drawdown circuit breaker can fire from another strategy's losses. State persisted to `data/rsi2_mr_state.json` carries this contamination across restarts. Fix: track strategy-attributed equity (initial_capital + sum of own realized P&L). (Surfaced 2026-05-09 CR finding MEDIUM-4.) |
| MS-C | P2 | RSI2MR yfinance dependency hardening — both SPY history (`_refresh_history`) and VIX (`VIXFeed.get_latest_close`) hit yfinance live. A yfinance outage skips the daily tick silently (only WARNING) and fires the VIX-stale alert. Add ntfy alert on N consecutive `_refresh_history` failures, OR add IBKR `reqHistoricalData` fallback. (Surfaced 2026-05-09 CR finding HIGH-1.) |
| MS-D | P0 | Hard guard against shared-symbol cross-strategy registration — `REGISTRY.build()` should raise `ConfigError` if any two `StrategyConfig` entries declare the same `symbol`. Today the bot mostly runs with shared symbols but breaks silently: position reconciliation lies (IBKR aggregates), `cancel_all(symbol)` cancels both strategies' orders, IBKR `avg_cost` fallback misattributes, P&L attribution under MS-A2 mis-counts. ~5-line guard; unblocks safety of MS-A1's `avg_cost` fallback. (Surfaced 2026-05-09 MS-A1 plan review.) |
| MS-F | P2 | Warn on state-file vs broker `avg_cost` disagreement during carry-over reconcile — when both a state file entry_price and a broker `pos.avg_cost` are present and they differ by >1%, log a `WARNING` so a hand-edited / corrupted / mis-paired state file can't silently produce wrong cost_basis on the next SELL. State still wins (it's the strategy's authority); the warning just makes the disagreement visible. (Surfaced 2026-05-09 MS-A1 second-pass CR.) |
| MS-E | P3 | Per-strategy logical position layer (enables shared-symbol cross-strategy) — proper fix for two strategies trading the same symbol: new `PositionLedger` mapping `(strategy, symbol) → shares`, rewrite of `OrderManager.get_positions` to split aggregate, restart-time reconciliation logic, ~30 tests. 1–2 weeks of work. Not needed today; adds optionality if user ever wants e.g. SMA + RSI both on QQQ. |

---

## Tooling & Code Quality

| # | Priority | Item |
|---|----------|------|
| 1.14 | P1 | Review and improve all documentation |
| 2.6 | P2 | Virtual environment setup docs update (Sprint 5.2 handled VPS; local Windows venv docs pending) |
| QA-15 | P2 | Delayed data staleness warning surfaced to strategies |
| QA-16 | P2 | Market hours check for DAY orders |
| 5.11 | P1 | Split test suite — mark IBKR-dependent tests with `requires_tws` so CI can run logic-only tests without TWS (today CI test step always fails because `tests/run_tests.py:94` connects at module load) |

---

## Owner Decisions Required

| # | Decision | Options |
|---|----------|---------|
| A | Live market data subscription (~$10–25/month via IBKR)? | **Yes** = real-time, works for intraday. **No** = 15-min delayed, fine for daily-bar strategies, free. |
| B | ✅ RESOLVED 2026-05-06 | **Independent** — each strategy gets its own 2% risk cap; trades are completely independent. |
