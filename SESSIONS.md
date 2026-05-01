# TradeBot — Session Log

Most-recent entry first.

---

## 2026-04-30 — IBKR log noise fix + closing ritual

- Diagnosed ntfy health alerts: bot healthy, on_tick() hadn't fired yet (fires 20:10 UTC daily — health.txt gets written then)
- Fixed `broker/order_manager.py`: 6 IBKR info codes (1100/1102/2103/2105/2107/2157) demoted from ERROR to INFO/WARNING via 3-tier classification (`_DEBUG_CODES` / `_INFO_CODES` / `_WARNING_CODES`)
- PR #9 (feature→develop) + PR #10 (develop→main) merged; resolved CLAUDE.md/TODO.md conflict caused by PR #8 having bypassed develop
- Created `closing-ritual` skill; Obsidian `Claude Handoff Prompt.md` updated with closing workflow and new Option C (VPS deploy)
- **Process improvement:** After skill creation, re-read manifest.json immediately to confirm entry persisted — rule 6 added to CLAUDE.md `Claude-specific rules` section
- **Next session:** Deploy to VPS after on_tick() at ~20:10 UTC; first Sunday 2FA test 2026-05-03 ~09:00 IL time
