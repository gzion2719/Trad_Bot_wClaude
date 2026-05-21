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

**Phase 6 — paper trading.** Bot running on VPS (paper account; SMACrossover-QQQ + RSI2MR-SPY + the PingPongTest-AAPL test-only strategy all live). **PingPongTest-AAPL shipped + deployed 2026-05-18** — a deliberately trivial alternating BUY 1 / SELL 1 AAPL strategy on `Interval(300)`, built only to make the bot visibly trade and verify the dashboard end-to-end (P&L is not a goal); `strategies/test_pingpong.py`, 35 tests `test_pp01..23`, pre- + post-impl CR, `tif=DAY` + market-hours gate + `_order_pending` self-heal + adopt-only-if-exact-qty reconcile. Off-switch = delete its `STRATEGY_METADATA` + `_STRATEGY_CLASSES` entries and redeploy. MS-I + MS-C3 deployed 2026-05-11 19:22 UTC. Dashboard Phase 4 fully deployed. **Profit-factor `+inf → null` wire-format fix shipped 2026-05-14** (string-sentinel `"Infinity"` from `_round_profit_factor`; renderer already accepted both forms; locked by `test_ds28` TestClient round-trip + `test_tl_pf_01..05` direct unit tests). **Dashboard Phase 5 (per-strategy view) — Sessions 1 + 2 + 3a + 3c shipped; read side complete.** S3c (2026-05-16, commit `959bb38`, branch `feature/strat-fills-csv-export`, PRs not yet opened) added `?format=csv` content-negotiation to `/api/strategies/{name}/fills` — buffered CSV (not streamed: `TradeLog.connection()` closes its sqlite conn on `__exit__`), server-side `_CSV_COLUMNS` mirrors the JS `_STRAT_HISTORY_COLS` constant (locked by `test_ds71`), 100k row cap → HTTP 413 (no silent truncation), formula-injection guard on string cells only (negative P&L stays numeric), UTF-8 BOM + RFC 4180 CRLF + RFC 6266 dual-form filename + `Cache-Control: no-store`; dependency order swapped so precedence is 401→404→400; frontend `<a id="strat-export" download>` href wired per-strategy. Pre- and post-impl CR both ran. `test_ds70..79`; **336 tests pass.** S3a (2026-05-15) bundled three stacked PRs: (a) **DB-X5 shared TestClient auth fixtures** in `tests/conftest.py` (`dashboard_token` / `dashboard_client` / `dashboard_client_unauth` + `_reset_all_rate_state` helper clearing BOTH `_rate_state` and `_SESSION_RATE_STATE`); retrofitted 13 callers in `test_dashboard.py` + ds28; added ds50..54 covering 401 paths on per-strategy endpoints. (b) **Per-strategy paginated history table** consuming existing `/fills` endpoint — toolbar (page-size 50/100/200/500 + status indicator), table with 8 columns mapped via module-level `_STRAT_HISTORY_COLS`, Prev/Next pager with Next disabled at server's 10k offset cap, single AbortController replaced on EVERY mutation (strategy switch / prev/next / page-size change), fully decoupled from the 30s summary poll, `aria-busy` + `title`-on-params for tooltip, empty-state branches on `_stratHistoryTotal`. (c) **CR-cycle-tracker-3b** chore branch closing 2 HIGH + 1 MEDIUM + L1 from the full-diff CR — explicit teardown in `dashboard_client`, db09/10/14/15 migrated to `monkeypatch`, `_js_decl_end` helper replacing brittle `js.find("\\n}\\n")` anchors, ds61 strengthened with regex operator extraction, ds69 locks `<th>` order + count + colspan against `_STRAT_HISTORY_COLS`. **326 tests pass.** Six CR rounds total (DB-X5 pre+post; 3b pre+second-opinion+post; chore pre+post). Second-opinion agent overturned the bundled-3b plan ("70% confidence is the tell") → CSV deferred to S3c via `?format=csv` on the existing endpoint. New `test_ds27` URL-drift tripwire from S2 still in force. **MS-A1+A2, MS-D, MS-B, MS-K, eager-save migration, MS-C, MS-J, MS-I, MS-C3** all shipped. State schema v2 with `partial_fill_halt` persistence. **B-11 thread-safety fix — THREE commits, root cause confirmed via ib_insync source (2026-05-15).** Branch `feature/ibkr-client-thread-safe-market-data` head is `554caf4` — PRs not yet merged + VPS not yet deployed. Symptom: PingPong had 0 fills since deploy. Layer 1 (`fff3950`): `_needs_threadsafe_route()` auto-detect + `run_coroutine_threadsafe` routing in `qualify_contract`/`get_market_price`/`get_account_summary`/`get_positions`/`is_alive`/`sleep`. **Deployed to VPS, still failing** with "There is no current event loop in thread 'Sched-PingPongTest-AAPL'". Layer 2 (`b8ec0da`): wrap each `*Async` call in inner `async def` (e.g. `_qualify`, `_fetch_summary`, `_heartbeat`) so ib_insync's `Async` coroutine is *created* on the main-loop thread, not in the daemon. **Deployed, still failing identically.** Layer 3 (`554caf4`, the actual fix): read ib_insync source — `Client.sendMsg()` ALWAYS calls `getLoop()` → `asyncio.get_event_loop_policy().get_event_loop()` from the calling thread; raises from any daemon. `IB.placeOrder`/`cancelOrder` both go through `sendMsg`. Added `IBKRClient.ib_place_order()` + `ib_cancel_order()` with the same inner-coroutine routing pattern; migrated `OrderManager.place_order`/`cancel_order`/`cancel_all` to use them; extended TS-07 grep tripwire to ban `placeOrder`/`cancelOrder` outside `ibkr_client.py`; added TS-12 + TS-13. **387 tests pass.** Process lesson logged: when an asyncio "no current event loop" error persists after a routing fix, read the WIRE-LAYER source (`Client.send` → `sendMsg`) before adding another wrapper. ib_insync's `getLoop()` is called from `sendMsg`, so every `IB.*` method that touches the socket is broken from a daemon thread unless routed.

