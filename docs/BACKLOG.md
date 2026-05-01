# TradeBot — Backlog

Categorized list of open items. Updated every 5 sessions during the hygiene review.
For sprint-by-sprint detail, see `TODO.md`. For the phased roadmap, see `docs/ROADMAP.md`.

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
| 4.8 | P2 | Multi-strategy runner — blocked on Decision B |
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

---

## Tooling & Code Quality

| # | Priority | Item |
|---|----------|------|
| 1.14 | P1 | Review and improve all documentation |
| 2.6 | P2 | Virtual environment setup docs update (Sprint 5.2 handled VPS; local Windows venv docs pending) |
| QA-15 | P2 | Delayed data staleness warning surfaced to strategies |
| QA-16 | P2 | Market hours check for DAY orders |

---

## Owner Decisions Required

| # | Decision | Options |
|---|----------|---------|
| A | Live market data subscription (~$10–25/month via IBKR)? | **Yes** = real-time, works for intraday. **No** = 15-min delayed, fine for daily-bar strategies, free. |
| B | Multi-strategy position behavior? | **Independent** = each strategy gets own position (default). **Combined** = shared cap, more complex. |
