# TradeBot — Session Log

Newest entry first. Max 5 content bullets + `**Process improvement:**` + `**Next session:**` per entry.
Read the last 3 entries at the start of every session (Step 4 of the opening ritual).

---

## 2026-05-02 — Dashboard Phase 3 control plane + bot/gateway status indicators

- Verified Phase 2 + weekend-fix on VPS: `/api/health` returns `stale_after_seconds=288000` on Saturday ✅; `/api/system` returns all fields ✅. The pending deploy note in CLAUDE.md was stale — code was already live.
- Built Phase 3 control plane: `POST /api/bot/restart` + `POST /api/bot/stop` gated by `Authorization: Bearer DASHBOARD_TOKEN`; `deploy/sudoers/tradebot-dashboard` scopes NOPASSWD to exactly those two commands; UI Controls card with token in localStorage; DB-09..DB-13 pass; ruff/black/mypy ✅. Deployed and browser-tested.
- Added bot status indicator (BOT / GATEWAY side-by-side pulse dots; red "Stopped" when bot is down); gateway label now shows "Logged in" / "Awaiting login" / "Down" by combining `gateway_service_status` + `gateway_port_open` — no backend changes needed.
- Added Dashboard Phase 4+ to BACKLOG: account balance+graph, per-strategy fills, per-strategy analytics (W/L, P&L, Sharpe, drawdown), UI redesign (DB-P4-1..4).
- **Process improvement:** SESSION_PROTOCOL.md two-PR rule strengthened — every push must include BOTH feature→develop AND develop→main links plus full VPS deploy command in one message. Violated 3× this session before codification.
- **Next session:** merge pending PRs → VPS deploy (`git pull + restart tradebot-dashboard`); then Dashboard Phase 4 (account balance) or Sunday 2FA dry-run (2026-05-03 ~09:00 IL).

## 2026-05-02 — Opening ritual non-negotiable trigger rule

