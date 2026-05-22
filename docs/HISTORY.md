# Project History — Incidents & Operational Milestones

Long-form archaeology that used to live inside `CLAUDE.md`'s "Current state" section. Extracted on 2026-05-22 (F-DOC-08) so the operator-readable index in `CLAUDE.md` stays ≤150 lines. `CHATLOG.md` remains the authoritative session log — this file is for the prose deep-dives that don't fit a 5-bullet session entry.

**Anchor convention.** Incident headings are `## B-NN — <name>`; milestone headings are dated (`## YYYY-MM-DD — <name>`). Linkable from `CLAUDE.md` as `docs/HISTORY.md#b-13--_set_market_data_type-threadsafe-routing` etc., per GitHub anchor slugification.

---

## Incidents (B-NN)

### B-08 — Reconnect always-failing (asyncio cross-thread)

**Date:** 2026-05-02. **Source:** `CHATLOG.md` 2026-05-02 "Reconnect asyncio threading fix" + git log.

**Symptom.** Every `ReconnectManager` reconnect attempt failed before reaching IBKR.

**Diagnosis.** `ib_insync` calls `asyncio.get_event_loop()` internally; Python 3.12 raises `RuntimeError` in non-main threads. `ReconnectManager` runs on a daemon thread.

**Fix.** `broker/ibkr_client.py` — save the main event loop on first `connect()` call (main thread); on subsequent calls from daemon thread use `asyncio.run_coroutine_threadsafe(ib.connectAsync(), main_loop)`. Replaced `ib.sleep()` with `time.sleep()` in post-connect poll.

**Tests / gate.** ruff/black/mypy clean. Deployed via `git pull origin main && systemctl restart tradebot`; bot confirmed connected (PID 52545).

**Branch / PR.** `feature/fix-reconnect-asyncio-thread` → develop → main.

---

### B-11 — IBKRClient thread-safety (three commits to root cause)

**Date:** 2026-05-15. **Commits:** `fff3950` (Layer 1), `b8ec0da` (Layer 2), `554caf4` (Layer 3). **Branch:** `feature/ibkr-client-thread-safe-market-data`.

**Symptom.** PingPongTest-AAPL had 0 fills since deploy. VPS journal: `RuntimeWarning: coroutine 'IB.qualifyContractsAsync' was never awaited`.

**Diagnosis.** Every strategy's `on_tick()` runs on a daemon scheduler thread. `on_tick` → `get_market_price` → `qualify_contract` → `ib.qualifyContracts()` → `loop.run_until_complete()` while the main asyncio loop is already running on the main thread → `RuntimeError` → swallowed by PingPong's broad `except (ValueError, RuntimeError)` → silent no-op every tick → zero fills. Same latent bug class identified in `OrderManager.place_order/cancel_order/cancel_all` (all called `self._ib.sleep(0.5)`), `is_alive()`, `get_account_summary`, `get_positions`. SMA/RSI2MR worked by accident: `accountSummary()` hits cache on non-first calls; neither strategy had placed a real order yet.

**Three-layer fix (process lesson included).**
- **Layer 1 (`fff3950`):** `_needs_threadsafe_route()` auto-detect + `run_coroutine_threadsafe` routing in `qualify_contract`/`get_market_price`/`get_account_summary`/`get_positions`/`is_alive`/`sleep`. Deployed to VPS → still failing with "There is no current event loop in thread 'Sched-PingPongTest-AAPL'".
- **Layer 2 (`b8ec0da`):** wrap each `*Async` call in inner `async def` (e.g. `_qualify`, `_fetch_summary`, `_heartbeat`) so ib_insync's `Async` coroutine is *created* on the main-loop thread, not in the daemon. Deployed → still failing identically.
- **Layer 3 (`554caf4`, the actual fix):** read ib_insync source — `Client.sendMsg()` ALWAYS calls `getLoop()` → `asyncio.get_event_loop_policy().get_event_loop()` from the calling thread; raises from any daemon. `IB.placeOrder`/`cancelOrder` both go through `sendMsg`. Added `IBKRClient.ib_place_order()` + `ib_cancel_order()` with the same inner-coroutine routing pattern; migrated `OrderManager.place_order`/`cancel_order`/`cancel_all` to use them. Extended TS-07 grep tripwire to ban `placeOrder`/`cancelOrder` outside `ibkr_client.py`; added TS-12 + TS-13.

