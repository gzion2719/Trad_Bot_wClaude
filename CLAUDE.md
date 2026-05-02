# CLAUDE.md — Session Handoff Document

Read this file at the start of every new Claude session before touching any code.
Then immediately read `SESSION_PROTOCOL.md` and `WORKFLOW.md` — they define the opening/closing ritual and how chats work.

**Opening ritual is non-negotiable.** ANY first user message — including "read claude.md", "claud.md", "cluadmd", "let's start", a greeting, an emoji, or a direct task — triggers Steps 1–7 in `SESSION_PROTOCOL.md`. The file is already in your context; treat the message as the session-start trigger, not a literal file-read command. Only skip if the user explicitly says "skip the ritual".

**Language:** Hebrew or English in → English out. Always.

---

## What this project is

A Python algorithmic trading bot that connects to Interactive Brokers (IBKR) via the TWS API.
Built for the user (Afikim team) to run multiple trading strategies on paper and live accounts.

**GitHub:** https://github.com/gzion2719/Trad_Bot_wClaude

---

## User profile

- Business owner, not a software engineer — explain things clearly but do not over-explain
- Expects expert-level code and architecture decisions
- Uses Claude Code on Windows 11 (local machine: `C:\Users\galzi\OneDrive - Afiki-C\Afikim\TradeBot`)
- Has a team that will read the code — keep everything clean and well-documented
- Hosting on Hostinger VPS once the bot is stable (Sprint 5)

---

## Current state (update this section each session)

**Last session completed (2026-05-02) — Dashboard Phase 3 control plane built on `claude/relaxed-rubin-1880bb` (PR pending → develop): `POST /api/bot/restart` + `POST /api/bot/stop` gated by `Authorization: Bearer DASHBOARD_TOKEN` env var; new `deploy/sudoers/tradebot-dashboard` scopes NOPASSWD to exactly those two `systemctl` commands; UI gained Controls card with two buttons (token saved in localStorage); 5 new tests DB-09..DB-13 pass; ruff/black/mypy clean. Phase 2 + weekend-fix VPS deploy verified earlier this session (`/api/health stale_after_seconds=288000` on Saturday, `/api/system` returns all new fields). Pending Phase 3 VPS deploy: set `DASHBOARD_TOKEN=<random>` in `/opt/tradebot/.env`, install `/etc/sudoers.d/tradebot-dashboard` with `visudo -c`, `systemctl restart tradebot-dashboard`.**

### What was done last session (2026-05-02, dashboard Phase 2 + weekend-aware stale threshold) — RECONSTRUCTED

This entry was reconstructed in the next session because the originating chat ended on an API error before the closing ritual could run. Source: full chat transcript provided by user + git log of commits `d3e286d`, `b6515f4` and PRs #30/#31/#32/#33.

