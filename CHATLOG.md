# TradeBot — Session Log

Newest entry first. Max 5 content bullets + `**Process improvement:**` + `**Next session:**` per entry.
Read the last 3 entries at the start of every session (Step 4 of the opening ritual).

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
