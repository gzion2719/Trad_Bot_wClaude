# Broker, risk & reliability review — 2026-05-21

**Status:** stub — originating subagent output not persisted.

This review was one of four parallel deep-review passes run as part of the IMPROVEMENT_PLAN drafting session on 2026-05-21. The subagent's full report existed only in that session's tool-result memory and was not persisted to disk before the session closed.

**Findings synthesized in:** [`docs/IMPROVEMENT_PLAN.md`](../../IMPROVEMENT_PLAN.md) under `F-BR-NN` tags (e.g., F-BR-01a bracket-leg RiskManager bypass, F-BR-01b open-order/exposure caps, F-BR-02 `cost_basis=None` on live SELLs, F-BR-03 `GlobalOrderBudget` operational cap, F-BR-04 `_fill_to_result` reconnect-replay, F-BR-05 ntfy alerting on halt/CB/error storms, F-BR-06 `IBKRFeed.is_live` correctness).

To trace any individual F-BR finding, search `docs/IMPROVEMENT_PLAN.md` for the tag — each appears in the phase that owns it (Phase 1 for the safety floor, Phase 3 for money-safety, Phase 7 for long-term).

**Why this file exists despite the missing source:** the plan's F-tag references would otherwise be dangling. This stub keeps the references dereferenceable; readers click through and land on a pointer to the synthesized findings.
