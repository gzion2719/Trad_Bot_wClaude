# Dashboard UX, data & security review — 2026-05-21

**Status:** stub — originating subagent output not persisted.

This review was one of four parallel deep-review passes run as part of the IMPROVEMENT_PLAN drafting session on 2026-05-21. The subagent's full report existed only in that session's tool-result memory and was not persisted to disk before the session closed.

**Findings synthesized in:** [`docs/IMPROVEMENT_PLAN.md`](../../IMPROVEMENT_PLAN.md) under three tag families:

- **`F-UX-NN`** — user-experience findings (e.g., F-UX-01 strategy overview row, F-UX-02 mobile breakpoints, F-UX-03 decorative "Live" topbar dot, F-UX-04 7-day mini equity sparkline, F-UX-05 Bot status hero widget).
- **`F-DT-NN`** — data/API findings (e.g., F-DT-01 `/heartbeat` endpoint, F-DT-02 `/state` endpoint, F-DT-03 `/api/open-orders`, F-DT-04 `/api/recent-errors` ring buffer, F-DT-06 2FA-window banner).
- **`F-SC-NN`** — security findings (e.g., F-SC-01 sliding session expiry, F-SC-02 audit log on restart/stop, F-SC-03..06 rate limiting / Origin check / debounce / token-rotate).

To trace any individual finding, search `docs/IMPROVEMENT_PLAN.md` for the tag — each appears in Phase 2 (observability data), Phase 5 (UX uplift), or Phase 6 (security hardening).

**Why this file exists despite the missing source:** the plan's F-tag references would otherwise be dangling. This stub keeps the references dereferenceable; readers click through and land on a pointer to the synthesized findings.
