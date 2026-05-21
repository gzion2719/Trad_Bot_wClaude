# TradeBot — Improvement Plan toward Production-Grade 24/7 Multi-Strategy

**Created:** 2026-05-21
**Revised:** 2026-05-21 (after unbiased plan review — 3 BLOCKING, 8 MAJOR resolved)
**Source:** Four parallel deep-review subagents covering (1) runtime & multi-strategy architecture, (2) broker/risk/reliability, (3) dashboard UX/data/security, (4) repo/CI/ops/security. Plan was then independently stress-tested by a fifth reviewer; revisions inline.
**Goal:** A live, robust, secured, production-grade trading bot running 24/7 on the VPS, capable of operating several independent strategies, with a dashboard that gives a 5-second-glance health verdict.

> This plan is intentionally **dependency-ordered**, not priority-flat. Earlier phases unblock later ones. Each phase is sized for 1–2 working sessions. Phases 0–3 are pre-requisites to flipping to a live (real-money) account. Phases 4–6 are required to scale comfortably past 3 strategies. Phase 7 is long-term architecture.

---

## Executive summary

**Where we are today.** The bot is functionally sound for paper-trading 2–3 strategies. Thread-safety (B-08→B-13) is now coherent; PR discipline is real; CI is broad; the dashboard exists and is well-secured at the network layer. The recent incident history (silent zero-fills) is mostly *closed wounds*.

**Where we are NOT today.**
1. **Silent-failure detection is the dominant weakness.** A dead strategy or a tripped circuit breaker is invisible to both the operator and the dashboard until the next P&L review.
2. **Risk enforcement has two real holes** — bracket-leg orders bypass the RiskManager, and the daily-loss halt depends on a `cost_basis` value that is `None` for every live fill.
3. **Multi-strategy isolation is conventional, not architectural.** All strategies share one IBKR connection, one asyncio loop, one OrderManager, and a broadcast callback list with no per-callback try/except.
4. **Plug-in ergonomics are file-edit-heavy.** Adding a strategy requires three coordinated edits; the failure mode (forgetting one) is only partly caught by a sync test.
5. **No backups, no off-VPS logs, no rollback story, ~87-branch graveyard** (98 remote branches total, 87 already merged into main but never deleted). Operationally not yet production-grade.
6. **Dashboard works for 1 strategy at a time.** No overview row, no per-strategy heartbeat, no risk/halt surfacing, no mobile layout.

**Verdict on the original end-goal.** Achievable in **6–8 working sessions of focused dev** following the phases below. Phases 0–3 are the hard floor for live trading. Phase 4 is the hard floor for "several strategies, independent of each other, plug-in surface." Phase 5 closes the dashboard gap. Phase 6 is the operational layer.

---

## How to read this plan

Each phase has:
- **Why now:** the dependency / risk story
- **Scope:** the specific findings it closes (cross-referenced to the source reports)
- **Deliverable:** what lands on `main` at end of phase
- **Exit criteria:** how we know we're done
- **Est. sessions:** rough effort

Findings are tagged `F-RT-NN` (runtime), `F-BR-NN` (broker/risk), `F-UX-NN`/`F-DT-NN`/`F-SC-NN` (dashboard), `F-OPS-NN` (ops). The four source reviews are captured in this session's CHATLOG entry.

---

## Phase 0 — Repo hygiene & safety nets

**Why now.** Cheap wins. Unblocks everything else. Reduces cognitive load. None of it touches trading code.