**B-12 — PingPong fast-fill race fixed (2026-05-15, `a932205`, PRs #242/#243 merged).** Post-B-11 deploy, PingPong placed exactly one BUY at 17:21:49 then went silent for 21+ min. Independent CR-skill review caught two compounding bugs both prior PingPong CRs (5/18 pre+post) missed: (BLOCKING) `test_pingpong.py:on_tick` re-set `_order_pending=True` AFTER `safe_place_order` returned, overwriting `on_fill`'s `_clear_pending()` when a fast MKT fill arrived inside `place_order`'s internal `_client.sleep(0.5)`. (MAJOR M1) `order_manager.py:place_order` wrote `_strategy_name_by_order_id` AFTER the sleep, so a fill event during the sleep built `OrderResult.strategy_name=None` and `BaseStrategy._dispatch_on_fill` filtered the callback out — strategy never saw its own fill. Fix: stamp `_strategy_name_by_order_id` BEFORE `_client.sleep`; in `on_tick` arm pending BEFORE `safe_place_order`, clear in exception paths, only stamp `_pending_order_id` post-call if pending survived. Tests `test_pp24`, `test_pp25`, `test_ms12`. **392 tests pass.** WORKFLOW.md gains the "Pending-flag pattern CR checklist" so the trace is verified on every future strategy with an in-flight guard. Side-investigation: journal grep showed SMA + RSI2MR placed ZERO orders in a week and `data/health.txt` mtime = `2026-05-14 20:10:00 UTC` (exact 16:10 ET DailyAt) → both daily strategies are healthy-and-quiet (no signal), not silently broken by M1.

**B-13 — `_set_market_data_type` threadsafe routing (2026-05-18, `d142517` + post-CR tighten `03d2ab7`, deployed + verified).** Symptom: PingPongTest-AAPL had 0 fills today. Every 5-min `on_tick` called `get_market_price(AAPL)` → IBKR returned `Error 10089 ("Requested market data requires additional subscription ... Delayed market data is available")` → poll timed out → no order placed. SMA-QQQ + RSI2MR-SPY weren't affected (they pull historical data from yfinance, not real-time via IBKR). Root cause via VPS journal: at Sun 23:59 UTC the gateway AutoRestartTime dropped the TCP connection; ReconnectManager's daemon-thread `connect()` reached the post-handshake `_set_market_data_type(DELAYED)` step at line 146; the method called `self.ib.reqMarketDataType(mode)` directly, which goes `IB.reqMarketDataType → Client.send → Client.sendMsg → getLoop()` and raises `RuntimeError: no current event loop in thread 'ReconnectManager'` from the daemon. The exception was caught by `connect()`'s broad except; subsequent ReconnectManager calls hit `if self.ib.isConnected(): return` at line 84 (TCP was still up from the prior partial-success), so the data mode was never re-applied. TWS resets the mode to REALTIME on every fresh session → every reqMktData on a paper account returns 10089. Fix: same auto-route pattern as `qualify_contract`/`ib_place_order`/`ib_cancel_order` — `_needs_threadsafe_route()` → inner `async def _set` → `run_coroutine_threadsafe`, new `_MKT_DATA_TYPE_TIMEOUT = 5`. Audit confirms `_set_market_data_type` was the last unrouted `sendMsg` call in the file. TS-14 + TS-15 lock the regression positively (record-thread + monkeypatch-spy on `run_coroutine_threadsafe`); TS-07 grep tripwire extended to forbid `reqMarketDataType` outside `ibkr_client.py`. **Closes Bug A** (deferred from B-10 — same root cause class). **390 passed / 49 skipped** (TWS-dependent skipped under `GITHUB_ACTIONS=true`).

**1 open code-review item:** CR-07 (`ib_insync` migration to `ib_async` fork — BACKLOG, multi-week).

**Immediate next steps:**
1. **Verify B-13 across the next nightly auto-restart** — Sun 2026-05-24 23:59 UTC should show a clean `Market data mode: delayed` log line after the daemon-thread reconnect, no `RuntimeError`, no `Error 10089` Monday morning.
2. **MS-C2 (P2)** — IBKR `reqHistoricalData` fallback for `_refresh_history`. Per the "Describe-from-source rule" (2026-05-17): MEASUREMENT-GATED. Do not design or build before 2026-06-12 when `scripts/yfinance_outage_report.py` runs.
3. **GC-4 — TLS for the dashboard** (Caddy/nginx + tailscale-cert). Only unblocked roadmap build item.
4. **Paper trading monitoring** — `TradeLog.daily_summary()` daily (ROADMAP 6.1, 6.2).

### What was done this session (2026-05-15 — B-11 IBKRClient thread-safety fix)

**Root cause diagnosis + proper fix for PingPong zero fills (commit `fff3950`, branch `feature/ibkr-client-thread-safe-market-data`):**

Root cause confirmed from VPS journal: `RuntimeWarning: coroutine 'IB.qualifyContractsAsync' was never awaited`. Every strategy's `on_tick()` runs on a daemon scheduler thread. `on_tick` → `get_market_price` → `qualify_contract` → `ib.qualifyContracts()` → `loop.run_until_complete()` while the main asyncio loop is already running on the main thread → `RuntimeError` → swallowed by PingPong's broad `except (ValueError, RuntimeError)` → silent no-op every tick → zero fills.

Same latent bug class identified in: `OrderManager.place_order/cancel_order/cancel_all` (all called `self._ib.sleep(0.5)`), `is_alive()` (`ib.reqCurrentTime`), `get_account_summary`, `get_positions`. SMA/RSI2MR worked by accident: `accountSummary()` hits cache on non-first calls; neither strategy has placed a real order yet.

Pre-impl CR returned NO-GO (2 BLOCKING found: `time.time()` → `loop.time()` inside coroutine; cold-start daemon caller should raise not fall through). Both findings folded before implementation.

**Changes:**
- `broker/ibkr_client.py`: `_needs_threadsafe_route()` raises on cold-start (daemon + no loop) and stopped loop; `qualify_contract`, `get_market_price`, `get_account_summary`, `get_positions`, `is_alive` all auto-route via `run_coroutine_threadsafe`; `sleep()` inline check (no `_needs_threadsafe_route`) falls back to `time.sleep()` on cold-start/stopped loop; `_ACCOUNT_TIMEOUT` constant added; all future timeouts include `_THREADSAFE_RESULT_SLACK`; `_get_market_price_async` uses `loop.time()` + `await asyncio.sleep`; `get_account_summary_threadsafe`/`get_positions_threadsafe` kept as one-liner aliases (BACKLOG TS-CLEANUP); `is_delayed` comment added; deprecated alias docstrings reference BACKLOG.
- `broker/order_manager.py`: 3× `self._ib.sleep(0.5)` → `self._client.sleep(0.5)`.
- `tests/conftest.py`: `client.ib.sleep()` × 2 → `client.sleep()`; `bg_event_loop` fixture added.
- `tests/test_ibkr_client_threadsafe.py`: new file, 11 tests TS-01..TS-11 (main vs daemon routing, timeout raises, grep tripwire, sleep helper, is_alive, cold-start, stopped-loop).
- `tests/test_test_pingpong.py`: `test_pp23` daemon-thread on_tick tripwire added.
- `WORKFLOW.md`: "IBKRClient method thread-safety rule" section added.
- `docs/BACKLOG.md`: TS-CLEANUP entry added.
- `TODO.md`: B-11 bug entry added.

Post-impl CR findings and fixes: B-1 (`sleep()` cold-start fallback), B-2 (`sleep()` Future timeout 11×), M-1 (conftest `ib.sleep` + TS-07 tripwire), M-2 (`_ACCOUNT_TIMEOUT` constant), M-3 (missing `_THREADSAFE_RESULT_SLACK` on qualify/account/positions), M-4 (`is_delayed` comment), m-1 (TS-11 spin-wait → Event-based), m-3 (alias BACKLOG refs). All resolved.

**Test count: 385 pass** (11 new TS tests + test_pp23; 42 TWS-connection errors pre-existing).
**Gate: ruff ✅ black ✅ mypy ✅ pytest ✅**

### What was done this session (2026-05-08 — RSI2-MR strategy, ROADMAP 4.6)

**RSI2-MR SPY mean-reversion strategy — full implementation cycle (commit `55cb168`, PR #147):**

New files:
- `strategies/rsi2_mr.py` — RSI2MR_SPY strategy: entry (RSI(2)≤10 + SMA(200) regime gate + VIX≤35), bracket orders (GTC STP + LMT), 8-bar time stop, RSI(2)≥70 exit, circuit-breaker (5 consecutive losses → halt until next month), state persistence to `data/rsi2_mr_state.json`, 6 tunable params at spec ceiling.
- `strategies/_indicators.py` — `sma()`, `rsi_wilder()`, `atr_wilder()` with Wilder smoothing.
- `data/vix_feed.py` — `VIXFeed` (backtest date-keyed + live yfinance); `load_vix_series()` factory.
- `config/calendars/fomc.py` — `is_fomc_day()` from hardcoded FOMC dates.
- `config/calendars/market_calendar.py` — `is_russell_rebalance_window()`, `is_pre_long_holiday_closure()`, `next_trading_day()`.
- `tests/test_rsi2_mr.py` — 45 tests (Sections A–F): indicator unit tests, calendar tests, feed external-series tests, bracket simulator tests, strategy logic tests, full integration tests. All 45 pass.

Modified files:
- `backtester/engine.py` — bracket simulator (STP gap-through, LMT no-slippage, GTC persistence, triggered-but-INACTIVE orders discarded not re-queued), external sidecar series (`external_data: Dict[str, pd.Series]`), `current_equity()` on MockOrderManager.
- `models/order.py` — `backtest_slippage_bps` field on `OrderRequest`.
- `runtime/strategy_runner.py` — `_make_trade_log_hook` passes `strategy.params`.
- `tests/test_multi_strategy_runner.py` — fixed `_FakeTradeLog.record()` kwarg.
- `requirements.txt` — no new deps (exchange-calendars already present).

**Baseline backtest 2006-2025** ($50k equity, $1 commission): 67 completed round-trips, 59.7% win rate, Sharpe 0.34, max DD -8.5%, profit factor 1.48, mean R-multiple +0.16.

**CR findings (20 issues) and fixes applied:**
- CRITICAL: `avg_fill_price or` → `if avg_fill_price is not None` in `on_fill(BUY)`.
- CRITICAL: `_exit()` fallback price uses `_entry_price` not 0.0 on cold restart.
- CRITICAL: Exception path in entry no longer zeros stop/target (live broker race — on_fill may still arrive).
- HIGH: `_bars_held` incremented *before* `_check_exits` (was off-by-one — held 9 bars instead of 8).
- MEDIUM: `_get_vix` unreachable branch removed; test_fi08 state-file isolated; `external_data` key normalized to lowercase `"vix"` throughout.

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
- Recovery: VNC tunnel → IB Gateway login → SMS code → `tradebot.service` restart. Reconnected to &lt;account-id&gt; in <30 seconds.

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
4. Verify: `ss -tlnp | grep 4001` shows LISTEN, then `sudo journalctl -fu tradebot` shows `Connected | account=&lt;account-id&gt;`

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
    ├── test_*.py           — pytest suite; canonical gate is `pytest tests/ -m "not market"`
    ├── run_tests.py        — legacy custom runner (still present; pytest is the source of truth)
    └── run_market_tests.py — tests requiring live market hours
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
| Account | &lt;account-id&gt; (paper) |
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
| `OPEN_SESSION_PROTOCOL.md` | Opening ritual — read first on every chat (Steps 1–7 + Trigger Guide) |
| `CLOSE_SESSION_PROTOCOL.md` | Closing ritual + Session Score — loaded on a farewell signal |
| `SESSION_RULES.md` | Rules 1–13 + TradeBot engineering rules — loaded just-in-time via the Trigger Guide |
| `SESSION_PROTOCOL.md` | Navigation stub — routing table to the three split files |
| `WORKFLOW.md` | User-facing reference: chat archetypes, git rules, pre-push gate, red flags, emergency |
| `.claude/skills/` | Committed project skills: `session-rituals`, `deep-review` |
| `CHATLOG.md` | Session log, newest-first — read last 3 entries in opening ritual |
| `TODO.md` | Sprint-by-sprint task tracker |
| `docs/ROADMAP.md` | Phased roadmap with acceptance checks |
| `docs/BACKLOG.md` | Categorized open items, reviewed every 5 sessions |
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
- **`IBKRClient.connect()` is thread-safe via `run_coroutine_threadsafe`** — Python 3.12 provides no asyncio event loop in non-main threads. `ReconnectManager` calls `connect()` from a daemon thread; the fix saves the main loop on first call and uses `asyncio.run_coroutine_threadsafe(ib.connectAsync(), main_loop)` for reconnects. If you see "There is no current event loop in thread ReconnectManager" in logs, the fix in `broker/ibkr_client.py` is not deployed.
