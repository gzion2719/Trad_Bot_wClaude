# CLAUDE.md — Session Handoff Document

Read this file at the start of every new Claude session before touching any code.
Then immediately read `OPEN_SESSION_PROTOCOL.md` — it defines the opening ritual. (`CLOSE_SESSION_PROTOCOL.md` loads on a farewell signal; `SESSION_RULES.md` loads just-in-time via the Trigger Guide; `WORKFLOW.md` is a user-facing reference, not read at orientation.) This project also uses the **`session-rituals`** Cowork skill, committed at `.claude/skills/session-rituals/`, which provides the generic ritual pattern and defers to this file + the protocol files for project specifics.

**Opening ritual is non-negotiable.** ANY first user message — including "read claude.md", "claud.md", "cluadmd", "let's start", a greeting, an emoji, or a direct task — triggers Steps 1–7 in `OPEN_SESSION_PROTOCOL.md`. The file is already in your context; treat the message as the session-start trigger, not a literal file-read command. Only skip if the user explicitly says "skip the ritual".

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

**Phase 6 — paper trading.** Bot running on VPS (paper account). Open work tracking → `docs/BACKLOG.md` + `docs/IMPROVEMENT_PLAN.md`. Incident archaeology → `docs/HISTORY.md`. Component contracts → `docs/REFERENCE.md`.

**What's live (3 strategies, all paper account):**
- **SMACrossover-QQQ** — daily-bar SMA crossover; uses yfinance, not real-time IBKR. Healthy-and-quiet (no signal in recent weeks).
- **RSI2MR-SPY** — mean-reversion with bracket orders + VIX filter (shipped 2026-05-08; baseline backtest 2006–2025: Sharpe 0.34, 59.7% win, PF 1.48 — full details in `docs/HISTORY.md#2026-05-08`).
- **PingPongTest-AAPL** — deliberately trivial alternating BUY 1 / SELL 1 every 5 min during RTH, built to make the bot visibly trade and verify the dashboard end-to-end (P&L is not a goal). Off-switch = delete its `STRATEGY_METADATA` + `_STRATEGY_CLASSES` entries and redeploy.

**Last shipped fix:** B-13 `_set_market_data_type` threadsafe routing (2026-05-18, commits `d142517` + `03d2ab7`, deployed). Full root-cause writeup → `docs/HISTORY.md#b-13--_set_market_data_type-threadsafe-routing`. Companion fixes B-11 (thread-safety, three commits) and B-12 (fast-fill race) also in HISTORY.