**Scope:**
- **F-OPS-01:** Prune 87 merged-but-not-deleted remote branches. Enable GitHub's "Automatically delete head branches" repo setting.
- **F-OPS-02:** Daily systemd timer + script to `sqlite3 .backup` `data/*.db` + tar state files + push to off-VPS target (S3/B2/rsync.net, 30-day retention). Test restore. **Deliverable includes a `scripts/verify_backup.py` validator** that asserts (a) schema matches current, (b) row count within 1% of source, (c) latest fill timestamp within 24h.
- **F-OPS-10 (Account-ID regex audit):** CI tripwire `DUE[0-9]{6,9}` does not match `DU…` (no `E`). Verify against the real account format and fix the pattern.
- **F-OPS-09:** Replace flaky `gitleaks 8.24.3` with 8.27+ or `trufflehog`.
- **`deploy/HANDOFF_DEVOPS.md` line 23** still says `ssh root@2.24.222.199` — root SSH is disabled and the public IP is UFW-blocked. Fix the contradiction.
- **F-DOC-08 (revised):** Move the "incident-narrative" prose (B-11 layer 1/2/3 walkthrough, B-12/B-13 root-cause histories, six-cycle CR notes) from `CLAUDE.md` into `docs/INCIDENTS.md`. **Keep `CLAUDE.md` "Current state" ≤150 lines, NOT 60** — it remains the operator-readable session-handoff index that `OPEN_SESSION_PROTOCOL.md` Step 4 depends on. The section structure (deploy version, what's live, next 1–3 steps, open blockers) is preserved; only the historical archaeology moves. **Do not touch `OPEN_SESSION_PROTOCOL.md`'s read-list unless the replacement file is wired in the same PR.**
- **Phase 0 wrap:** prune ephemeral `claude/*` worktree branches older than 14 days.
- **Commit the four review reports** to `docs/reviews/2026-05-21/{runtime,broker,dashboard,ops}.md` so this plan's F-tag references stay dereferenceable.

**Deliverable.** Clean branch list, working backups + restore validator, accurate handoff doc, trimmed CLAUDE.md (incident archaeology extracted, operator index intact).

**Exit criteria.** `git branch -r | wc -l` ≤ 15. `scripts/verify_backup.py` passes against yesterday's backup. CI green with new scanner. `CLAUDE.md` ≤ 300 lines total (current state section ≤ 150 lines). `OPEN_SESSION_PROTOCOL.md` Step 4 still resolves to a real, current section.

**Est. sessions: 1.**

---

## Phase 1 — Pre-multi-strategy safety floor

**Why now.** Two recent incidents (B-12 PingPong silence 17:21–17:42, B-13 PingPong dead for 5 days from a 10089 chain) had the same root cause class: a strategy goes wrong and *nobody is told*. We must not add more strategies until that's fixed.

**Scope:**
- **F-RT-01 (P0):** `start_all()` silently continues after `on_start` failure, leaving an orphan RiskManager and dangling fill callbacks. Fail-fast: if any strategy's `on_start` raises, the bot refuses to start with a clear ERROR. (Future option: degraded-mode with structured status, but fail-fast for v1.)
- **F-RT-09 (P2):** `stop_all()` does not `join()` scheduler threads — race between `on_stop()` and an in-flight `on_tick()` on SIGTERM. Mirror the `BarScheduler.stop()` pattern.
- **F-BR-01a (P0) — the actual money risk:** Bracket-leg orders bypass `RiskManager.is_halted()`. A strategy whose daily-loss halt has tripped (sticky) will still emit its stop/target legs because `safe_place_order` is the only check path and the brackets skip it. **Mandatory deliverable:** new `safe_place_protective_order(request)` helper on `BaseStrategy` running a slimmer check (halt + value cap + sane-price assertion; skips R/R). Strategies migrate to it for every bracket leg. Grep tripwire (like TS-07) banning direct `self.om.place_order(` in `strategies/*`.
- **F-BR-01b (P1) — operational, can defer one phase:** Same brackets also skip the open-order and exposure caps. Folded into the helper above; lands by virtue of fixing 01a.
- **F-BR-05 (P1):** No alerting on dangerous failure modes. Wire ntfy.sh pushes (already configured) for: `ReconnectManager._halted` flips, any `RiskManager._halted_today` flips, `BarScheduler` stopped after error budget, `Error 10089/354` recurrence, `DuplicateOrderError` raised. **Plus a weekly synthetic ntfy ping** — a cron'd "TradeBot watchdog still alive" message so a broken ntfy topic or muted phone surfaces within 7 days rather than only when a real alert is missed.

**Deliverable.** A bot that refuses to start partially, alerts the operator within seconds of any "trading has stopped" condition, and enforces risk on every order regardless of caller.

**Exit criteria.** Manual test: kill a strategy's `on_start` → bot refuses to start. Manual test: trip a partial-fill halt → ntfy push received within 5 seconds. Grep tripwire fails any new `self.om.place_order(` outside `base_strategy.py`.

**Est. sessions: 1–2.**

---

## Phase 2 — Per-strategy observability (the keystone phase)

**Why now.** This is the single highest-leverage change in the plan. It plumbs heartbeat data through three layers (strategy → state → dashboard → alerts) and addresses findings from all four reviews simultaneously. Phases 5 and 6 build on it. Without it, you cannot answer "is SMACrossover-QQQ alive *right now*" without grepping logs.

**Scope:**
- **F-RT-07 (P1) — the foundation:** Each `StrategyHandle` writes a `last_tick_at` + `last_status` (running / errored / stopped / never-started) to `data/heartbeat/<name>.json` (atomic-rename). Bot also writes a `data/bot_started_at` marker on `start_all()`. **Dashboard staleness math is `now - max(last_tick_at, bot_started_at + cadence)`** — this prevents false-reds for the first cadence window after every restart (a heartbeat with `last_tick_at = 2 hours ago` after a restart at minute 1 looks identical to "strategy is hung" without this floor).
- **F-RT-04 (P1):** Introduce a `MarketClock` using `exchange-calendars` (NYSE calendar — `XNYS`) that resolves "next 16:10 ET on a trading day." `DailyAt` uses it. The dashboard uses it to color heartbeat staleness ("expected next tick at X"). **Calendar-of-record:** NYSE via `exchange-calendars`. Half-days (early close), FOMC days, and US-market holidays handled by the library. Israeli holidays / operator-unavailability are out of scope — those are handled by the operator runbook (Phase 6), not by the bot's clock. **NO missed-tick replay in this phase** — that's a behavior change for live trading (an opt-in change for a future phase), not observability. If the bot misses 16:10 ET because of a restart, the tick is skipped and the dashboard shows "next tick: tomorrow 16:10 ET" — same as today's contract.
- **F-DT-01 (P0):** `/api/strategies/{name}/heartbeat` returns the heartbeat dict. Or fold into the existing `/summary` payload.
- **F-DT-02 (P0):** `/api/strategies/{name}/state` returning the strategy's persisted state file + `RiskManager.is_halted()` + counters (`_consecutive_losses`, `_partial_fill_halt`, daily P&L vs cap).
- **F-UX-01 (P0):** Strategy overview row above the secondary tabs on the Strategies tab. One chip per strategy showing: last-tick age (color: green <2× cadence / amber <4× / red >4× / black=dead), today's P&L, halted/CB badge, in-position dot.
- **F-UX-05 (P1):** Single hero "Bot status" widget on Mission Control. Green when (health=ok AND bot=active AND gateway logged-in AND no strategies stale AND no halted CB), red/amber otherwise, one-line reason on hover.

**Deliverable.** From the dashboard home screen the operator sees, in <5 seconds, whether every strategy is alive, when it last ticked, and whether anything is halted.

**IBKR rate-limit instrumentation (cross-cutting):** Phase 2 adds per-strategy heartbeat + risk-state polling on top of the existing account-snapshot poller. Before shipping, **measure** the bot's outbound IBKR message rate against the documented ~50 msg/sec budget (sample for 1 hour during RTH). Add a log line per minute summarizing `msgs_out_last_60s`. If we're already at 30+/sec at 3 strategies, F-RT-03 callback indexing (Phase 4) moves earlier — not later.

**Exit criteria.** Simulate a strategy hang on staging → red chip appears within `2 × cadence`. Trip a circuit breaker → "halted" badge appears within one poll. Restart the bot → no chip turns red for the first cadence window (the `bot_started_at` floor works). IBKR `msgs_out_last_60s` log line present and ≤ 30 sustained.

**Est. sessions: 2–3** (revised — five F-numbered items across backend + frontend + alerting is realistically 2–3, not 2).

---

## Phase 3 — Live-trading money-safety floor

**Why now.** These three findings each independently can cause silent money loss with real capital. None can be deferred past going live.

**Scope:**
- **F-BR-02 (P0):** `cost_basis` is `None` for every live SELL fill → `realized_pnl` stored NULL → daily-loss halt math under-counts → sticky halt may never fire. Fix in two layers:
  1. At SELL time, if the strategy didn't supply `cost_basis`, fetch from IBKR `portfolio()` `averageCost`. Fail LOUD (CRITICAL log + ntfy) if neither source has a number.
  2. `PnLPoller` cross-checks `TradeLog` aggregate against IBKR account-level `RealizedPnL` once per cycle; emits CRITICAL + ntfy on divergence > $X. **Cross-check is suppressed during `ReconnectManager._halted == False AND ib.isConnected() == False`** (i.e., the brief reconnect window) — divergence during reconnect is expected and would otherwise page the operator every Sunday 23:59 UTC and every TWS daily restart.
- **F-BR-04 (P1):** `_fill_to_result` (reconnect-replay path) hardcodes `order_type="MKT"`, no `cost_basis`. Reach into `self._orders` by `orderId` first; preserve `lmtPrice`/`auxPrice`/`orderType`; compute `cost_basis` from portfolio snapshot at reconcile time. Re-fire the NULL-pnl WARNING when reconciled fills land mid-day, not only at startup.
- **F-BR-03 (P1) — clarified scope:** This is an **operational order-count cap (raw count, IBKR wire-budget concern)**, NOT a capital coupling. Decision B (independent 2% per strategy, BACKLOG 2026-05-06) is **preserved** — each strategy still sizes positions independently from its own equity attribution. What changes: a new `GlobalOrderBudget` tracks the *count* of open orders across all strategies against an account-wide IBKR ceiling (currently undefined — set to 50 conservatively, document the source). `OrderManager.place_order` raises `OrderBudgetExceededError` if the count would breach. **When the cap is hit, behavior is "last placer loses" (the request is rejected with a clear error to the strategy) — no round-robin, no FIFO eviction of someone else's orders.** Per-strategy `max_open_orders` stays as a secondary local cap.
- **F-OPS-05 (P1):** Tagged releases (`v2026.05.21` etc.) on every `main` merge. Document `git checkout v<previous> && systemctl restart tradebot` rollback in HANDOFF_DEVOPS.md.

**Deliverable.** Risk enforcement that cannot be bypassed by caller-path bugs. P&L attribution that survives reconnect storms and is independently cross-checked against IBKR.

**Exit criteria.** Inject a reconciled SELL with no `cost_basis` → ntfy push within one poll. Place 11 orders across 3 strategies (3+4+4) → 11th rejected by GlobalRiskManager. Rollback procedure rehearsed once on staging.

**Est. sessions: 2.**

---

## Phase 4 — Multi-strategy plug-in surface

**Why now.** This is the work that makes "add a new strategy easily" real. After Phases 1–3 the infrastructure is safe enough to invite new strategies in. Below 3 strategies it's optional; above 3 it pays off every time you touch the framework.

**Scope:**
- **F-RT-03 (P1):** OrderManager broadcast callbacks: O(N²) work per fill, no try/except per callback, one buggy hook can break TradeLog for *other* strategies. Index callbacks by `strategy_name` (dict-of-lists); wrap each invocation in try/except with structured error log.
- **F-RT-05 (P1):** Decorator-based registration. New strategy = one new file with `@register_strategy("Name")` on the class + a row in `strategies.yml`. Drop the manual `_STRATEGY_CLASSES` import dict. Keep MS-D-style validation. **Coexistence note:** the dashboard's `STRATEGY_METADATA` static import (2026-05-12 extraction) relies on the metadata being importable without instantiating strategies. The decorator must register into a module-level dict *at import time* via class definition (not at first instantiation) so the dashboard still gets full metadata from a side-effect-free import of `strategies/__init__.py`.
- **F-RT-06 (P1):** Shared `StateStore` helper. `store = StateStore(strategy_name, schema_version=N, migrations={...})` — atomic-rename writes, schema versioning, single audit log. State files live under `data/state/<strategy_name>.json` by convention. Every stateful strategy adopts it.
- **F-RT-08 (P2):** Move callback registration from `BaseStrategy.__init__` into `StrategyRunner.build()`. Make `BaseStrategy.__init__` side-effect-free with respect to OrderManager. Tests, backtests, and REPLs no longer mutate global state on construction.

**Deliverable.** Adding a new strategy is one file + one YAML row. The framework cannot leak one strategy's bug into another's fill routing.

**Exit criteria.** Write a throwaway "EchoStrategy" in one file; bot picks it up on restart. Inject `raise Exception` in one strategy's `on_fill` → other strategies' TradeLog rows still write.

**Est. sessions: 1–2.**

---

## Phase 5 — Dashboard UX uplift

**Why now.** Phases 0–4 give us the data and safety. Phase 5 makes the dashboard worthy of it. Mobile/off-site support is the missing leg per the original brief ("user checks bot from off-site").

**Scope:**
- **F-UX-02 (P0):** Mobile breakpoints at 480 / 720 / 1024. Collapse `.grid` to one column, KPI strip to two-up, hide low-priority table columns (Cost basis, R-multiple, Params) with row-expand. Test with iPhone-sized viewport.
- **F-UX-03 (P1):** Decorative "Live" topbar dot — either bind to `/api/health` (preferred) or replace with build version. Stops misleading the eye when real liveness goes red.
- **F-UX-04 (P1):** 7-day mini equity sparkline on Mission Control (uses existing `/api/equity-history`).
- **F-DT-03 (P1):** `/api/open-orders` from the account-snapshot poller. Small table under Recent Fills.
- **F-DT-04 (P1):** In-process last-N error ring buffer; `/api/recent-errors` returning `{ts, strategy, code, message}`. "Recent issues" card on Mission Control. Closes the "10089 was invisible until I SSH'd" gap.
- **F-DT-06 (P2):** "Weekly 2FA window in Xh" amber banner when within 24h of next Sunday 01:00 ET.
- **Mock-port polish (DEFERRED to Phase 5.5):** DASH-N1 (hero orb), DASH-N6 (equity chart grid + last-point glow), DASH-N8 (CSS-tripwire test for palette tokens). These are P2/P3 visual polish — keeping them out of Phase 5 lets that phase actually fit in 1–2 sessions.

**Deliverable.** Dashboard that meets the 5-second-glance bar on phone *and* desktop, surfaces open orders + recent issues + 2FA cadence.

**Exit criteria.** Open dashboard on iPhone → readable without horizontal scroll. Reject an order via test → appears in "Recent issues" within one poll. Visit on a Saturday → 2FA banner present.

**Est. sessions: 1–2.**

### Phase 5.5 — Visual polish (optional, time-permitting)

DASH-N1 / N6 / N8 from BACKLOG. Pure CSS/SVG. Cut without consequence if Phase 6 or strategy work is more urgent.

**Est. sessions: 0.5–1.**

---

## Phase 6 — Operational hardening (serialized between phase boundaries)

**Why now.** Required for live trading. **NOT literally concurrent with Phases 2–5** — `pip-tools` migration changes the requirements pipeline that CI runs on every PR; off-VPS log shipping changes the same systemd deploy unit as the dashboard. Treat Phase 6 work as serialized chunks slotted between other phase boundaries (e.g., after Phase 2 ships, before Phase 3 begins, run one Phase 6 chunk).

**Scope:**
- **F-OPS-03 (P1):** Adopt `pip-tools` (or `uv`). Commit `requirements.lock` (pinned, hashed) generated from `requirements.in` (top-level floors). Add `.github/dependabot.yml` for weekly Python + Actions updates.
- **F-OPS-04 (P1):** Cap `journald` size (`SystemMaxUse=2G`). Logrotate for `logs/`. Off-VPS log shipping to Grafana Cloud Loki free tier or nightly `rsync` to the same backup target as Phase 0.
- **F-OPS-06 (P1):** Operator runbook as a separate file (not buried in CLAUDE.md): how to SSH from a fresh machine, how to VNC, where IBKR Mobile codes come from, who has the password. Designate a backup operator. Send the IBKR support inquiry about IB Key push (CHATLOG mentions a draft — status unclear).
- **F-OPS-07 (P2):** Install `pandas-stubs` + `types-requests`. Drop `--ignore-missing-imports` where possible. Add `pytest --cov=. --cov-report=term-missing --cov-fail-under=80` to CI. Run mypy on `tests/` with looser rules.
- **F-SC-01:** Slide session expiry on activity; cap absolute at 24h.
- **F-SC-02:** Wrap `/api/bot/restart` and `/api/bot/stop` in `audit_log("bot.restart", fp, ip)`.
- **F-SC-03..06:** Bearer-token rate limiting, tighter Origin check, per-session debounce on restart, in-product token-rotate command. Defensive layering; ship together.
- **GC-4:** Dashboard TLS via Caddy/nginx + `tailscale cert`. **Re-prioritized by this plan:** previously the "next session" item in CHATLOG; now lands inside Phase 6 (after observability + money-safety are in). If you'd rather GC-4 stay the immediate next focus instead of Phase 0, that's a one-decision swap — flag it explicitly.
- **F-FEED-01 (yfinance outage measurement):** BACKLOG MS-C2 schedules `scripts/yfinance_outage_report.py` to run from the VPS starting 2026-06-12. Phase 6 takes ownership: ensure the script is on the VPS, the cron is armed, and the first month's report is reviewed. The report's outcome decides whether IBKR `reqHistoricalData` fallback (MS-C2 dev work) is built.
- **F-DEPLOY-01 (deploy-fails-mid-way story):** Document and rehearse: (a) `tradebot-dashboard` ⇄ `tradebot` version skew handling (the dashboard reads state files the bot writes; a schema change in one without the other is the failure mode). (b) Graceful drain — `systemctl stop tradebot` should let any in-flight `place_order` complete before SIGTERM. Today it doesn't; add a `PreStop` hook or a stop-event that `OrderManager` waits on. (c) StateStore schema migrations (Phase 4 F-RT-06) must include a `pre-migrate` backup + `post-migrate` validation step; document in `deploy/MIGRATIONS.md`.

**Deliverable.** Reproducible deploys, off-VPS log retention, documented on-call procedure, layered dashboard security, TLS.

**Exit criteria.** Wipe VPS → rebuild from `requirements.lock` → bot starts. Logs queryable from outside the VPS. Backup operator successfully completes Sunday 2FA rehearsal.

**Est. sessions: 1–2.**

---

## Phase 7 — Long-term architecture (deferred, post-live)

**Why later.** These are real wins but each is multi-week and disruptive. Defer until the bot has been live and stable for ≥4 weeks.

**Scope:**
- **F-RT-02 (P0 long-term):** Move toward per-strategy IBKR connections (each with a unique `clientId`, up to ~32 supported). Strongest isolation; eliminates the "one slow handler wedges everyone" failure mode. Alternative interim: `asyncio.Queue`-based dispatch in OrderManager so callbacks return immediately.
- **CR-07:** Migrate from archived `ib_insync` to maintained `ib_async` fork. Resolves the deprecated `asyncio.get_event_loop()` in `IBKRClient._main_loop` capture and several other latent issues. Multi-week effort; do alongside a paper-only environment.
- **Staging environment:** Second cheap VPS (or second IBC instance, different clientId, different paper account) tracking `develop` as a smoke environment. Catches deploy issues before main.
- **F-OPS-04 (full):** Loki + Grafana dashboards for long-term forensics, alerting rules ("strategy hasn't ticked in N min" → page).
- **F-BR-06:** `IBKRFeed.is_live` corrected to track `reqMarketDataType` ack rather than account mode.

**Est. sessions: multi-week, sequenced separately.**

---

## Plan-for-the-plan (checkpoint discipline)

This plan will go stale unless we re-review it. Mandatory checkpoints:

- **After Phase 2 ships:** re-run a smaller version of the four-slice review (focus: did the heartbeat + observability change the failure-mode catalog?). Adjust Phases 3–6 based on actual incident data from the first two weeks of Phase 2 in production.
- **After Phase 3 ships:** explicit go/no-go on live-account flip. If any P0 from Phase 1 or 3 has slipped back open, NO-GO.
- **After Phase 4 ships:** decide whether F-RT-02 (per-strategy IBKR connections, Phase 7) is needed by measurement — if `msgs_out_last_60s` stays under 30/sec at N=4 strategies, defer indefinitely.
- **Findings tag stability:** the F-RT/F-BR/F-UX/F-DT/F-SC/F-OPS tags reference the original four review reports. Those reports are committed to `docs/reviews/2026-05-21/` (Phase 0 deliverable — add to scope) so the tags remain dereferenceable as the plan evolves.

---

## Cross-cutting risks (call out, then track)

These don't fit neatly in a phase. Decide explicitly.

1. **Bus-factor 1 on weekly 2FA.** Owner is the only person who can re-auth on Sundays. Mitigation in Phase 6 (operator runbook + backup operator), but the real fix is structural: get IB Key push-notification 2FA approved by IBKR for the Israeli account, or share VNC + IBKR Mobile credentials with a trusted second operator. Track as an open business decision, not just engineering.
2. **Single VPS, no failover.** A Hostinger account incident or VPS hardware failure = bot down for as long as it takes to rebuild. Mitigation: Phase 0 backups + Phase 7 staging environment. Decide whether to invest in a true hot-standby — probably overkill for paper, table-stakes for live with non-trivial capital.
3. **The `ib_insync` archive risk.** The library has had three thread-safety adventures in two days. It's archived. CR-07 (Phase 7) addresses this but ships late. Carry the risk; track new strategy issues against it.
4. **Goal-vs-capacity question.** This plan is 8–10 sessions. The user's stated cadence is single sessions with reviews + closing rituals — call it 2–3 sessions/week. So the production-ready end state is 4–5 weeks out at sustainable pace, faster with consecutive day work. Set expectation accordingly.
5. **IBKR message-rate budget.** Documented at ~50 msg/sec. Phases 2 (per-strategy polling) + 3 (RealizedPnL cross-check) + 5 (open-orders + recent-errors) each add outbound calls. Phase 2 includes an `msgs_out_last_60s` instrumentation log so we measure before we ship Phases 3/5 on top. If we breach 30/sec sustained, F-RT-03 callback indexing moves up.
6. **Monitoring-of-monitoring.** ntfy.sh is our alerting channel. A broken topic or muted phone makes every alert silent. Phase 1's F-BR-05 includes a weekly synthetic ntfy ping so this fails loudly within 7 days.

---

## What's deliberately NOT in this plan

- **Strategy quality.** Per user instruction — not reviewed.
- **F-RT-02 isolation rearchitecture** as a near-term phase. The current shared-loop model is adequate for ≤5 strategies. Defer until measured pain.
- **A second exchange / multi-broker support.** Not scoped.
- **Backtest framework improvements.** Already adequate per the brief.
- **Strategy parameter optimization / walk-forward tooling.** Out of scope.

---

## Phase-by-phase dependency graph

```
Phase 0 (hygiene + backups) ────┐
                                 ▼
Phase 1 (safety floor) ─────┐    │
                            ▼    │
Phase 2 (observability) ────┼────┤
                            ▼    │
Phase 3 (money-safety) ─────┼────┼─── pre-LIVE gate
                            ▼    │
Phase 4 (plug-in surface) ──┘    │
                                 ▼
Phase 5 (dashboard UX)  ◄────────┤  (depends on Phase 2 data)
                                 ▼
Phase 6 (ops hardening) ◄────────┤  (parallel to 2–5)
                                 ▼
Phase 7 (long-term arch)  ──────  POST-LIVE
```

**Pre-live gate:** Phases 0 + 1 + 2 + 3 must be done. Phase 4 strongly recommended. Phases 5 + 6 can land in parallel or shortly after.

---

## Next step (now)

User picks the next session's focus from this menu:
1. **Phase 0** (1 session, low risk, high cleanup value) — recommended first.
2. **Phase 1** (1–2 sessions, closes the silent-failure category) — recommended second.
3. **GC-4 first** (TLS for the dashboard) — was the previous "next session" item; this plan moved it into Phase 6, but it can stay as the immediate next focus if the user prefers shipping a long-tracked item before starting the new plan.
4. **Skip ahead to Phase 2** if observability is the most painful current friction.

After this planning session is closed, each phase becomes its own session with normal pre-impl CR → code → post-impl CR → CHATLOG entry cadence per `WORKFLOW.md`.
