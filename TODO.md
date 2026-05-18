# TradeBot — Project Task Tracker

Legend: `[ ]` pending · `[x]` done · `[~]` in progress · `[!]` blocked

---

## Sprint 1 — Foundation ✅ COMPLETE

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
| 1.12 | [x] | P0 | Git repo → github.com/gzion2719/Trad_Bot_wClaude |
| 1.13 | [x] | P0 | `CLAUDE.md` — full context handoff for new Claude sessions |
| 1.14 | [ ] | P1 | Review and improve all documentation |
| 1.15 | [x] | P1 | Define and document test plan → `TEST_PLAN.md` |
| 1.16 | [x] | P1 | Execute test plan — 40/40 passing (Run 4, 2026-04-10) |

---

## QA Audit Fixes ✅ COMPLETE (both rounds)

### Round 1 — 25 issues (all Critical + High fixed)

| # | Status | Issue |
|---|--------|-------|
| QA-01 | [x] | Race condition: `sleep(0.5)` before cache write reduces window |
| QA-02 | [x] | Thread safety: `threading.Lock` on `self._orders` |
| QA-03 | [x] | `connect()` waits for account state before returning |
| QA-04 | [x] | Dead code in `_best_price()` — midpoint fallback fixed |
| QA-05 | [x] | Heartbeat via `is_alive()` / `reqCurrentTime()` |
| QA-06 | [x] | `connect()` failure caught + retry with backoff in `main.py` |
| QA-07 | [x] | Market data polling loop with timeout instead of fixed sleep |
| QA-08 | [x] | Live port (7496) warning logged loudly on connect |
| QA-09 | [ ] | No risk management (addressed in Sprint 2.2) |
| QA-10 | [x] | `get_positions()` and `get_open_orders()` guard on is_connected |
| QA-11 | [x] | `avg_fill_price` returns None (not 0.0/NaN) for unfilled orders |
| QA-12 | [x] | Error 202 moved to `_WARNING_CODES`, cache updated via events |
| QA-13 | [x] | `submitted_at` set at `place_order()` time, not object creation |
| QA-14 | [x] | `qualify_contract()` prefers `primaryExchange` |
| QA-15 | [ ] | Delayed data staleness warning to strategies (Sprint 3.1) |
| QA-16 | [ ] | Market hours check for DAY orders (Sprint 2.5) |
| QA-17 | [ ] | Backtester stubs marked WIP (Sprint 3.3) |
| QA-18 | [x] | Fractional quantity warning in `OrderRequest.__post_init__` |

### Round 2 — 13 issues (all fixed 2026-04-10)

| # | Status | Issue |
|---|--------|-------|
| R2-01 | [x] | `get_positions()` was hardcoded 0.0 — now reads real IBKR values via `ib.portfolio()` |
| R2-02 | [x] | `_handle_order_status()` double-lock race eliminated — single lock block |
| R2-03 | [x] | `_handle_cancel_order()` snapshots Trade inside lock |
| R2-04 | [x] | `get_market_price()` try/finally guarantees `cancelMktData()` on exception |
| R2-05 | [x] | `sync()` calls `openTrades()` before acquiring lock |
| R2-06 | [x] | Unknown IBKR status logged as WARNING; `PendingCancel` added to enum |
| R2-07 | [x] | Error codes 502/503/504 classified as connection errors, forwarded to callbacks |
| R2-08 | [x] | `_clear_callbacks()` added; called in tests before each callback registration |
| R2-09 | [x] | `Position.fetched_at` timestamp added |
| R2-10 | [x] | Startup test cleanup reports count and waits appropriately |
| R2-11 | [x] | `connect()` retry loop uses `total_attempts` variable, logs "attempt N/M" |
| R2-12 | [x] | Tests use `logging.disable(INFO)` — WARNING/ERROR/CRITICAL visible |
| R2-13 | [x] | `_best_price(ticker: Ticker)` type hint added |

---

## Sprint 2 — Stability & Risk ✅ COMPLETE