**Pre-impl CR.** NO-GO at Layer 1 plan — 2 BLOCKING (`time.time()` → `loop.time()` inside coroutine; cold-start daemon caller should raise not fall through). Both folded before implementation. **Post-impl CR.** B-1 (`sleep()` cold-start fallback), B-2 (`sleep()` Future timeout 11×), M-1 (conftest `ib.sleep` + TS-07 tripwire), M-2 (`_ACCOUNT_TIMEOUT` constant), M-3 (missing `_THREADSAFE_RESULT_SLACK` on qualify/account/positions), M-4 (`is_delayed` comment), m-1 (TS-11 spin-wait → Event-based), m-3 (alias BACKLOG refs). All resolved.

**Tests / gate.** `tests/test_ibkr_client_threadsafe.py` new (TS-01..TS-11) + `test_pp23` daemon-thread on_tick tripwire. 387 pass; ruff/black/mypy ✅.

**Other changes.** `broker/order_manager.py` 3× `self._ib.sleep(0.5)` → `self._client.sleep(0.5)`. `tests/conftest.py` `bg_event_loop` fixture. `WORKFLOW.md` "IBKRClient method thread-safety rule" added. `docs/BACKLOG.md` TS-CLEANUP entry. `TODO.md` B-11 entry.

**Process lesson.** When an asyncio "no current event loop" error persists after a routing fix, read the WIRE-LAYER source (`Client.send` → `sendMsg`) before adding another wrapper. ib_insync's `getLoop()` is called from `sendMsg`, so every `IB.*` method that touches the socket is broken from a daemon thread unless routed. Codified in WORKFLOW.md.

---

### B-12 — PingPong fast-fill race (pending overwrite + strategy_name late-write)

**Date:** 2026-05-15. **Commit:** `a932205`. **PRs:** #242, #243 (merged).

**Symptom.** Post-B-11 deploy, PingPong placed exactly one BUY at 17:21:49 then went silent for 21+ min.

**Diagnosis (independent CR-skill review caught both — both prior PingPong CRs from 5/18 pre+post missed them).**
- **BLOCKING.** `test_pingpong.py:on_tick` re-set `_order_pending=True` AFTER `safe_place_order` returned, overwriting `on_fill`'s `_clear_pending()` when a fast MKT fill arrived inside `place_order`'s internal `_client.sleep(0.5)` window.
- **MAJOR M1.** `order_manager.py:place_order` wrote `_strategy_name_by_order_id` AFTER the sleep, so a fill event during the sleep built `OrderResult.strategy_name=None` and `BaseStrategy._dispatch_on_fill` filtered the callback out — strategy never saw its own fill.

**Fix.** Stamp `_strategy_name_by_order_id` BEFORE `_client.sleep`. In `on_tick` arm `_order_pending` + `_pending_since` BEFORE `safe_place_order`, clear in all exception paths, only stamp `_pending_order_id` post-call if pending survived.

**Tests / gate.** `test_pp24` (synchronous BUY fill mid-place_order → pending stays False), `test_pp25` (same for SELL), `test_ms12` (strategy_name visible inside the `_client.sleep` mock). **392 pass.** WORKFLOW.md "Pending-flag pattern CR checklist" added.

**Side investigation.** User asked if M1 could have hidden SMA/RSI2MR fills too. Journal grep showed both placed ZERO orders in a week — only lifecycle messages. `data/health.txt` mtime was `2026-05-14 20:10:00 UTC` exactly = 16:10 ET = the DailyAt tick — both daily strategies are healthy-and-quiet (no signal), not silently broken; M1 hid nothing for them.

---

### B-13 — `_set_market_data_type` threadsafe routing

**Date:** 2026-05-18. **Commits:** `d142517` + post-CR tighten `03d2ab7`. Deployed + verified.