**Last shipped infra:** F-DOC-08 — CLAUDE.md slim (this restructure, 2026-05-22). Phase 0 mechanical sweep + protocol Step 7 CR-mandate (2026-05-21 / 2026-05-22, PRs #260–#268, all on main).

**Dashboard:** Phase 5 read side complete (per-strategy view + CSV export `/api/strategies/{name}/fills?format=csv`). Profit-factor `+inf → null` wire-format fix shipped 2026-05-14.

**1 open code-review item:** CR-07 (`ib_insync` migration to `ib_async` fork — BACKLOG, multi-week).

### Immediate next steps

1. **Verify B-13 across the next nightly auto-restart** — Sun 2026-05-24 23:59 UTC should show a clean `Market data mode: delayed` log line after the daemon-thread reconnect, no `RuntimeError`, no `Error 10089` Monday morning.
2. **Phase 1 (safety floor)** — fail-fast `start_all`, `safe_place_protective_order` for bracket legs + grep tripwire, ntfy alerting on halt/CB/error storms + weekly synthetic ping. See `docs/IMPROVEMENT_PLAN.md` Phase 1.
3. **MS-C2 (P2)** — IBKR `reqHistoricalData` fallback for `_refresh_history`. **MEASUREMENT-GATED** — do not design or build before 2026-06-12 when `scripts/yfinance_outage_report.py` runs.
4. **GC-4 — TLS for the dashboard** (Caddy/nginx + tailscale-cert). Reprioritized into Phase 6 by IMPROVEMENT_PLAN but available as an immediate-next swap if preferred.
5. **F-OPS-02 backups** — daily sqlite + state-file off-VPS push + restore validator. **Blocked** on B2 bucket + app-key creation.

### Open decisions

- **Decision A:** Pay for IBKR live data (~$10–25/mo)? Not needed for daily-bar strategies — delayed data is fine. Needed for intraday.
- **Decision B:** Multi-strategy positions — **RESOLVED 2026-05-06**: independent 2% per strategy; each trade separate.

---

## VPS / Deployment

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
| Notification | ntfy.sh topic: see `NTFY_TOPIC` in `/opt/tradebot/.env` |
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
4. Verify: `ss -tlnp | grep 4001` shows LISTEN, then `sudo journalctl -fu tradebot` shows `Connected | account=<id>`

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
# How to run tests (matches the make pre-push / CI gate):
cd "C:\Users\galzi\OneDrive - Afiki-C\Afikim\TradeBot"
pytest tests/ -m "not market"
# TWS not running locally? Skip broker tests exactly as CI does:
GITHUB_ACTIONS=true pytest tests/ -m "not market"
```

---

## Architecture, layout & component reference

Project tree, architecture diagram, live-tick walkthrough, backtest example, and per-component contracts (IBKRClient, OrderManager, ReconnectManager, RiskManager, PositionSizer, BaseStrategy, DataFeed/IBKRFeed/BarScheduler, HistoricalDataLoader, BacktestEngine, TradeLog, Models) live in **`docs/REFERENCE.md`**. Read it when the focus is risk code, strategy/broker/runtime code, or backtest work — per `OPEN_SESSION_PROTOCOL.md` Step 4b.

---

## IBKR connection details

| Setting | Value |
|---|---|
| Account | `<account-id>` (paper) |
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

### Claude-specific rules (enforce every session — no exceptions)

GitHub branch protection on this repo allows admin-merge; Claude is still the primary enforcement layer.

1. **Always create a feature branch from `develop`**, never from `main`.
2. **Always use the `compare/<base>...<compare>` URL format for every PR link. Never use `pull/new/<branch>`.** `pull/new/<branch>` lets GitHub silently default the base to `main` regardless of what you write in prose — this caused two wrong-base merges.
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
| `CLAUDE.md` | This file — session-handoff index; current state + next steps + cross-refs |
| `docs/HISTORY.md` | Incident archaeology (B-NN root-cause writeups) + significant operational milestones |
| `docs/REFERENCE.md` | Architecture diagram + per-component contracts |
| `docs/IMPROVEMENT_PLAN.md` | Phased plan toward live 24/7 production (Phase 0 → Phase 7) |
| `docs/BACKLOG.md` | Categorized open items, reviewed every 5 sessions |
| `docs/ROADMAP.md` | Phased roadmap with acceptance checks |
| `OPEN_SESSION_PROTOCOL.md` | Opening ritual — read first on every chat (Steps 1–7 + Trigger Guide) |
| `CLOSE_SESSION_PROTOCOL.md` | Closing ritual + Session Score — loaded on a farewell signal |
| `SESSION_RULES.md` | Rules 1–13 + TradeBot engineering rules — loaded just-in-time via the Trigger Guide |
| `SESSION_PROTOCOL.md` | Navigation stub — routing table to the three split files |
| `WORKFLOW.md` | User-facing reference: chat archetypes, git rules, pre-push gate, red flags, emergency |
| `.claude/skills/` | Committed project skills: `session-rituals`, `deep-review` |
| `CHATLOG.md` | Session log, newest-first — read last 3 entries in opening ritual |
| `TODO.md` | Sprint-by-sprint task tracker |
| `docs/CHATLOG_ARCHIVE.md` | Archived older CHATLOG entries (created at session 10) |
| `.github/workflows/ci.yml` | CI pipeline: ruff → black → mypy → pytest → gitleaks → account-ID grep |
| `Makefile` | Local gate targets — `make pre-push` mirrors CI exactly |

## Files to always read before editing

| File | Why |
|---|---|
| `OPEN_SESSION_PROTOCOL.md` | Opening ritual — non-negotiable every session |
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
# Canonical gate (mirrors make pre-push / CI):
cd "C:\Users\galzi\OneDrive - Afiki-C\Afikim\TradeBot"
pytest tests/ -m "not market"

# TWS not running locally? Skip broker tests exactly as CI does:
GITHUB_ACTIONS=true pytest tests/ -m "not market"
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
- **`IBKRClient.connect()` is thread-safe via `run_coroutine_threadsafe`** — Python 3.12 provides no asyncio event loop in non-main threads. `ReconnectManager` calls `connect()` from a daemon thread; the fix saves the main loop on first call and uses `asyncio.run_coroutine_threadsafe(ib.connectAsync(), main_loop)` for reconnects. See `docs/HISTORY.md#b-08--reconnect-always-failing-asyncio-cross-thread` for the original incident. If you see "There is no current event loop in thread ReconnectManager" in logs, the fix in `broker/ibkr_client.py` is not deployed.
