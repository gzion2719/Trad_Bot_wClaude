# TradeBot — Project Task Tracker

Legend: `[ ]` pending · `[x]` done · `[~]` in progress · `[!]` blocked

---

## Sprint 1 — Foundation (current)

| # | Status | Priority | Task |
|---|--------|----------|------|
| 1.1 | [x] | P0 | Connect to IBKR TWS via API (`ib_insync`) |
| 1.2 | [x] | P0 | Paper trading account verified |
| 1.3 | [x] | P0 | `IBKRClient` — connection, market data, contract qualification |
| 1.4 | [x] | P0 | `OrderManager` — place, cancel, deduplicate, event callbacks |
| 1.5 | [x] | P0 | Real-time TWS sync (catches external/manual order changes) |
| 1.6 | [x] | P0 | Delayed market data mode auto-set for paper accounts |
| 1.7 | [x] | P1 | Structured logging (console + rotating file) |
| 1.8 | [x] | P1 | Data models (`OrderRequest`, `OrderResult`, `Position`) |
| 1.9 | [x] | P1 | Project structure, README, .gitignore, .env.example |
| 1.10 | [x] | P1 | `main.py` entry point with event loop |
| 1.11 | [x] | P1 | `BaseStrategy` abstract class |
| 1.12 | [ ] | P0 | Set up Git repo → github.com/gzion2719/Trad_Bot_wClaude |
| 1.13 | [ ] | P0 | `CLAUDE.md` — full context handoff for new Claude sessions |
| 1.14 | [ ] | P1 | Review and improve all documentation |
| 1.15 | [x] | P1 | Define and document test plan → see `TEST_PLAN.md` |
| 1.16 | [ ] | P1 | Execute test plan, log bugs by severity |

---

## Sprint 2 — Stability & Risk

| # | Status | Priority | Task |
|---|--------|----------|------|
| 2.1 | [ ] | P0 | Auto-reconnect on TWS disconnect (with backoff) |
| 2.2 | [ ] | P0 | Risk manager — max position size, max daily loss, max open orders |
| 2.3 | [ ] | P1 | Position sizing module (fixed, % of equity, Kelly criterion) |
| 2.4 | [ ] | P1 | Heartbeat / health check (detect stale connection) |
| 2.5 | [ ] | P1 | Config validation on startup (catch bad settings early) |
| 2.6 | [ ] | P2 | Virtual environment setup instructions (`venv`) |
| 2.7 | [ ] | P2 | Alert system — notify on fill, error, daily loss breach (email/Slack) |

---

## Sprint 3 — Data & Backtesting

| # | Status | Priority | Task |
|---|--------|----------|------|
| 3.1 | [ ] | P0 | Live data feed — real-time price streaming per symbol |
| 3.2 | [ ] | P0 | Historical data loader (CSV + `yfinance`) |
| 3.3 | [ ] | P0 | Backtesting engine — replay OHLCV data through strategy |
| 3.4 | [ ] | P1 | Performance metrics — Sharpe, max drawdown, win rate, P&L curve |
| 3.5 | [ ] | P1 | Trade history persistence (SQLite or CSV) |
| 3.6 | [ ] | P2 | Paper trading simulation mode (no TWS required) |

---

## Sprint 4 — Strategies

| # | Status | Priority | Task |
|---|--------|----------|------|
| 4.1 | [ ] | P0 | Discuss and select first strategy |
| 4.2 | [ ] | P0 | Implement and backtest strategy #1 |
| 4.3 | [ ] | P1 | Implement and backtest strategy #2 |
| 4.4 | [ ] | P2 | Strategy parameter management (config-driven, no code changes) |
| 4.5 | [ ] | P2 | Multi-strategy runner (run multiple strategies simultaneously) |

---

## Sprint 5 — Deployment

| # | Status | Priority | Task |
|---|--------|----------|------|
| 5.1 | [ ] | P1 | Hostinger VPS setup guide |
| 5.2 | [ ] | P1 | IB Gateway (headless) setup on VPS |
| 5.3 | [ ] | P1 | Process manager (`systemd` or `supervisor`) for auto-restart |
| 5.4 | [ ] | P2 | Monitoring dashboard (simple web UI or Grafana) |
| 5.5 | [ ] | P2 | CI/CD pipeline (auto-run tests on push) |

---

## Bugs & Improvements Log

| # | Severity | Description | Status |
|---|----------|-------------|--------|
| — | — | *No bugs logged yet — test plan pending* | — |

---

## Sprint 6 — Intelligence & Tooling

| # | Status | Priority | Task |
|---|--------|----------|------|
| 6.1 | [ ] | P1 | Research best MCP servers / APIs for live and historical market data (Polygon.io, Alpaca, FMP, yfinance) |
| 6.2 | [ ] | P1 | Research best sources for trading logic: books, papers, quant blogs (identify top 5–10 references) |
| 6.3 | [ ] | P2 | Evaluate connecting Claude to a financial data MCP for real-time reasoning during sessions |
| 6.4 | [ ] | P2 | Build a reference doc (`RESOURCES.md`) with vetted sources for strategies, risk management, and market microstructure |

---

## Notes

- **Priority:** P0 = must have · P1 = should have · P2 = nice to have
- **Severity (bugs):** S1 = critical · S2 = major · S3 = minor
- Update this file at the start of every session
- See `CLAUDE.md` for full context when starting a new Claude session