| # | Status | Priority | Task |
|---|--------|----------|------|
| 2.1 | [x] | P0 | `ReconnectManager` — daemon thread, backoff [5,10,30,60,120]s, `sync()` after reconnect, strategies pause via `wait_for_connection()` |
| 2.2 | [x] | P0 | `RiskManager` — per-order cap, per-symbol exposure, daily loss ceiling, `plan_trade()` (validate + size atomically), `validate_setup()` supports longs and shorts |
| 2.3 | [x] | P1 | `PositionSizer` — `risk_based()` (primary, 2% rule), `fixed()`, `percent_of_equity()`, `kelly()` |
| 2.4 | [x] | P1 | Heartbeat — `is_alive()` via `reqCurrentTime()` |
| 2.5 | [x] | P1 | `validate_config()` + `ConfigError` — called first in `main()`, warns loudly on live port |
| 2.6 | [ ] | P2 | Virtual environment setup — `docs/setup.md` (deferred to Sprint 5.2) |
| 2.7 | [ ] | P2 | Alert system (email/Slack) — deferred to Sprint 6+ |

**New file layout for Sprint 2:**
```
risk/
  __init__.py
  risk_manager.py      # RiskManager, RiskViolationError
  position_sizer.py    # PositionSizer (static)
broker/
  reconnect.py         # ReconnectManager (NEW)
config/
  validator.py         # validate_config(), ConfigError (NEW)
docs/
  setup.md             # venv instructions (NEW)
```

**Updated `main.py` wiring order:**
1. `validate_config()`
2. `client = IBKRClient()`
3. `client.connect(retries=3)`
4. `om = OrderManager(client)`
5. `rm = RiskManager(client, ...)`
6. `reconnect = ReconnectManager(client, om)`
7. `reconnect.start()`
8. `om.on_fill(rm.record_fill)`
9. Load and start strategy

**Updated `BaseStrategy.__init__` signature:**
```python
def __init__(self, client, order_manager, risk_manager, reconnect)
```
Typical `on_tick()` guard at top:
```python
self.reconnect.wait_for_connection(timeout=30)
if self.risk_manager.is_halted(): return
```

---

## Sprint 3 — Data & Backtesting ✅ COMPLETE

| # | Status | Priority | Task |
|---|--------|----------|------|
| 3.1 | [x] | P0 | `DataFeed` / `IBKRFeed` / `BarScheduler` — abstract feed, 5-sec real-time bars, timer-driven `on_tick()`, clean subscribe/unsubscribe |
| 3.2 | [x] | P0 | `HistoricalDataLoader` — `load_yfinance()`, `load_ibkr()` (11s rate limit), `load_csv()` |
| 3.3 | [x] | P0 | `BacktestEngine` + `MockOrderManager` + `BacktestDataFeed` — same strategy class runs live and in backtest, fills at next-bar open (no look-ahead bias) |
| 3.4 | [x] | P1 | `backtester/metrics.py` — `sharpe_ratio()`, `max_drawdown()`, `win_rate()`, `profit_factor()`, `summary()` |
| 3.5 | [x] | P1 | `TradeLog` (SQLite WAL) — `record()`, `get_history()`, `daily_summary()`, schema: cost_basis, realized_pnl, strategy_params |
| 3.6 | [x] | P2 | Paper simulation covered by BacktestEngine |

**New file layout for Sprint 3:**
```
data/
  __init__.py
  bar.py           # Bar dataclass
  feed.py          # DataFeed (abstract), IBKRFeed, BarScheduler
  historical.py    # HistoricalDataLoader
  trade_log.py     # TradeLog (SQLite)
backtester/
  __init__.py
  engine.py        # BacktestEngine, MockOrderManager
  portfolio.py     # BacktestPortfolio
  metrics.py       # pure metric functions
```

---

## Sprint 4 — Implement Strategy
### Goal: Pick a strategy → backtest it → run it on paper