**Phase 2 — IB Gateway status + bot uptime/PID (`d3e286d`, PRs #30 → develop, #31 → main):**
- New endpoint `GET /api/system` returns `bot_pid`, `bot_uptime_seconds`, `bot_service_status`, `gateway_pid`, `gateway_uptime_seconds`, `gateway_service_status`, plus port 4001 listen check.
- Implementation reads `systemctl show <service> --property=MainPID,ActiveEnterTimestamp` and `systemctl is-active <service>`. Degrades gracefully on dev PC / Windows where systemctl is unavailable.
- Dashboard UI gained a "System" card with green pulsing dot when gateway is active, bot uptime in human-readable form, and port-open indicator.
- User confirmed live read on VPS via Tailscale: gateway active ✅, bot PID 52545 ✅, uptime 6.3h ✅, port 4001 open ✅.

**Weekend-aware stale threshold fix (`b6515f4`, PRs #32 → develop, #33 → main):**
- Diagnosis: dashboard showed Liveness "stale" (last tick 42.5h ago = Friday Apr 30 20:10 UTC) on a Saturday. Initial wrong-path: hypothesized `BarScheduler` stopped after 5 consecutive `on_tick()` exceptions. User intuited the actual cause: it was the weekend.
- Root cause: SMA strategy doesn't use `BarScheduler` — it uses a custom `_daily_scheduler` in `main.py` that fires `on_tick()` once per day at 16:10 ET. Weekend gap = ~72h, but the dashboard's hardcoded `_STALE_AFTER_SECONDS = 26h` threshold wasn't aware of this. Bot was healthy the whole time; alarm was a false positive.
- Fix in `dashboard/app.py`: replaced constant with `_stale_threshold_seconds()` returning 80h on Saturday/Sunday/Monday-before-16:10-ET, 26h on regular trading days. Updated DB-03/DB-04 tests to cover both branches. ruff/black/mypy all ✅.
- Process improvement codified in `WORKFLOW.md` "Debugging discipline" section: before hypothesizing failure modes for a "stopped" symptom, read the producer to confirm expected cadence.

### What was done earlier this session (2026-05-02, dashboard Phase 1)

**Mission control dashboard — Phase 1 read-only (ROADMAP 5.7):**
- New `dashboard/` module with FastAPI app: `dashboard/app.py` (routes), `dashboard/__main__.py` (uvicorn entry), `dashboard/static/index.html` (auto-polling UI, dark theme, refreshes every 5s).
- Endpoints: `GET /api/health` (reads `data/health.txt`, classifies ok/stale/missing/unreadable against the same 26h threshold as `tradebot-health.timer`), `GET /api/today` (`TradeLog.daily_summary()`), `GET /api/recent-fills?limit=N` (clamped 1–200), `GET /api/info` (account/host/port metadata).
- New systemd unit `deploy/systemd/tradebot-dashboard.service`: separate process from `tradebot.service` so a dashboard crash cannot affect the live bot. Binds `127.0.0.1:8080`. Reach via Tailscale `http://100.113.140.69:8080` or `ssh -L 8080:localhost:8080 chappy-vps`. **Never expose publicly without HTTP auth + TLS.**
- Added `fastapi>=0.110.0` and `uvicorn[standard]>=0.27.0` to `requirements.txt`.
- Added 6 tests (DB-01 through DB-06) to `tests/run_tests.py` Section 18 — exercise route functions directly (no HTTP layer / no httpx dep). All 6 pass locally.
- ruff ✅ black ✅ mypy ✅ (mypy uses `--ignore-missing-imports` so FastAPI lack of stubs is fine). black auto-reformatted `tests/run_tests.py`.
- **Scope deliberately limited to read-only.** Control plane (kill/restart bot) and IB Gateway login surface (replace VPN VNC 2FA) are explicitly deferred — separate phases tracked in BACKLOG. Bundling these would have tripled the blast radius.

### What was done earlier this session (2026-05-02, B-08 reconnect fix)

**Reconnect always-failing bug fixed (B-08):**
- Root cause: `ib_insync` calls `asyncio.get_event_loop()` internally; Python 3.12 raises `RuntimeError` in non-main threads — every `ReconnectManager` reconnect attempt failed before reaching IBKR.
- Fix in `broker/ibkr_client.py`: save main event loop on first `connect()` call (main thread); on subsequent calls from daemon thread use `asyncio.run_coroutine_threadsafe(ib.connectAsync(), main_loop)`. Also replaced `ib.sleep()` with `time.sleep()` in post-connect poll.
- ruff ✅ black ✅ mypy ✅. PR `feature/fix-reconnect-asyncio-thread` → develop → main. Deployed via `git pull origin main && systemctl restart tradebot`. Bot confirmed connected (PID 52545).

### What was done last session (2026-05-01, continued)

**Code quality gate — made ruff + black + mypy all pass:**
- Created `pyproject.toml` — ruff config (ignores E402 intentional docstring pattern, E702 intentional semicolons in test runner); black line-length=100
- Ran `ruff check --fix`: auto-fixed 22 issues (unused imports, f-strings without placeholders, multiple imports on one line, redefined var)
- Fixed 8 F841 unused-variable issues manually in `tests/run_tests.py` and `tests/run_market_tests.py`
- Ran `black .`: auto-formatted 23 files to project style
- Fixed 15 mypy errors across 5 files:
  - `backtester/metrics.py`: added None guards for Optional[float] in `win_rate()` and `profit_factor()`
  - `data/feed.py`: added `# type: ignore[attr-defined]` for ib_insync's `updateEvent` (untyped lib)
  - `broker/order_manager.py`: asserted non-None before passing prices to IB order constructors; annotated `avg_price: Optional[float]`
  - `strategies/sma_crossover.py`: **bug fix** — `get_account_summary()` returns a list not a dict; fixed `_get_equity()` to build dict comprehension first (`{s.tag: s.value for s in ...}`)
  - `main.py`: added `# type: ignore[assignment]` on `timezone` fallback lines; changed `TradeLog(db_path="...")` to use `Path(...)`

### What was done last session (2026-05-01, earlier)

**Protocol scaffold bootstrap — YuTom methodology applied to TradeBot:**
- Created `SESSION_PROTOCOL.md` — full opening/closing ritual with worked example
- Created `WORKFLOW.md` — 3 chat archetypes, pre-push gate, red flags, emergency protocol
- Created `CHATLOG.md` — session memory log, newest-first format
- Created `docs/ROADMAP.md` — phased plan migrated from TODO.md sprints (Phases 1–7)
- Created `docs/BACKLOG.md` — all open items categorized (Infra/Strategy/Risk/Tooling/Decisions)
- Created `.github/workflows/ci.yml` — CI: ruff → black --check → mypy → pytest on push + PR
- Created `Makefile` — `make pre-push` mirrors CI exactly for local gate
- Updated `CLAUDE.md` — added protocol file references, language pair, file map section
- Marked TODO 5.8 [x] (CI/CD pipeline now done)

### What was done last session (2026-04-30)

**IBKR info-code noise fix (`broker/order_manager.py`) — PR #9 merged to develop:**
- Codes 1100/1102/2103/2105/2107/2157 were missing from all sets → fell through to `logger.error()` → flooded `journalctl`
- New three-tier classification: `_DEBUG_CODES` (silent), `_INFO_CODES` (→ INFO), `_WARNING_CODES` (→ WARNING)
- 1100 (connectivity lost) → WARNING; 1102/2103/2105/2107/2157 (restored/data farm) → INFO; real errors unchanged at ERROR
- TODO 6.5 marked [~] (in progress)

**Recovered bot from 6-day outage (Apr 24 → Apr 30):**
- Root cause: IBKR's weekly token reset on Sunday Apr 26 (~01:00 ET) invalidated the gateway session — stuck at 2FA prompt all week
- Recovery: VNC tunnel → IB Gateway login → SMS code → `tradebot.service` restart. Reconnected to DUE090987 in <30 seconds.

**IB Gateway transitioned to full systemd management:**
- Created 3 new systemd units: `xvfb.service`, `x11vnc.service`, `ibgateway.service`
- All enabled for auto-start on boot. `tradebot.service` already had `Requires=ibgateway.service` from prior work — chain works end-to-end.
- Replaced the old backgrounded/disowned IB Gateway process with proper supervision. No more "gateway dies and nobody notices" outages.
- `x11vnc` now always running on `:99` listening on `localhost` only (must use SSH tunnel).

**IBC config hardening:**
- Added `ReloginAfterSecondFactorAuthenticationTimeout=yes` to `/opt/ibc/config.ini` — IBC will auto-restart the login flow if a 2FA prompt times out (instead of sitting silently like Apr 24).

**Researched IBKR 2FA constraints (and corrected my earlier wrong advice):**
- IBKR's `AutoRestartTime` keeps gateway sessions alive **for up to a week** with no 2FA needed for daily restarts. IBC was already configured with this (logs show `Auto restart time already set to 11:59 PM`).
- **2FA is required ONCE per week** — Sunday ~01:00 ET when IBKR servers invalidate all tokens. Mon–Sat restarts use the cached token, no human action needed.
- Owner is enrolled in **Interactive IL Key** (Israeli code-generator variant), not push-notification IB Key. Push 2FA appears unavailable for Israeli accounts — needs IBKR support inquiry.
- IBKR has **revoked all 2FA opt-out paths** for trading. There is no API key, service account, or Trusted IP bypass. Weekly 2FA is the regulatory floor.

**START HERE — next tasks:**
1. **Deploy Phase 2 + weekend fix to VPS** — pulls already-on-main `/api/system` endpoint and weekend-aware stale threshold:
   - `ssh chappy-vps && sudo -i && cd /opt/tradebot && git pull origin main && systemctl restart tradebot-dashboard`
   - Verify: `curl http://100.113.140.69:8080/api/system` returns the new fields; `curl http://100.113.140.69:8080/api/health` shows `ok` on Sat/Sun (not `stale`).
2. **First Sunday morning (next: 2026-05-03 ~09:00 IL time = 02:00 ET) — test the weekly re-auth flow.**
   - SSH chappy-vps → tunnel `ssh -L 5900:localhost:5900 chappy-vps` → TightVNC `localhost:5900`
   - Generate code in IBKR Mobile (Security → Generate Code), enter in gateway login dialog
   - Confirm gateway logs in and bot reconnects within 2 min: `sudo journalctl -fu tradebot`
3. **Dashboard Phase 3 — control plane** (kill/restart bot endpoints with token auth + narrow sudoers rule). Fresh feature branch from `develop`.
4. **Send IBKR support inquiry** (drafted in Obsidian) asking about: (a) switching from Interactive IL Key to push-notification IB Key, (b) any unattended weekly auth options for paper accounts.
5. **Monitor paper trading** — `sudo journalctl -fu tradebot` daily; check `TradeLog.daily_summary()` each trading day.
6. **4.5 — Tune** — after 1+ week paper results, test sma_fast=20/sma_slow=50; validate on 2008/2022 bear regimes.

**Pre-live hardening items (non-blocking for paper, tracked):**
- Q4: if avg_cost==0 on reconcile, consider deferring `_in_position=True` until stop can be computed
- Q6a: consider auto-re-placing STOP in `_exit()` when SELL is rejected
- M7: validate strategy on 2008/2022 bear regimes before going live

**Owner decisions still open:**
- **Decision A:** Pay for IBKR live data (~$10–25/mo)? Not needed for daily-bar strategies — delayed data is fine. Needed for intraday.
- **Decision B:** Multi-strategy positions — independent or combined caps? Not blocking until Sprint 4.8.

**VPS details:**
| Setting | Value |
|---|---|
| Provider | Hostinger KVM 1 |
| Public IP | 2.24.222.199 — **port 22 BLOCKED by UFW. Do NOT SSH to this IP.** |
| Tailscale IP | 100.113.140.69 — only network path for SSH |
| OS | Ubuntu 24.04 LTS |
| SSH | `ssh chappy-vps` (alias for `chappy@100.113.140.69`, key `~/.ssh/chappy_v3`) |
| SSH user | `chappy` (sudo-capable). Root SSH is **disabled**. |
| Sudo | `sudo -i` or `sudo <cmd>` for `/opt/` work. Prompts for chappy password. |
| Rescue | Hostinger web console (browser KVM) if Tailscale/SSH fails |
| Bot dir | `/opt/tradebot` |
| IBC dir | `/opt/ibc` |
| IB Gateway dir | `/opt/ibgw` |
| Notification | ntfy.sh topic: `tradebot-DUE090987` |
| Systemd units | `xvfb.service` → `x11vnc.service` → `ibgateway.service` → `tradebot.service` (chain auto-starts on boot) |

**Access pattern:** `ssh chappy-vps` → `sudo -i` → work in `/opt/`
**VNC tunnel:** `ssh -L 5900:localhost:5900 chappy-vps` (x11vnc is always running on `:99` via systemd)
**If SSH times out:** check Tailscale is running on your PC first.

---

## Weekly 2FA cadence (read this — it's how the bot stays alive)

IBKR's security model:
- **Mon–Sat at 23:59 UTC**: IBC's `AutoRestartTime` triggers a gateway restart. Uses the cached token — **no 2FA, fully automated**. Bot reconnects within 30 seconds.
- **Sunday ~01:00 ET (08:00 IL time)**: IBKR servers invalidate all tokens. The next gateway restart sits at the login screen waiting for a fresh 2FA code. **Owner must intervene once per week.**

### Sunday morning recovery routine (60 seconds)
1. SSH `chappy-vps`, then in a second local terminal: `ssh -L 5900:localhost:5900 chappy-vps`
2. TightVNC → `localhost:5900` → see IB Gateway login dialog
3. IBKR Mobile app → Security → **Generate Code** → enter the 6 digits in the dialog
4. Verify: `ss -tlnp | grep 4001` shows LISTEN, then `sudo journalctl -fu tradebot` shows `Connected | account=DUE090987`

### What we did to harden against missed Sundays
- `ReloginAfterSecondFactorAuthenticationTimeout=yes` in `/opt/ibc/config.ini` — IBC re-prompts if a 2FA code expires unanswered (instead of sitting silently)
- `ibgateway.service` with `Restart=on-failure` — gateway process is supervised; crashes get logged and retried

### What we CANNOT fix (regulatory floor)
- IBKR has revoked all 2FA opt-out paths for trading accounts (paper or live). No API key, no service-account flow, no Trusted IP bypass eliminates the weekly login.
- Owner is on **Interactive IL Key** (Israeli code-generator). Standard push-notification IB Key may not be available for Israeli accounts — pending IBKR support inquiry.
- If owner travels and misses a Sunday, bot will be down until they return + complete the 2FA. Mitigation: schedule travel around Sundays, or pre-share VNC access with a trusted team member for that one minute.

---

## Python environment

- Python: 3.12 (`C:\Users\galzi\AppData\Local\Programs\Python\Python312\python.exe`)
- No virtual environment yet (Sprint 5.2)

```bash
# How to run tests:
cd "C:\Users\galzi\OneDrive - Afiki-C\Afikim\TradeBot"
"C:\Users\galzi\AppData\Local\Programs\Python\Python312\python.exe" -m tests.run_tests
```

---

## Full project layout

```
TradeBot/
├── broker/
│   ├── ibkr_client.py      — ib_insync wrapper: connect, market data, contract qualification
│   ├── order_manager.py    — place/cancel/sync orders, thread-safe cache, event callbacks
│   └── reconnect.py        — ReconnectManager: auto-reconnect daemon with exponential backoff
│
├── risk/
│   ├── risk_manager.py     — RiskManager: pre-trade checks (order value, exposure, daily loss)
│   └── position_sizer.py   — PositionSizer: fixed, percent_of_equity, kelly (static methods)
│
├── data/
│   ├── bar.py              — Bar frozen dataclass: symbol, timestamp, OHLCV, is_delayed
│   ├── feed.py             — DataFeed (abstract), IBKRFeed (5-sec bars), BarScheduler
│   ├── historical.py       — HistoricalDataLoader: yfinance, IBKR reqHistoricalData, CSV
│   └── trade_log.py        — TradeLog: SQLite WAL, record fills, cost_basis, realized_pnl
│
├── backtester/
│   ├── engine.py           — BacktestEngine, MockOrderManager, BacktestDataFeed
│   ├── portfolio.py        — BacktestPortfolio: cash, positions, weighted avg cost, equity curve
│   └── metrics.py          — sharpe_ratio, max_drawdown, win_rate, profit_factor, summary()
│
├── models/
│   └── order.py            — OrderRequest, OrderResult (+ cost_basis field), Position, enums
│
├── config/
│   ├── settings.py         — loads .env: IB_HOST, IB_PORT, IB_CLIENT_ID
│   ├── validator.py        — validate_config(), ConfigError — called first in main()
│   └── logging_config.py   — rotating file + console logger
│
├── strategies/
│   └── base_strategy.py    — BaseStrategy ABC with full Sprint 4-ready interface
│
├── main.py                 — wiring: validate → connect → OrderManager → RiskManager → ReconnectManager
└── tests/
    ├── run_tests.py        — 93 tests across 17 sections (most run without TWS connection)
    └── run_market_tests.py — 5 tests requiring live market hours
```

---

## Architecture

```
main.py
  validate_config()
  IBKRClient  ──────────────────────────────────────────────────────────────────
    └── OrderManager                                                            │
          ├── RiskManager       (wired via om.on_fill)                          │
          └── ReconnectManager  (monitors disconnect, retries with backoff)     │
                └── Strategy(client, order_manager, risk_manager, reconnect,   │
                             feed, symbol)   ◄──────────────────────────────────┘
```

### How a live strategy tick works
```python
def on_tick(self):
    if not self.reconnect.wait_for_connection(timeout=60):
        return                                    # pause during TWS reconnect
    if self.risk_manager.is_halted():
        return                                    # daily loss ceiling hit
    bar = self.feed.get_latest(self.symbol)
    if bar is None:
        return
    # ... signal logic ...
    request = OrderRequest(symbol=self.symbol, action=OrderAction.BUY, quantity=10)
    self.safe_place_order(request, bar.close)     # risk check + place in one call
```

### How to backtest a strategy
```python
from backtester.engine import BacktestEngine
from data.historical import HistoricalDataLoader

df = HistoricalDataLoader.load_yfinance("AAPL", "2022-01-01", "2024-01-01")
engine = BacktestEngine(
    strategy_class=MyStrategy,
    data=df,
    symbol="AAPL",
    initial_capital=100_000,
    strategy_kwargs={"sma_fast": 10, "sma_slow": 30},  # passed to __init__
)
result = engine.run()
result.print_summary()
```

The **same strategy class** runs in live and backtest unchanged. The engine injects `MockOrderManager` instead of the real one. Fills happen at the next bar's open (no look-ahead bias).

---

## Key component reference

### IBKRClient (`broker/ibkr_client.py`)
- `connect(retries=3)` — connects, auto-sets delayed data for paper accounts, removes duplicate disconnect handlers
- `get_market_price(symbol)` — polls with timeout, try/finally guarantees `cancelMktData()`
- `qualify_contract(contract)` — resolves full contract, prefers `primaryExchange`
- `is_alive()` — heartbeat via `reqCurrentTime()`

### OrderManager (`broker/order_manager.py`)
- `place_order(request, allow_duplicate=False)` — validates, deduplicates, submits
- `cancel_order(order_id)` / `cancel_all(symbol)`
- `get_open_orders()` / `get_positions()` — current state (reads IBKR portfolio for full P&L)
- `sync()` — pulls all open orders from TWS via `reqAllOpenOrders`
- `on_fill(cb)` / `on_cancel(cb)` / `on_error(cb)` — register callbacks

### ReconnectManager (`broker/reconnect.py`)
- `start()` — arms the manager after initial connect
- `stop()` — disarms on clean shutdown
- `wait_for_connection(timeout)` — strategies call this at top of `on_tick()`
- `is_halted` — True if all reconnect attempts exhausted
- **Key design:** `connect()` and `sync()` are in separate try/except blocks — sync failure after good TCP connect halts immediately rather than looping

### RiskManager (`risk/risk_manager.py`)
- **`plan_trade(entry, stop, target, equity, order_action=BUY)`** — PRIMARY method for strategies. Atomically validates R/R + 2% rule, then returns correctly sized share count. Always use this instead of calling `validate_setup()` + `risk_based()` separately.
  - Example: entry $150, stop $145, target $165, equity $10k → R/R=3.0 ✓, risk/share=$5 ≤ $200 ✓ → **40 shares**
  - Short example: entry $100, stop $105, target $85, `order_action=OrderAction.SELL` → same math, correctly inverted
  - `equity` MUST be fresh from `client.get_account_summary()["NetLiquidation"]` — never cache across bars
- `validate_setup(entry, stop, target, equity, order_action=BUY)` — validates only (no sizing). Use `plan_trade()` instead.
  - **Rule A:** `(target − entry) / (entry − stop) ≥ min_reward_risk_ratio` (3.0 default)
  - **Rule B:** stop distance per share must be ≤ `equity × max_risk_per_trade_pct` (2% default)
  - Supports both longs (stop < entry) and shorts (stop > entry)
- `check(request, current_price)` — raises `RiskViolationError` if any order-level rule breached
- `update_daily_pnl(pnl)` — wired via daemon in `main.py` — ACTIVE
- `reset_daily()` — wired via daemon in `main.py`, fires at 9:30 AM ET each day — ACTIVE
- `is_halted()` — True if daily loss ceiling breached
- `record_fill(result)` — logging-only hook (wired via `om.on_fill`), does NOT update P&L

**Constructor parameters (main.py):**
```
max_risk_per_trade_pct=0.02   # 2% of equity max risk per trade
min_reward_risk_ratio=3.0     # minimum 1:3 R/R required for every trade
```

### PositionSizer (`risk/position_sizer.py`)
- **`PositionSizer.risk_based(equity, entry_price, stop_price, risk_pct=0.02)`** — do not call directly from strategies; use `rm.plan_trade()` instead so sizing and validation always use the same `risk_pct`. If calling directly, MUST pass `risk_pct=rm.max_risk_per_trade_pct`.
- `PositionSizer.fixed(shares)` — fixed quantity
- `PositionSizer.percent_of_equity(equity, price, pct)` — e.g., 2% of $50k at $150 = 6 shares
- `PositionSizer.kelly(win_rate, win_loss_ratio, equity, price, max_fraction=0.25)` — capped Kelly

### BaseStrategy (`strategies/base_strategy.py`)
- Implement: `on_start()`, `on_tick()`, `on_stop()`
- Override optionally: `on_fill(result)` — auto-wired, called on every fill
- Override: `params` property — return config dict, stored in TradeLog per trade
- Use: `self.safe_place_order(request, price)` — always use this, not `self.om.place_order()`
- Available: `self.feed`, `self.symbol`, `self.client`, `self.om`, `self.risk_manager`, `self.reconnect`

### DataFeed / IBKRFeed / BarScheduler (`data/feed.py`)
- `IBKRFeed(client)` — subscribes to 5-sec real-time bars via `reqRealTimeBars`
- `feed.subscribe(symbol, callback)` — atomic, deduped, handler stored for clean removal
- `feed.unsubscribe(symbol)` / `feed.unsubscribe_all()`
- `feed.get_latest(symbol)` — returns most recent `Bar` or None
- `BarScheduler(strategy, interval_seconds=60)` — calls `on_tick()` on a timer; stops after 5 consecutive errors

### HistoricalDataLoader (`data/historical.py`)
- `load_yfinance(symbol, start, end, interval="1d")` — free, no API key, returns UTC DataFrame
- `load_ibkr(symbol, duration, bar_size, client)` — enforces 11s rate limit between calls
- `load_csv(filepath, symbol)` — auto-detects date column, validates OHLCV

### BacktestEngine (`backtester/engine.py`)
- `BacktestEngine(strategy_class, data, symbol, initial_capital, commission=1.0, strategy_kwargs={})`
- `engine.run()` → `BacktestResult` with `.fills`, `.equity_curve`, `.metrics`, `.portfolio`
- `result.print_summary()` — prints formatted metrics table
- ⚠️ **Single-symbol only** — `BacktestDataFeed.get_latest()` returns None for any other symbol (TODO in Sprint 4.8)

### TradeLog (`data/trade_log.py`)
- `TradeLog(db_path=None)` — SQLite WAL, auto-creates schema, safe migration on upgrade
- `record(result, strategy_name, strategy_params=None)` — call from `on_fill`
- `get_history(symbol, strategy, since, limit=500)` — returns list of dicts
- `daily_summary(date=None)` — returns `{total_trades, buys, sells, gross_buy, gross_sell, net_flow, realized_pnl}`
- Schema: `id, strategy_name, symbol, action, quantity, fill_price, fill_value, filled_at, order_id, account, cost_basis, realized_pnl, strategy_params`

### Models (`models/order.py`)
- `OrderResult` has `cost_basis: Optional[float]` — set by `BacktestPortfolio` on SELL fills; used by `win_rate()` and `profit_factor()`
- `OrderStatus.PENDING_CANCEL` — legitimate IBKR state during cancellation, not an error

---

## IBKR connection details

| Setting | Value |
|---|---|
| Account | DUE090987 (paper) |
| Host | 127.0.0.1 |
| Port | 7497 (paper) / 7496 (live — config validator warns loudly) |
| Client ID | 1 |
| Market data | Delayed auto-set for paper; realtime for live |

TWS must be running and logged in before starting the bot.
TWS API must have "Enable ActiveX and Socket Clients" checked.
TWS restarts daily ~11:45 PM EST — `ReconnectManager` handles this automatically.

---

## Git workflow

This project uses a **hybrid Git Flow**. Every team member must follow it.

### Branch structure

| Branch | Purpose | Who merges into it |
|---|---|---|
| `main` | Production — what runs on the VPS | Only `develop` (via PR) or `hotfix/*` (via PR) |
| `develop` | Integration — finished features accumulate here | Only `feature/*` branches (via PR) |
| `feature/<name>` | One branch per feature/task | Cut from `develop`, PR back to `develop` |
| `hotfix/<name>` | Emergency fix for a live production bug | Cut from `main`, PR to `main` AND `develop` |

### Rules — no exceptions

1. **Never push directly to `main` or `develop`.** All changes go through PRs.
2. **All feature work starts from `develop`**, not `main`.
3. **`main` only gets code from `develop`** (via PR, when the sprint is ready to ship) **or from a `hotfix`** (emergency only).
4. **Hotfixes must be merged into both `main` AND `develop`** — otherwise the fix gets lost on the next release.
5. **Branch names:** use `feature/short-description` or `hotfix/short-description`. Lowercase, hyphens, no spaces.

### Normal feature workflow

```bash
git checkout develop && git pull origin develop
git checkout -b feature/my-feature
# ... do the work ...
git push -u origin feature/my-feature
# Open PR → develop on GitHub
# After merge, delete the feature branch
```

### Shipping to production

When `develop` is stable and tested on paper:
```bash
# Open PR: develop → main on GitHub
# After merge, the VPS gets the new code:
ssh chappy-vps
cd /opt/tradebot && sudo git pull && sudo systemctl restart tradebot
```

### Emergency hotfix (production is broken)

```bash
git checkout main && git pull origin main
git checkout -b hotfix/fix-description
# ... fix the bug ...
git push -u origin hotfix/fix-description
# PR → main   (deploys the fix)
# PR → develop (keeps develop in sync — do NOT skip this)
```

### `gh` CLI note

`gh` is not installed on the dev PC. Open PRs via browser — **always use the `compare` URL format** (see rule 2 below). Never use `pull/new/<branch>` — it lets GitHub default the base to `main`.

### Claude-specific rules (enforce every session — no exceptions)

GitHub branch protection is not enforced on this free private repo. Claude is the enforcement layer.

1. **Always create a feature branch from `develop`**, never from `main`.
2. **Always use the `compare/<base>...<compare>` URL format for every PR link. Never use `pull/new/<branch>`.**
   `pull/new/<branch>` lets GitHub silently default the base to `main` regardless of what you write in prose — this caused a feature → main merge and again in May 2026 when the dashboard PR was given with the wrong URL.
   - Feature work: `https://github.com/gzion2719/Trad_Bot_wClaude/compare/develop...<feature-branch>`
   - Shipping to production: `https://github.com/gzion2719/Trad_Bot_wClaude/compare/main...develop`
   - Hotfix → main: `https://github.com/gzion2719/Trad_Bot_wClaude/compare/main...<hotfix-branch>`
   - Hotfix → develop: `https://github.com/gzion2719/Trad_Bot_wClaude/compare/develop...<hotfix-branch>`
3. **Never say "open a PR" without providing the full `compare/` URL** — prose-only base/compare instructions are not enough; the URL must encode the base branch mechanically.
4. **Before starting any work**, check current branch with `git branch` and confirm it is a `feature/*` or `hotfix/*` branch, never `main` or `develop` directly.
5. **After a PR merges to main**, always open a follow-up PR or fast-forward `develop` to keep them in sync.
6. **After creating a skill**, immediately re-read the manifest.json to confirm the entry persisted before declaring done — the system can overwrite the manifest between tool calls.

---

## Key conventions

- All currency: USD unless specified
- Default exchange: SMART (IBKR's smart routing)
- Default TIF: GTC — avoids DAY order cancellation when market is closed
- `setup_logging()` must be called before any module that uses `logging`
- Never import from `.env` directly — always go through `config/settings.py`
- Always qualify contracts before placing orders (`client.qualify_contract(...)`)
- Always use `safe_place_order()` in strategies — never call `self.om.place_order()` directly
- `profit_factor()` and `win_rate()` require `cost_basis` on fills — only populated by `BacktestPortfolio` (not live fills)

---

## File map

| File | Purpose |
|---|---|
| `CLAUDE.md` | This file — full project context, read first every session |
| `SESSION_PROTOCOL.md` | Opening + closing ritual — read immediately after CLAUDE.md |
| `WORKFLOW.md` | Chat archetypes, pre-push gate, git rules, red flags |
| `CHATLOG.md` | Session log, newest-first — read last 3 entries in opening ritual |
| `TODO.md` | Sprint-by-sprint task tracker |
| `docs/ROADMAP.md` | Phased roadmap with acceptance checks |
| `docs/BACKLOG.md` | Categorized open items, reviewed every 5 sessions |
| `docs/CHATLOG_ARCHIVE.md` | Archived older CHATLOG entries (created at session 10) |
| `.github/workflows/ci.yml` | CI pipeline: ruff → black → mypy → pytest |
| `Makefile` | Local gate targets — `make pre-push` mirrors CI exactly |

## Files to always read before editing

| File | Why |
|---|---|
| `SESSION_PROTOCOL.md` | Opening/closing ritual — non-negotiable every session |
| `WORKFLOW.md` | How chats work, pre-push gate, red flags |
| `CHATLOG.md` | Last 3 entries — where we left off |
| `docs/ROADMAP.md` | Current phase and pending items |
| `TODO.md` | Sprint-level task status |
| `strategies/base_strategy.py` | Interface every strategy must implement |
| `backtester/engine.py` | How backtest replay works |
| `broker/order_manager.py` | Core live trading logic |
| `models/order.py` | Data contracts used everywhere |

---

## How to run tests

```bash
# Full test suite (requires TWS running and connected):
cd "C:\Users\galzi\OneDrive - Afiki-C\Afikim\TradeBot"
"C:\Users\galzi\AppData\Local\Programs\Python\Python312\python.exe" -m tests.run_tests

# Expected results:
#   Trading day:  81/81 pass
#   Weekend:      72/81 pass (9 GE market-data tests require open market — expected)
```

---

## Known limitations / watch out for

- **Daily loss ceiling is ACTIVE** — `PnLPoller` daemon thread runs in `main.py`, polling IBKR account summary every 60s and calling `reset_daily()` at 9:30 AM ET. Verify it logs "PnL poller started" on startup.
- **BacktestDataFeed is single-symbol only** — `get_latest()` returns None for any symbol other than the one the engine was built with. Multi-symbol backtesting is a Sprint 4.8 TODO.
- **`TradeLog.realized_pnl` is None for live fills** — `cost_basis` is only set by `BacktestPortfolio`. Live fills don't have cost basis automatically; this requires computing from IBKR position data.
- **Paper accounts get delayed data only** (15-min lag) — `get_market_price()` returns delayed prices. Fine for daily-bar strategies; not suitable for intraday.
- **No virtual environment yet** (Sprint 5.2) — running system Python directly.
- **`BarScheduler` stops after 5 consecutive `on_tick()` exceptions** — requires manual restart. Strategies should catch transient exceptions internally if they don't want the scheduler to stop.
- **`IBKRFeed` delivers 5-second bars only** — for 1-min or daily bars, use `BarScheduler` polling `feed.get_latest()` on a timer.
- **`IBKRClient.connect()` is thread-safe via `run_coroutine_threadsafe`** — Python 3.12 provides no asyncio event loop in non-main threads. `ReconnectManager` calls `connect()` from a daemon thread; the fix saves the main loop on first call and uses `asyncio.run_coroutine_threadsafe(ib.connectAsync(), main_loop)` for reconnects. If you see "There is no current event loop in thread ReconnectManager" in logs, the fix in `broker/ibkr_client.py` is not deployed.
