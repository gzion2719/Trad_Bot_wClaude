# TradeBot — Roadmap

**Guiding principle:** phases end when they actually work, not on a calendar.
Paper trading runs for as long as needed — real money only after all Go/No-Go criteria pass.

---

## Phase 1 — Foundation ✅ COMPLETE

**Goal:** Connect to IBKR, place and cancel orders, basic logging and data models.

Key deliverables: IBKRClient, OrderManager, BaseStrategy, .env config, test plan.

**Acceptance check:** 40/40 tests passing, paper account connected, orders round-trip correctly.

---

## Phase 2 — Stability & Risk ✅ COMPLETE

**Goal:** Bot survives TWS daily restart; every order goes through a risk gate.

Key deliverables: ReconnectManager (backoff), RiskManager (plan_trade, 2% rule, 1:3 R/R), PositionSizer, ConfigValidator.

**Acceptance check:** Bot reconnects automatically after simulated disconnect; a trade that violates risk rules is rejected with a clear error.

---

## Phase 3 — Data & Backtesting ✅ COMPLETE

**Goal:** Historical data loading, backtesting engine, trade logging.

Key deliverables: DataFeed/IBKRFeed/BarScheduler, HistoricalDataLoader (yfinance/IBKR/CSV), BacktestEngine, BacktestMetrics, TradeLog (SQLite).

**Acceptance check:** Same strategy class runs in backtest and live without modification; backtest results are reproducible.

---

## Phase 4 — First Strategy ✅ MOSTLY COMPLETE

**Goal:** One strategy backtested, validated, and running on paper.

Key deliverables: SMA 10/30 crossover on QQQ daily bars, 4-round architect review, backtest (+36% return, 2.27 profit factor), wired into main.py with RiskManager caps.

**Remaining:**
- 4.5 [ ] Tune parameters after 1+ week paper results (sma_fast=20/sma_slow=50, validate on 2008/2022 bear regimes)
- 4.6 [ ] Implement and backtest a second strategy
- 4.7 [ ] Strategy parameter management (YAML/JSON config)
- 4.8 [!] Multi-strategy runner — blocked on Decision B

**Acceptance check:** Strategy profitable or near-breakeven on paper after 2–4 weeks; parameter tuning validated on historical bear regimes.

---

## Phase 5 — VPS Deployment ✅ MOSTLY COMPLETE

**Goal:** Bot running 24/7 on Hostinger VPS, auto-recovering from failures.

Key deliverables: systemd chain (xvfb → x11vnc → ibgateway → tradebot), IBC config, PnLPoller, Tailscale/UFW hardening, ntfy.sh health heartbeat.

**Remaining:**
- 5.7 [ ] Monitoring dashboard (Grafana or simple web UI)
- 5.8 [x] CI/CD pipeline — **now done (2026-05-01)**
- 5.9 [ ] IBKR Trusted IP — add VPS IP `2.24.222.199` in IBKR account → Security → Trusted IPs
- 5.16 [ ] IBKR support inquiry — ask about push 2FA for Israeli accounts

**Acceptance check:** Bot survives a simulated VPS reboot; bot survives TWS daily restart; alerts fire correctly via ntfy.sh.

---

## Phase 6 — Paper Trading Period ⬅ CURRENT PHASE

**Goal:** Run paper for 2–4 weeks, monitor fills and P&L, confirm all systems work before real money.

Tasks:
- 6.1 [ ] Monitor TradeLog.daily_summary() every trading day
- 6.2 [ ] Verify fills at expected prices (compare backtest vs paper fills)
- 6.3 [ ] Verify daily loss ceiling triggers correctly
- 6.4 [ ] Confirm bot recovers from TWS daily restart and Sunday 2FA (first test: 2026-05-03)
- 6.5 [~] Weekly log review for WARNING/ERROR patterns — info code noise fixed (PR #9)
- 6.6 [ ] Adjust risk limits based on paper results
- 6.7 [ ] Research alternative data sources (Polygon.io, Alpaca, FMP)
- 6.8 [ ] Build RESOURCES.md with vetted strategy/risk references

**Go/No-Go criteria before Phase 7:**
- [ ] Strategy profitable or near-breakeven over 2–4 weeks paper
- [ ] No unexpected crashes or missed fills
- [ ] Daily loss ceiling confirmed working
- [ ] Bot auto-recovers from TWS daily restart without intervention
- [ ] Risk limits reviewed and set conservatively for live

---

## Phase 7 — Go Live (Small Positions)

**Goal:** Switch to live account with minimal risk to verify everything works with real money.

Tasks:
- 7.1 [ ] Decision A: subscribe to IBKR live data (~$10–25/month)?
- 7.2 [ ] Change IB_PORT=7496 (live) — config validator will warn loudly
- 7.3 [ ] Set position size to 1–5 shares for first 2 weeks
- 7.4 [ ] Set max_daily_loss very conservatively (-$50) for first live run
- 7.5 [ ] Monitor live fills daily for first week
- 7.6 [ ] Gradually increase position size as confidence grows
- 7.7 [ ] Decision B: multi-strategy position caps — independent or combined?
- 7.8 [ ] Email/Slack alerts on fill, daily loss breach, error codes

**Acceptance check:** First live trade executes correctly at expected price; daily loss ceiling fires correctly on a manually injected loss; no unexpected behavior after 1 week live.
