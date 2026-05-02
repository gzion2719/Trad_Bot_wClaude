# TradeBot — Session Log

Newest entry first. Max 5 content bullets + `**Process improvement:**` + `**Next session:**` per entry.
Read the last 3 entries at the start of every session (Step 4 of the opening ritual).

---

## 2026-05-02 — Reconnect asyncio threading fix (B-08)

- Diagnosed root cause of recurring "on_tick stale" alerts: `ib_insync` calls `asyncio.get_event_loop()` internally; Python 3.12 raises `RuntimeError` in non-main threads, so every `ReconnectManager` reconnect attempt failed before reaching IBKR.
- Fixed `broker/ibkr_client.py`: save main event loop on first `connect()` (main thread); reconnects from daemon thread use `asyncio.run_coroutine_threadsafe(ib.connectAsync(), main_loop)`. Also replaced `ib.sleep()` with `time.sleep()` in post-connect poll.
- Deployed via `feature/fix-reconnect-asyncio-thread` → PR #21 develop → PR #22 main → VPS `git pull + restart`. Bot confirmed healthy (PID 52545, connected DUE090987).
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
