# Runtime & multi-strategy architecture review — 2026-05-21

**Status:** stub — originating subagent output not persisted.

This review was one of four parallel deep-review passes run as part of the IMPROVEMENT_PLAN drafting session on 2026-05-21. The subagent's full report existed only in that session's tool-result memory and was not persisted to disk before the session closed.

**Findings synthesized in:** [`docs/IMPROVEMENT_PLAN.md`](../../IMPROVEMENT_PLAN.md) under `F-RT-NN` tags (e.g., F-RT-01 `start_all()` silent partial-start, F-RT-02 shared IBKR connection, F-RT-03 broadcast-callback O(N²), F-RT-04 MarketClock, F-RT-05 decorator registration, F-RT-06 StateStore helper, F-RT-07 heartbeat files, F-RT-08 callback registration in build(), F-RT-09 stop_all join()).

To trace any individual F-RT finding, search `docs/IMPROVEMENT_PLAN.md` for the tag — each appears in the phase that owns it (Phase 1 for the safety-floor findings, Phase 2 for observability, Phase 4 for the plug-in surface).

**Why this file exists despite the missing source:** the plan's F-tag references would otherwise be dangling. This stub keeps the references dereferenceable; readers click through and land on a pointer to the synthesized findings.