**Symptom.** PingPongTest-AAPL had 0 fills today. Every 5-min `on_tick` called `get_market_price(AAPL)` → IBKR returned `Error 10089 ("Requested market data requires additional subscription ... Delayed market data is available")` → poll timed out → no order placed. SMA-QQQ + RSI2MR-SPY weren't affected (they pull historical data from yfinance, not real-time via IBKR).

**Diagnosis via VPS journal.** At Sun 23:59 UTC the gateway AutoRestartTime dropped the TCP connection; ReconnectManager's daemon-thread `connect()` reached the post-handshake `_set_market_data_type(DELAYED)` step at line 146; the method called `self.ib.reqMarketDataType(mode)` directly, which goes `IB.reqMarketDataType → Client.send → Client.sendMsg → getLoop()` and raises `RuntimeError: no current event loop in thread 'ReconnectManager'` from the daemon. The exception was caught by `connect()`'s broad except; subsequent ReconnectManager calls hit `if self.ib.isConnected(): return` at line 84 (TCP was still up from the prior partial-success), so the data mode was never re-applied. TWS resets the mode to REALTIME on every fresh session → every reqMktData on a paper account returns 10089.

**Fix.** Same auto-route pattern as `qualify_contract`/`ib_place_order`/`ib_cancel_order` — `_needs_threadsafe_route()` → inner `async def _set` → `run_coroutine_threadsafe`. New `_MKT_DATA_TYPE_TIMEOUT = 5`. Audit confirms `_set_market_data_type` was the last unrouted `sendMsg` call in the file.

**Tests / gate.** TS-14 + TS-15 lock the regression positively (record-thread + monkeypatch-spy on `run_coroutine_threadsafe`); TS-07 grep tripwire extended to forbid `reqMarketDataType` outside `ibkr_client.py`. **390 passed / 49 skipped** (TWS-dependent skipped under `GITHUB_ACTIONS=true`).

**Closes** the deferred "Bug A" from B-10 (same root cause class).

---

## Operational milestones

### 2026-05-08 — RSI2-MR SPY mean-reversion strategy shipped (ROADMAP 4.6)

**Commit `55cb168`, PR #147.**

**New files.**
- `strategies/rsi2_mr.py` — RSI2MR_SPY: entry (RSI(2)≤10 + SMA(200) regime gate + VIX≤35), bracket orders (GTC STP + LMT), 8-bar time stop, RSI(2)≥70 exit, circuit-breaker (5 consecutive losses → halt until next month), state persistence to `data/rsi2_mr_state.json`, 6 tunable params at spec ceiling.
- `strategies/_indicators.py` — `sma()`, `rsi_wilder()`, `atr_wilder()` with Wilder smoothing.
- `data/vix_feed.py` — `VIXFeed` (backtest date-keyed + live yfinance); `load_vix_series()` factory.
- `config/calendars/fomc.py` — `is_fomc_day()` from hardcoded FOMC dates.
- `config/calendars/market_calendar.py` — `is_russell_rebalance_window()`, `is_pre_long_holiday_closure()`, `next_trading_day()`.
- `tests/test_rsi2_mr.py` — 45 tests (Sections A–F): indicator unit tests, calendar tests, feed external-series tests, bracket simulator tests, strategy logic tests, full integration tests. All 45 pass.

**Modified files.**
- `backtester/engine.py` — bracket simulator (STP gap-through, LMT no-slippage, GTC persistence, triggered-but-INACTIVE orders discarded not re-queued), external sidecar series (`external_data: Dict[str, pd.Series]`), `current_equity()` on MockOrderManager.
- `models/order.py` — `backtest_slippage_bps` field on `OrderRequest`.
- `runtime/strategy_runner.py` — `_make_trade_log_hook` passes `strategy.params`.
- `tests/test_multi_strategy_runner.py` — fixed `_FakeTradeLog.record()` kwarg.

**Baseline backtest 2006–2025** ($50k equity, $1 commission): 67 completed round-trips, 59.7% win rate, Sharpe 0.34, max DD -8.5%, profit factor 1.48, mean R-multiple +0.16.

