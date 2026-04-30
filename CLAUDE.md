# CLAUDE.md — Session Handoff Document

Read this file at the start of every new Claude session before touching any code.
It gives you full context so you can continue work without re-explaining everything.

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

**Last session completed (2026-04-22) — main branch — Sprint 5 VPS deployment: all files created. Bot is ready to deploy to Hostinger.**

### What was done this session

**Sprint 5 — VPS deployment files complete:**
- Provisioned Hostinger KVM 1 VPS: Ubuntu 24.04 LTS, US Boston 2, IP `2.24.222.199`
- Created `deploy/setup.sh` — one-shot root script: installs Java 17, Xvfb, IB Gateway, IBC, Python venv, systemd units
- Created `deploy/ibc/config.ini` — IBC config for paper trading (credentials are placeholders; user fills in on VPS)
- Created `deploy/ibc/start_ibgateway.sh` — Xvfb + IBC wrapper, exec'd by systemd
- Created `deploy/systemd/ibgateway.service` — runs IBC/IB Gateway, OnFailure notifies
- Created `deploy/systemd/tradebot.service` — runs Python bot as `tradebot` user, Requires ibgateway, RestartSec=30
- Created `deploy/systemd/tradebot-notify@.service` — template unit, posts to ntfy.sh topic `tradebot-DUE090987` on failure
- Created `deploy/systemd/tradebot-health.service/.timer` — checks `data/health.txt` every 2h, alerts if stale >26h
- Added health.txt write to `strategies/sma_crossover.py` `on_tick()` — writes UTC ISO timestamp after guard checks pass
- Created `deploy/CHECKLIST.md` — step-by-step numbered checklist for the user to follow

**Notification channel:** ntfy.sh, topic `tradebot-DUE090987` (free, no account needed). User subscribes via ntfy app or https://ntfy.sh/tradebot-DUE090987. Alerts fire only on: service failure, crash restart, or stale health check.

**START HERE — next tasks:**
1. **Deploy to VPS** — SSH in, clone repo, run `bash /opt/tradebot/deploy/setup.sh`, fill in IBKR credentials in `/opt/ibc/config.ini`, start services. Full steps in `deploy/CHECKLIST.md`.
2. **Subscribe to ntfy alerts** — install ntfy app, subscribe to `tradebot-DUE090987`
3. **Start paper trading on VPS** — `systemctl start ibgateway && systemctl start tradebot`
4. **Monitor 1+ week** — check `TradeLog.daily_summary()` each trading day, review `journalctl -fu tradebot`
5. **4.5 — Tune** — after paper results, test sma_fast=20/sma_slow=50; validate on 2008/2022 bear regimes

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
| IB Gateway dir | `/opt/ibgateway` |
| Notification | ntfy.sh topic: `tradebot-DUE090987` |

**Access pattern:** `ssh chappy-vps` → `sudo -i` → work in `/opt/`
**VNC tunnel:** `ssh -L 5900:localhost:5900 chappy-vps` (not the old root version)
**If SSH times out:** check Tailscale is running on your PC first.

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

`gh` is not installed on the dev PC. Open PRs via browser:
`https://github.com/gzion2719/Trad_Bot_wClaude/pull/new/<branch-name>`

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

## Files to always read before editing

| File | Why |
|---|---|
| `TODO.md` | Current task priorities and sprint roadmap |
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