| # | Status | Priority | Task |
|---|--------|----------|------|
| 4.1 | [x] | P0 | Select first strategy — SMA 10/30 crossover on QQQ daily bars |
| 4.2 | [x] | P0 | Implement `strategies/sma_crossover.py` — 4 rounds of architect review. Round 4 verdict: GO. Post-GO hardening: Q2 (cancel any SELL on restart, not just STOP), Q3 (floor fallback stop at min(avg_cost, latest_close)*0.97), Q4/Q6a (on_tick re-arm broker STOP when _stop_order_id is None), log escalation (rejected-SELL → ERROR). |
| 4.3 | [x] | P0 | Backtest QQQ 2020-2024 (`backtests/backtest_sma_qqq.py`): +36% return, 2.27 profit factor, -12.7% max DD, 45% win rate. All fixes are live-only, backtest numbers unchanged. |
| 4.4 | [~] | P0 | Wire SMACrossover into main.py — DONE: RiskManager caps (120k/100k/-2k), daily scheduler at 16:10 ET, TradeLog wired via om.on_fill. Run on paper >= 1 week. |
| 4.5 | [ ] | P1 | Tune strategy parameters based on backtest + paper results |
| 4.6 | [x] | P2 | Implement and backtest a second strategy — RSI2-MR SPY done 2026-05-08 (PR #147). 67 trades 2006-2025, 59.7% win, Sharpe 0.34, DD -8.5%. Not registered in REGISTRY yet — paper-only branch until promoted. |
| 4.7 | [ ] | P2 | Strategy parameter management (YAML/JSON config, no code changes to switch params) |
| 4.8 | [x] | P2 | Multi-strategy runner — Phase A done 2026-05-07: `config/strategies.REGISTRY` + `runtime/StrategyRunner`, per-strategy RiskManager + scheduler (DailyAt/Interval), fills routed via `OrderResult.strategy_name`. Phase B done 2026-05-09: RSI2MR-SPY added to REGISTRY (symbol=SPY, DailyAt(16,10), all default params). VIX sidecar is self-managed by the strategy — no StrategyRunner changes needed. |
| 4.8.1 | [x] | P1 | MS-A1 (cost_basis pipeline) done 2026-05-09: strategies stamp `OrderResult.cost_basis` on SELL from `_entry_price`; per-strategy JSON state files persist entry across restarts; broker `avg_cost` fallback for clean installs; callback-ordering contract documented in `runtime/strategy_runner.py` and tested both directions. 15 new tests in `tests/test_pnl_attribution_a1.py`. Pure plumbing — no production behavior change yet. |
| 4.8.2 | [x] | P1 | MS-A2 done 2026-05-09: PnLPoller queries TradeLog per strategy (replaced account-level RealizedPnL feed). RiskManager gains `strategy_name`, sticky `_halted_today` flag (cleared only by `reset_daily()` at 9:30 ET). `is_halted()` and `check()` both honor the sticky flag — no path can place orders after a same-day halt. Initial sync refresh on startup so strategies don't tick blind for the first 60s. ET-trading-day cutoff (DST-safe). Index `idx_trades_strategy_filled` added. 14 new tests in `tests/test_pnl_attribution_a2.py` including the bug-of-record assertion (one strategy halts, the other keeps trading). |
| 4.8.3 | [x] | P0 | MS-D done 2026-05-09: `config/strategies._validate_registry()` raises `ConfigError` at module load on empty registry, blank/duplicate names, or shared symbols (case-insensitive). `StrategyRunner._validate_registry()` delegates to the same function — single source of truth. 6 tests (MS-12a..e + updated MS-01/02). Comments at `sma_crossover.py:177` and `rsi2_mr.py:680` now reflect actual guard location. |
| 4.8.4 | [x] | P1 | MS-B done 2026-05-10: `RSI2MR_SPY._get_strategy_attributed_equity()` returns `initial_capital + own realized P&L (TradeLog) + unrealized on open position`, used at the two CB sites (peak ratchet + 8% drawdown trip). Position sizing still uses broker NetLiq. State schema bumped to v2 with one-shot reset of contaminated `strategy_peak_equity`/`circuit_breaker_until`; eager-save inside the migration block so v2 lands on disk immediately. Verified live on VPS: state file at `schema_version: 2`. |
| 4.8.5 | [x] | P1 | MS-K done 2026-05-10 (in same PR as MS-B): trip-on-detect guard for partial SELL. New `_partial_fill_halt` flag halts both entries AND exits (existing CB only halts entries — would have naked-shorted dangling shares). Float-tolerant compare; CB + ntfy defense-in-depth. Persisted in v2 state. Proper partial-fill handling deferred to MS-K-full (BACKLOG P3). |

---

## Sprint 5 — Wire Risk + Deploy to VPS
### Goal: Activate the daily loss ceiling, set up venv, deploy to Hostinger

| # | Status | Priority | Task |
|---|--------|----------|------|
| 5.1 | [x] | P0 | `PnLPoller` daemon — wired and active in `main.py`. Calls `reset_daily()` at 9:30 AM ET, polls `update_daily_pnl()` every 60s, shuts down cleanly on exit |
| 5.2 | [x] | P0 | Set up Python virtual environment — handled by `deploy/setup.sh` on VPS |
| 5.3 | [x] | P0 | Hostinger VPS provisioned — Ubuntu 24.04 LTS, KVM 1, US Boston 2, IP 2.24.222.199 |
| 5.4 | [x] | P0 | IBC (headless IB Gateway) setup — `deploy/ibc/config.ini` + `deploy/ibc/start_ibgateway.sh` created. Run `deploy/setup.sh` on VPS to install. |
| 5.5 | [x] | P0 | `systemd` units created — `ibgateway.service`, `tradebot.service`, `tradebot-notify@.service`, `tradebot-health.service/.timer`. All in `deploy/systemd/`. |
| 5.6 | [x] | P1 | Health heartbeat — `on_tick()` writes UTC timestamp to `data/health.txt`; `tradebot-health.timer` checks every 2h, notifies via ntfy.sh if stale >26h |
| 5.7 | [x] | P2 | Monitoring dashboard — **Phase 1** done 2026-05-02. **Phase 2** done 2026-05-02. **Phase 3** (control plane) done 2026-05-02, deployed/verified 2026-05-03. **Phase 4** (IBKR Account tab) done + deployed 2026-05-06: KPI strip (Cash/Unrealized/Realized), equity chart, positions + balances; `AccountSnapshotPoller` every 30s. **GC-3 console security review** done 2026-05-06: H-1 (CSP wildcard ws→'self'), M-1/M-2 (rate-limit/audit on WS rejections), M-3 (release session-only), M-4 (re-issue revokes prior token), + 3 post-review findings (F1/F2/F6). 4 new tests. PRs #119/#120 merged. **Phase 5 — Per-strategy view, Session 1** done 2026-05-12: `/api/strategies`, `/api/strategies/{name}/summary`, `/api/strategies/{name}/fills` endpoints with path-safety + 30s TTL cache; `config/strategy_metadata.py` extracted so dashboard imports class-free metadata only; Mission Control's Recent Fills gains a Strategy column; `TradeLog.lifetime_summary()` + public `connection()` context manager (`busy_timeout=5000`). 30 tests `test_ds01..26` + 2 unbiased CR rounds. **Session 2** done 2026-05-13: Strategies top tab + dynamic secondary tab strip + per-strategy KPI strip (UI-only on Session 1's endpoints), N-tab keyboard refactor, sessionStorage persistence with list validation, lazy first-activation init, 30s polling gated by `_onStratTab && document.visibilityState`, `aria-live` KPI region. **DB-path bug bundled** in the same PR — `dashboard/app.py` was reading `data/trades.db` while `main.py` writes to `data/paper_trades.db`; the dashboard had been showing empty data on VPS the whole time. New `test_ds27` URL-drift tripwire catches `console/lock/release`-class typos permanently. Pre-impl CR yielded 13 findings; all material findings folded in pre-code. FastAPI `+inf → null` fix shipped 2026-05-14 (string-sentinel). **Session 3a** done 2026-05-15: three stacked PRs — (a) DB-X5 shared TestClient auth fixtures + ds50..54 (401 paths); (b) per-strategy paginated history table consuming existing `/fills` (single AbortController, decoupled from 30s poll, Next disabled at 10k offset cap, ds60..68); (c) `chore/cr-cycle-tracker-3b` CR follow-ups (db09-15 → monkeypatch, `_js_decl_end` helper, ds61 operator regex, ds69 thead-parity guard). 326 tests pass; 6 CR rounds. Second-opinion agent overturned bundled-3b → CSV deferred to Session 3c. **Session 3c** done 2026-05-16: `?format=csv` content-negotiation on `/api/strategies/{name}/fills` — buffered CSV (not streamed), server-side `_CSV_COLUMNS` mirrors the JS `_STRAT_HISTORY_COLS` constant, 100k row cap → HTTP 413, formula-injection guard on string cells only, UTF-8 BOM + RFC 4180 CRLF + RFC 6266 filename + `Cache-Control: no-store`, dependency order swapped for 401→404→400 precedence, frontend `<a id="strat-export" download>`; pre- + post-impl CR; `test_ds70..79`; 336 tests; commit `959bb38`. Read side of Phase 5 complete. **GC-4** (TLS/Caddy) still pending. |
| 5.8 | [x] | P2 | CI/CD pipeline (auto-run tests on push to GitHub) — `.github/workflows/ci.yml` added 2026-05-01; file was gitignored until code-review fix 2026-05-02 (PR `feature/restore-ci-workflow`) |
| 5.9 | [x] | P1 | IBKR Trusted IP whitelist — **CLOSED: won't do.** Account-level IP Restrictions allows only one IP per user; adding VPS would block home PC access. Takes a business day to change. Gateway API Trusted IPs (different feature) already set to 127.0.0.1 in IBC config — no action needed. |
| 5.10 | [x] | P0 | VPS deployment debugged — IBC empty password fixed, Read-Only API unchecked + `ReadOnlyApi=no` in config, 2FA loop resolved via `ExistingSessionDetectedAction=manual`, `UseSSL=yes` added |
| 5.11 | [x] | P0 | Risk caps updated for QQQ paper account — max_order=$120k, max_position=$100k, max_daily_loss=-$2,000. Merged via PR. |
| 5.12 | [x] | P0 | VPS hardened — Tailscale installed, UFW blocks port 22, SSH only via `ssh chappy-vps` (Tailscale IP). `chappy` user replaces root. CLAUDE.md updated. |
| 5.13 | [x] | P1 | Hybrid Git Flow implemented — main/develop/feature/hotfix branches, PR-only policy, Claude enforcement rules added to CLAUDE.md. `develop` branch created and synced. |
| 5.14 | [x] | P0 | **IB Gateway under systemd** (2026-04-30). Created `xvfb.service`, `x11vnc.service`, `ibgateway.service` — all enabled, chained via `Requires=`/`After=`. Replaces the old backgrounded/disowned IBC process. Recovered bot from 6-day outage caused by Sunday Apr 26 token reset with no human to enter 2FA. |
| 5.15 | [x] | P1 | **IBC config: 2FA timeout recovery** (2026-04-30). Added `ReloginAfterSecondFactorAuthenticationTimeout=yes` to `/opt/ibc/config.ini` so IBC re-prompts instead of sitting silently after a 2FA timeout. |
| 5.16 | [ ] | P1 | **IBKR support inquiry — push 2FA for Israeli account.** Owner is on Interactive IL Key (code generator). Ask: (a) can we switch to push-notification IB Key? (b) any unattended weekly auth path for paper? Draft email is in CLAUDE.md / Obsidian handoff. |

**VPS readiness checklist (must all be done before going live):**

| Requirement | Status | Sprint |
|---|---|---|
| Config validation (no accidental live port) | ✅ Done | 2.5 |
| RiskManager with daily loss ceiling | ✅ Active — PnLPoller wired in main.py | 2.2 / 5.1 |
| Auto-reconnect + strategy pause during gap | ✅ Done | 2.1 |
| SIGTERM handler for clean systemd shutdown | ✅ Done | Sprint 4 pre-flight |
| Virtual environment | ✅ Done — handled by deploy/setup.sh on VPS | 5.2 |
| IBC (headless IB Gateway on VPS) | ✅ Running under systemd (`ibgateway.service`) since 2026-04-30 | 5.4 / 5.14 |
| systemd process supervisor | ✅ Full chain: xvfb → x11vnc → ibgateway → tradebot. All enabled, all auto-start on boot. | 5.5 / 5.14 |
| Strategy backtested and validated | ✅ Done — QQQ SMA backtest 4.3 | 4.3 |
| Strategy paper-traded and monitored | [~] In progress — bot live on VPS paper account | 4.4 |

---

## Sprint 6 — Paper Trading Period
### Goal: Run paper for 2–4 weeks, monitor fills and P&L, fix issues before going live

| # | Status | Priority | Task |
|---|--------|----------|------|
| 6.1 | [ ] | P0 | Monitor `TradeLog.daily_summary()` every trading day — check realized_pnl, trade count, fill quality |
| 6.2 | [ ] | P0 | Verify fills are happening at expected prices (compare backtest vs paper fills) |
| 6.3 | [ ] | P0 | Verify daily loss ceiling triggers correctly if a simulated loss is fed via `update_daily_pnl()` |
| 6.4 | [ ] | P0 | Check reconnect behaviour — confirm bot recovers cleanly after TWS daily restart (~11:45 PM EST). **Note:** Mon–Sat restarts use cached token (no 2FA). Sunday ~01:00 ET requires owner to enter fresh 2FA via VNC — see CLAUDE.md "Weekly 2FA cadence" section. First test: Sunday 2026-05-03. |
| 6.5 | [~] | P1 | Review logs weekly — look for WARNING/ERROR patterns (6.C done: IBKR info codes 1100/1102/2103/2105/2107/2157 demoted from ERROR to INFO/WARNING in order_manager.py — PR #9 merged to develop) |
| 6.6 | [ ] | P1 | Adjust `max_order_value`, `max_position_value`, `max_daily_loss` limits based on paper results |
| 6.7 | [ ] | P2 | Research best MCP servers / APIs for live and historical market data (Polygon.io, Alpaca, FMP) |
| 6.8 | [ ] | P2 | Build `RESOURCES.md` with vetted sources for strategies, risk management, market microstructure |

**Go/No-Go criteria before Sprint 7 (live trading):**
- [ ] Strategy profitable or near-breakeven over 2–4 weeks on paper
- [ ] No unexpected crashes or missed fills
- [ ] Daily loss ceiling confirmed working
- [ ] Bot auto-recovers from TWS daily restart without manual intervention
- [ ] Risk limits reviewed and set conservatively for live

---

## Sprint 7 — Go Live (Small Position Sizes)
### Goal: Switch to live account with minimal risk to verify everything works with real money

| # | Status | Priority | Task |
|---|--------|----------|------|
| 7.1 | [ ] | P0 | Decision A: subscribe to IBKR live market data (~$10–25/month)? Required for intraday; optional for daily-bar strategies |
| 7.2 | [ ] | P0 | Change `.env` `IB_PORT=7496` (live) — config validator will warn loudly |
| 7.3 | [ ] | P0 | Set position size to minimum (1–5 shares per trade) for first 2 weeks live |
| 7.4 | [ ] | P0 | Set `max_daily_loss` very conservatively (e.g., -$50) for first live run |
| 7.5 | [ ] | P0 | Monitor live fills for first week daily — compare to paper performance |
| 7.6 | [ ] | P1 | Gradually increase position size as confidence grows |
| 7.7 | [ ] | P1 | Decision B: multi-strategy positions — independent or combined caps? |
| 7.8 | [ ] | P2 | Email/Slack alerts on fill, daily loss breach, and error codes |

---

## Owner Decisions Required

These two questions are not blocking Sprint 2 or 3, but must be answered before Sprint 4.

| # | Decision | Options | Deadline |
|---|----------|---------|----------|
| A | Live market data subscription (~$10–25/month via IBKR)? | **Yes** = real-time signals, works for intraday. **No** = 15-min delayed, fine for end-of-day strategies, free. | Before Sprint 4 |
| B | Multi-strategy position behavior: when two strategies both want to buy the same stock, do they act independently (each gets own position) or combine into one? | **Independent (default)** = Strategy A buys 10, Strategy B buys 10 → 20 shares total. **Combined** = shared cap, more complex. | Before Sprint 4.5 |

---

## Bugs & Improvements Log

| # | Severity | Description | Status |
|---|----------|-------------|--------|
| B-01 | S1 | `limit_price=0` and negative `limit_price` not rejected | Fixed |
| B-02 | S1 | `OrderManager.__init__` crashed when not connected | Fixed |
| B-03 | S2 | `cancel_order()` returned `True` for already-cancelled orders | Fixed |
| B-04 | S1 | `get_positions()` returned hardcoded 0.0 for all P&L fields | Fixed |
| B-05 | S1 | `cancelMktData()` not called if exception during price polling | Fixed |
| B-06 | S1 | Double-lock race in `_handle_order_status()` Cancelled branch | Fixed |
| B-07 | S2 | `PendingCancel` not in `OrderStatus` enum — logged false warnings | Fixed |
| B-08 | S1 | `ReconnectManager` reconnect always failed — `ib_insync` calls `asyncio.get_event_loop()` internally; Python 3.12 raises RuntimeError in non-main threads. Fix: `run_coroutine_threadsafe(ib.connectAsync(), main_loop)` in `broker/ibkr_client.py` | Fixed 2026-05-02 |
| B-09 | S1 | `OrderManager.sync()` crashed nightly (~00:00 UTC) — called from ReconnectManager daemon thread after IBC AutoRestartTime; same Python 3.12 asyncio thread issue. Fix: `run_coroutine_threadsafe` path in `sync()`. Deployed 2026-05-06. | Fix v1 incomplete (see B-10) |
| B-10 | S1 | B-09 v1 still crashed nightly: `_do_sync()` coroutine called sync `reqAllOpenOrders()` which calls `loop.run_until_complete()` — raises "This event loop is already running" because we're already awaiting on the main loop. Fix: `await reqAllOpenOrdersAsync()` inside the threadsafe coroutine. Bug A (analogous failure in `connect()` post-handshake at attempt 5) deferred — bot recovers via systemd restart. | Fixed 2026-05-07 — awaiting nightly confirmation |
| B-11 | S1 | PingPong zero fills since deploy (2026-05-15): `on_tick` runs on daemon scheduler thread → `get_market_price` → `qualify_contract` → `ib.qualifyContracts()` → `loop.run_until_complete()` raises — swallowed by broad except → silent no-op every tick. Same latent bug in `OrderManager` (`ib.sleep` on place/cancel), `is_alive` (`ib.reqCurrentTime`). Fix v1 (`fff3950`): `_needs_threadsafe_route()` auto-detect + `run_coroutine_threadsafe` routing in `qualify_contract`, `get_market_price`, `get_account_summary`, `get_positions`, `is_alive`, `sleep`. Fix v2 (`b8ec0da`): wrap each `*Async` call in inner `async def` so ib_insync's `Async` method is created on main loop thread, not daemon. Fix v3 (`554caf4`): root cause confirmed via ib_insync source — `Client.sendMsg` always calls `getLoop()` → `asyncio.get_event_loop_policy().get_event_loop()` from daemon raises "There is no current event loop". `IB.placeOrder`/`cancelOrder` go through `sendMsg`. Added `IBKRClient.ib_place_order` + `ib_cancel_order` routing methods; updated `OrderManager.place_order`/`cancel_order`/`cancel_all`; extended TS-07 grep to cover both methods; added TS-12 + TS-13. **387 tests pass.** | Fixed 2026-05-15 (3 commits, deployed to VPS — PingPong placed first BUY successfully at 17:21:49) |
| B-12 | S1 | PingPong fast-fill race (2026-05-15 — surfaced post-B-11 deploy): bot placed 1 BUY then went silent for 21+ min. Two compounding bugs both prior PingPong CRs missed. (a) `test_pingpong.py:on_tick` re-set `_order_pending=True` AFTER `safe_place_order` returned, overwriting `on_fill`'s `_clear_pending()` when a fast MKT fill arrived during `place_order`'s internal `_client.sleep(0.5)`. (b) `order_manager.py:place_order` wrote `_strategy_name_by_order_id` AFTER the sleep, so the fill event built `OrderResult.strategy_name=None`, `BaseStrategy._dispatch_on_fill` filtered the callback out, strategy never saw its own fill. Fix (`a932205`): stamp `_strategy_name_by_order_id` BEFORE the sleep; in `on_tick` arm pending BEFORE `safe_place_order` and clear in exception paths; only stamp `_pending_order_id` post-call if pending survived. Tests: `test_pp24`, `test_pp25`, `test_ms12`. **392 tests pass.** WORKFLOW.md "Pending-flag pattern CR checklist" added to catch the same trace next time. | Fixed 2026-05-15 (PRs #242/#243 merged) |
| B-13 | S1 | PingPongTest-AAPL had 0 fills today (2026-05-18, Monday): every `reqMktData(AAPL)` rejected with code 10089 ("Requested market data requires additional subscription ... Delayed market data is available"). Root cause: `IBKRClient._set_market_data_type` called `self.ib.reqMarketDataType(mode)` directly — `IB.reqMarketDataType → Client.send → Client.sendMsg → getLoop()` raises `RuntimeError: no current event loop in thread 'ReconnectManager'` from the daemon thread. After Sun 23:59 UTC gateway auto-restart drop, `connect()`'s post-handshake `_set_market_data_type(DELAYED)` at line 146 raised silently; the next ReconnectManager `connect()` short-circuited on `if ib.isConnected(): return` (TCP was up from the partial success), so mode was never re-applied. TWS resets mode to REALTIME on every fresh session → every `reqMktData` for the paper account returns 10089. SMA/RSI2MR weren't affected (yfinance, not real-time IBKR). Fix (`d142517` + post-CR tighten `03d2ab7`): same auto-route pattern as `qualify_contract`/`ib_place_order`/`ib_cancel_order`; new `_MKT_DATA_TYPE_TIMEOUT = 5`; TS-14/TS-15 lock the regression positively (record-thread + monkeypatch spy on `run_coroutine_threadsafe`); TS-07 grep tripwire extended. Audit confirms `_set_market_data_type` was the last unrouted `sendMsg` call in `ibkr_client.py`. **Closes Bug A** (deferred from B-10 — same root-cause class: daemon-thread `sendMsg → getLoop()` raise). **390 passed / 49 skipped.** | Fixed 2026-05-18 (PRs feature→develop→main, deployed + verified) |

---

## Code Review Cycle (2026-05-02) — codereview.md

See `codereview.md` for full issue table. Work top-to-bottom by execution priority.

| # | Issue | Severity | Status | PR |
|---|-------|----------|--------|----|
| CR-01 | Restore CI — `.github/workflows/` was gitignored, no CI in repo | Critical | [x] | `feature/restore-ci-workflow` |
| CR-02 | ntfy topic hard-coded with account ID, journal logs shipped publicly | Critical | [x] | `feature/ntfy-private-topic` |
| CR-03 | No backup operator for weekly 2FA — single point of failure | High | [x] | `feature/cr-03-operator-runbook` |
| CR-04 | Dashboard binds 0.0.0.0, no auth on GET endpoints | High | [x] | `feature/cr-04-05-dashboard-bind-ratelimit` |
| CR-05 | No rate limiting / lockout on `/api/bot/*` token endpoint | High | [x] | `feature/cr-04-05-dashboard-bind-ratelimit` |
| CR-06 | No secret scanner in pre-push gate or CI | High | [x] | `feature/add-gitleaks-pregate` |
| CR-07 | `ib_insync` archived/unmaintained; no lockfile for deps | High | [ ] | — (multi-week, track in BACKLOG) |
| CR-08 | `/opt/ibc/config.ini` not chmod 600 in setup.sh | High | [x] | `feature/cr-08-chmod-ibc-config` |
| CR-09 | Health timer stale threshold (93600s) doesn't match dashboard logic | Medium | [x] | `feature/cr-09-health-timer-weekend` |
| CR-10 | Dashboard bearer token stored in localStorage | Medium | [x] | `feature/cr-remaining-quick-fixes` |
| CR-11 | Account ID `<account-id>` literal in source files | Medium | [x] | `feature/ntfy-private-topic` |
| CR-12 | ntfy notification body contains 50 lines of journalctl output | Medium | [x] | `feature/ntfy-private-topic` (impl: `deploy/systemd/tradebot-notify@.service` — body is summary-only) |
| CR-13 | TradeLog reopened on every dashboard request (60 opens/min) | Medium | [x] | `feature/cr-remaining-quick-fixes` |
| CR-14 | `params` exposes `initial_capital` in live mode (misleading log) | Medium | [x] | `feature/cr-remaining-quick-fixes` |
| CR-15 | systemd units missing hardening directives (NoNewPrivileges etc.) | Medium | [x] | `feature/cr-remaining-quick-fixes` |
| CR-16 | Dashboard renders API fields into HTML without escaping (XSS) | Low | [x] | `feature/cr-remaining-quick-fixes` |
| CR-17 | 0.0.0.0 bind documented inconsistently across 3 files | Low | [x] | resolved by CR-04 |
| CR-18 | Bearer-token check not covered by HTTP-layer tests | Low | [x] | `feature/cr-remaining-quick-fixes` |
| CR-19 | Custom test runner instead of pytest — onboarding cost | Low | [x] | `feature/cr-19-pytest-migration` |
| CR-20 | `RiskManager.check()` swallows open-orders exception silently | Low | [x] | `feature/cr-remaining-quick-fixes` |

---

## Notes

- **Priority:** P0 = must have · P1 = should have · P2 = nice to have
- **Severity (bugs):** S1 = critical · S2 = major · S3 = minor
- Update this file at the start of every session
- See `CLAUDE.md` for full context when starting a new Claude session
- Architect plan for Sprint 2 & 3 logged 2026-04-10
- Architect review (Sprint 4 pre-flight) completed 2026-04-11 — 7 structural fixes applied, codebase is Sprint 4-ready
- Risk rules amended 2026-04-11: `plan_trade()` enforces 2% max risk + 1:3 min R/R atomically; short trades supported; PnLPoller now ACTIVE
- Test status: 93/93 on trading days · 84/93 on weekends (9 GE market-data tests require open market)