**CR findings (20 issues, all fixed).** Highlights:
- CRITICAL: `avg_fill_price or` → `if avg_fill_price is not None` in `on_fill(BUY)`.
- CRITICAL: `_exit()` fallback price uses `_entry_price` not 0.0 on cold restart.
- CRITICAL: Exception path in entry no longer zeros stop/target (live broker race — on_fill may still arrive).
- HIGH: `_bars_held` incremented *before* `_check_exits` (was off-by-one — held 9 bars instead of 8).
- MEDIUM: `_get_vix` unreachable branch removed; test_fi08 state-file isolated; `external_data` key normalized to lowercase `"vix"` throughout.

---

### 2026-05-02 — Dashboard Phase 2 + weekend-aware stale threshold (RECONSTRUCTED)

This entry was reconstructed in the next session because the originating chat ended on an API error before the closing ritual could run. Source: full chat transcript + git log of commits `d3e286d`, `b6515f4` and PRs #30/#31/#32/#33.

**Phase 2 — IB Gateway status + bot uptime/PID (`d3e286d`, PRs #30 → develop, #31 → main).**
- New endpoint `GET /api/system` returns `bot_pid`, `bot_uptime_seconds`, `bot_service_status`, `gateway_pid`, `gateway_uptime_seconds`, `gateway_service_status`, plus port 4001 listen check.
- Implementation reads `systemctl show <service> --property=MainPID,ActiveEnterTimestamp` and `systemctl is-active <service>`. Degrades gracefully on dev PC / Windows where systemctl is unavailable.
- Dashboard UI gained a "System" card with green pulsing dot when gateway is active, bot uptime in human-readable form, and port-open indicator.
- User confirmed live read on VPS via Tailscale: gateway active ✅, bot PID 52545 ✅, uptime 6.3h ✅, port 4001 open ✅.

**Weekend-aware stale threshold fix (`b6515f4`, PRs #32 → develop, #33 → main).**
- Dashboard showed Liveness "stale" (last tick 42.5h ago = Friday Apr 30 20:10 UTC) on a Saturday. Initial wrong-path: hypothesized `BarScheduler` stopped after 5 consecutive `on_tick()` exceptions. User intuited the actual cause: it was the weekend.
- Root cause: SMA strategy doesn't use `BarScheduler` — it uses a custom `_daily_scheduler` in `main.py` that fires `on_tick()` once per day at 16:10 ET. Weekend gap = ~72h, but the dashboard's hardcoded `_STALE_AFTER_SECONDS = 26h` threshold wasn't aware of this. Bot was healthy the whole time; alarm was a false positive.
- Fix in `dashboard/app.py`: replaced constant with `_stale_threshold_seconds()` returning 80h on Saturday/Sunday/Monday-before-16:10-ET, 26h on regular trading days. Updated DB-03/DB-04 tests to cover both branches. ruff/black/mypy all ✅.
- Process improvement codified in `WORKFLOW.md` "Debugging discipline" section: before hypothesizing failure modes for a "stopped" symptom, read the producer to confirm expected cadence.

---

### 2026-05-02 — Dashboard Phase 1 read-only (ROADMAP 5.7)

- New `dashboard/` module with FastAPI app: `dashboard/app.py` (routes), `dashboard/__main__.py` (uvicorn entry), `dashboard/static/index.html` (auto-polling UI, dark theme, refreshes every 5s).
- Endpoints: `GET /api/health` (reads `data/health.txt`, classifies ok/stale/missing/unreadable against the same 26h threshold as `tradebot-health.timer`), `GET /api/today` (`TradeLog.daily_summary()`), `GET /api/recent-fills?limit=N` (clamped 1–200), `GET /api/info` (account/host/port metadata).
- New systemd unit `deploy/systemd/tradebot-dashboard.service`: separate process from `tradebot.service` so a dashboard crash cannot affect the live bot. Binds `127.0.0.1:8080`. Reach via Tailscale `http://100.113.140.69:8080` or `ssh -L 8080:localhost:8080 chappy-vps`. **Never expose publicly without HTTP auth + TLS.**
- Added `fastapi>=0.110.0` and `uvicorn[standard]>=0.27.0` to `requirements.txt`.
- Added 6 tests (DB-01 through DB-06) — exercise route functions directly (no HTTP layer / no httpx dep). All 6 pass locally.
- ruff ✅ black ✅ mypy ✅ (mypy uses `--ignore-missing-imports` so FastAPI lack of stubs is fine).
- **Scope deliberately limited to read-only.** Control plane (kill/restart bot) and IB Gateway login surface (replace VPN VNC 2FA) are explicitly deferred — separate phases tracked in BACKLOG.

---

### 2026-05-01 — Code quality gate (ruff + black + mypy all pass)

- Created `pyproject.toml` — ruff config (ignores E402 intentional docstring pattern, E702 intentional semicolons in test runner); black line-length=100.
- Ran `ruff check --fix`: auto-fixed 22 issues (unused imports, f-strings without placeholders, multiple imports on one line, redefined var).
- Fixed 8 F841 unused-variable issues manually in `tests/run_tests.py` and `tests/run_market_tests.py`.
- Ran `black .`: auto-formatted 23 files.
- Fixed 15 mypy errors across 5 files:
  - `backtester/metrics.py`: added None guards for Optional[float] in `win_rate()` and `profit_factor()`.
  - `data/feed.py`: added `# type: ignore[attr-defined]` for ib_insync's `updateEvent` (untyped lib).
  - `broker/order_manager.py`: asserted non-None before passing prices to IB order constructors; annotated `avg_price: Optional[float]`.
  - `strategies/sma_crossover.py`: **bug fix** — `get_account_summary()` returns a list not a dict; fixed `_get_equity()` to build dict comprehension first (`{s.tag: s.value for s in ...}`).
  - `main.py`: added `# type: ignore[assignment]` on `timezone` fallback lines; changed `TradeLog(db_path="...")` to use `Path(...)`.

---

### 2026-04-30 — IBKR info-code noise fix + 6-day outage recovery + IB Gateway systemd transition

**IBKR info-code noise fix (`broker/order_manager.py`) — PR #9 merged to develop.**
- Codes 1100/1102/2103/2105/2107/2157 were missing from all sets → fell through to `logger.error()` → flooded `journalctl`.
- New three-tier classification: `_DEBUG_CODES` (silent), `_INFO_CODES` (→ INFO), `_WARNING_CODES` (→ WARNING).
- 1100 (connectivity lost) → WARNING; 1102/2103/2105/2107/2157 (restored/data farm) → INFO; real errors unchanged at ERROR.

**Recovered bot from 6-day outage (Apr 24 → Apr 30).**
- Root cause: IBKR's weekly token reset on Sunday Apr 26 (~01:00 ET) invalidated the gateway session — stuck at 2FA prompt all week.
- Recovery: VNC tunnel → IB Gateway login → SMS code → `tradebot.service` restart. Reconnected to account in <30 seconds.

**IB Gateway transitioned to full systemd management.**
- Created 3 new systemd units: `xvfb.service`, `x11vnc.service`, `ibgateway.service`. All enabled for auto-start on boot.
- `tradebot.service` already had `Requires=ibgateway.service` from prior work — chain works end-to-end.
- Replaced the old backgrounded/disowned IB Gateway process with proper supervision.
- `x11vnc` now always running on `:99` listening on `localhost` only (must use SSH tunnel).

**IBC config hardening.** Added `ReloginAfterSecondFactorAuthenticationTimeout=yes` to `/opt/ibc/config.ini` — IBC will auto-restart the login flow if a 2FA prompt times out.

**Researched IBKR 2FA constraints (and corrected earlier wrong advice).**
- IBKR's `AutoRestartTime` keeps gateway sessions alive **for up to a week** with no 2FA needed for daily restarts. IBC was already configured with this.
- **2FA is required ONCE per week** — Sunday ~01:00 ET when IBKR servers invalidate all tokens. Mon–Sat restarts use the cached token, no human action needed.
- Owner is enrolled in **Interactive IL Key** (Israeli code-generator variant), not push-notification IB Key. Push 2FA appears unavailable for Israeli accounts — needs IBKR support inquiry.
- IBKR has **revoked all 2FA opt-out paths** for trading. There is no API key, service account, or Trusted IP bypass. Weekly 2FA is the regulatory floor.