- Slipped on session start: treated "read claude.md" as a literal file-read command and skipped Steps 1–7. User caught it.
- Codified fix in SESSION_PROTOCOL.md: explicit trigger list (read claude.md / cluadmd / let's start / greetings / tasks / emojis / commands that look like literal file reads) + mechanical pre-response self-check before first reply.
- Reinforced in CLAUDE.md header (always-loaded context) so the rule is visible even if SESSION_PROTOCOL.md isn't read first.
- Only carve-out: user explicitly says "skip the ritual".
- **Process improvement:** SESSION_PROTOCOL.md Opening Ritual gains non-negotiable trigger list + self-check; CLAUDE.md header gains reinforcement paragraph.
- **Next session:** deploy Phase 2 + weekend fix to VPS (`git pull + restart tradebot-dashboard`); then Sunday 2FA dry-run (2026-05-03 ~09:00 IL) or Dashboard Phase 3.

## 2026-05-02 — Reconstructed previous session close (docs cleanup)

- Opening ritual flagged drift: `git log` showed PRs #30/#31/#32/#33 + commits `d3e286d` and `b6515f4` on main, but CHATLOG.md and CLAUDE.md still described Phase 1 as the most recent work — previous chat had ended on an API error before its closing ritual could run.
- User provided full transcript of the missed session; reconstructed a complete CHATLOG entry for "Dashboard Phase 2 + weekend-aware stale threshold" from transcript + git evidence.
- Updated CLAUDE.md Current state header + START HERE tasks (deploy command now points at already-merged main, not unmerged feature branch); refreshed TODO.md 5.7 and ROADMAP.md 5.7 to mark Phase 2 + weekend fix as shipped.
- Added "Debugging discipline" section to WORKFLOW.md (read producer cadence before hypothesizing failure modes) and a SESSION_PROTOCOL.md Step 5 sub-rule (reconstruct missing CHATLOG entries before piling new work on stale state).
- **Process improvement:** SESSION_PROTOCOL.md Step 5 gains "git/CHATLOG drift = reconstruct first" rule; WORKFLOW.md gains "Debugging discipline" section. Both edits in this session.
- **Next session:** deploy Phase 2 + weekend fix to VPS (`git pull + restart tradebot-dashboard`); then choose between Dashboard Phase 3 (control plane) or Sunday 2FA dry-run.

## 2026-05-02 — Dashboard Phase 2 + weekend-aware stale threshold (5.7) [reconstructed close]

- Built dashboard Phase 2 System card: `/api/system` returns bot PID/uptime + IB Gateway service status (`systemctl show ... MainPID,ActiveEnterTimestamp`) + port 4001 listen check. UI gained green pulsing gateway indicator + human-readable uptime. Shipped `d3e286d` via PRs #30 → develop, #31 → main.
- Diagnosed dashboard "stale liveness" alarm (last tick 42.5h ago on Saturday): SMA strategy fires `on_tick()` once daily at 16:10 ET via custom `_daily_scheduler` in `main.py` (not BarScheduler), so 72h weekend gap > 26h hardcoded threshold = false positive. Bot was healthy throughout.
- Fixed `dashboard/app.py` with `_stale_threshold_seconds()` returning 80h on Sat/Sun/Monday-pre-tick, 26h trading days; updated DB-03/DB-04 tests; ruff/black/mypy ✅. Shipped `b6515f4` via PRs #32 → develop, #33 → main.
- **Pending VPS deploy:** `ssh chappy-vps && sudo -i && cd /opt/tradebot && git pull origin main && systemctl restart tradebot-dashboard` to pick up Phase 2 + weekend fix.
- Originating chat ended on API error before closing ritual — entry reconstructed from full transcript + git log in the next session.
- **Process improvement:** WORKFLOW.md gains "Debugging discipline" section — before hypothesizing failure modes for a "stopped" symptom, read the producer to confirm expected cadence. Cost us several rounds chasing a phantom BarScheduler-stopped bug before the user intuited "could it just be the weekend?".
- **Next session:** verify VPS dashboard restart picks up Phase 2 + weekend fix; choose between Dashboard Phase 3 (control plane + token auth) or Sunday 2FA dry-run (next test = 2026-05-03 ~09:00 IL).

## 2026-05-02 — Mission control dashboard Phase 1 (5.7)

- Built read-only dashboard: FastAPI app, 4 endpoints (`/api/health`, `/api/today`, `/api/recent-fills`, `/api/info`), auto-polling dark UI, `tradebot-dashboard.service`. Deployed to VPS, accessible from PC and iPhone via Tailscale at `http://100.113.140.69:8080`.
- Fixed critical git rule mid-session: `pull/new/<branch>` URL defaults base to `main` — mandated `compare/<base>...<compare>` format in CLAUDE.md rule 2 + memory entry.
- Dashboard rebound `127.0.0.1` → `0.0.0.0` for Tailscale mobile access; UFW has no allow for 8080 so public internet is blocked. Service file updated in repo to match.
- Phase 3 design tabled: kill/restart needs token auth + narrow sudoers rule — read-only ships first, control plane is next session.
- **Process improvement:** CLAUDE.md rule 2 bans `pull/new/` URLs — mandates `compare/` format. Memory entry `feedback_pr_url_format.md` added for cross-session persistence.
- **Next session:** Dashboard Phase 2 (IB Gateway status card, bot uptime/PID) then Phase 3 control plane with token auth.

## 2026-05-02 — Reconnect asyncio threading fix (B-08)

- Diagnosed root cause of recurring "on_tick stale" alerts: `ib_insync` calls `asyncio.get_event_loop()` internally; Python 3.12 raises `RuntimeError` in non-main threads, so every `ReconnectManager` reconnect attempt failed before reaching IBKR.
- Fixed `broker/ibkr_client.py`: save main event loop on first `connect()` (main thread); reconnects from daemon thread use `asyncio.run_coroutine_threadsafe(ib.connectAsync(), main_loop)`. Also replaced `ib.sleep()` with `time.sleep()` in post-connect poll.
- Deployed via `feature/fix-reconnect-asyncio-thread` → PR #21 develop → PR #22 main → VPS `git pull + restart`. Bot confirmed healthy (PID 52545, connected paper account).
- Real test is the next IBKR server blip — watch for `Reconnected to TWS successfully` instead of the event loop error chain.
- **Process improvement:** asyncio thread limitation added to CLAUDE.md known limitations — future sessions won't need to re-diagnose this pattern.
- **Next session:** confirm first live reconnect worked; then begin mission control dashboard (ROADMAP 5.7).

## 2026-05-02 — Bot recovery + reconnect auto-restart fix

- Diagnosed ntfy "on_tick stale" alerts: IB Gateway had a 6-min IBKR server blip at 05:23 UTC May 2; bot exhausted its 10 reconnect attempts and went silent for ~31h while systemd still showed "active (running)".
- Fixed `ReconnectManager`: added `os._exit(1)` after retries exhausted so systemd `Restart=on-failure` triggers a clean restart. `sys.exit` was insufficient — it only kills the daemon thread, not the process.
- Investigated IBKR Trusted IP (5.9): account-level feature is one-IP-per-user; adding VPS IP blocks home PC access. Closed as won't do. Gateway API Trusted IPs already correct (`127.0.0.1` in IBC config).
- Deployed fix: PR → develop → `sudo git pull origin develop && sudo systemctl restart tradebot` on VPS. Bot confirmed healthy, on_tick scheduled for 16:10 ET today.
- **Process improvement:** WORKFLOW.md gains "Web research rule" — if WebFetch returns 403, go straight to WebSearch, don't retry the same domain.
- **Next session:** design + implement mission control dashboard — bot stats, kill/restart buttons, IB Gateway login UI to replace weekly VNC 2FA.

---

## 2026-05-01 — Stale-main caught after first ritual test failed

- Tested ritual in fresh chat after develop→main merge — it didn't fire. Diagnosed: local `main` was 8 commits behind `origin/main`, and the project folder was parked on a stale feature branch (`feature/document-weekly-2fa-cadence`), so new chats loaded a pre-scaffold CLAUDE.md.
- User ran `git checkout main && git pull origin main` — local main now matches origin (75fbc9c).
- Identified three compounding causes: (a) Claude Code worktrees branch off LOCAL refs that go stale, (b) project folder left on feature branches, (c) CHATLOG "PR merged" written before GitHub actually merged.
- Plan for next session: SessionStart hook in `.claude/settings.json` to auto-fetch + ff-pull main; extend SESSION_PROTOCOL Step 5 with a "behind origin" warning; add worktree-hygiene rule to WORKFLOW.md.
- **Process improvement:** none codified this session — the codification IS the next session.
- **Next session:** wire SessionStart hook + Step 5 extension + WORKFLOW rule via a feature branch → PR develop → PR main.

---

## 2026-05-01 — Protocol scaffold bootstrap

- Set up SESSION_PROTOCOL.md, WORKFLOW.md, CHATLOG.md, docs/ROADMAP.md, docs/BACKLOG.md, .github/workflows/ci.yml, Makefile — full YuTom-style protocol now live on the project.
- Updated CLAUDE.md: references SESSION_PROTOCOL.md + WORKFLOW.md, records language pair (Hebrew or English in, English out), file map updated.
- CI pipeline wired: ruff → black --check → mypy → pytest on push to main and on every PR.
- docs/BACKLOG.md consolidates all open TODO.md items by category; TODO.md remains the sprint-by-sprint tracker.
- **Process improvement:** none this session (bootstrap — no prior protocol to improve against).
- **Next session:** run `make pre-push` to verify local gate passes clean; then continue Sprint 6 paper-trading monitoring.
