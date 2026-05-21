# Repo, CI, ops & security review — 2026-05-21

**Status:** stub — originating subagent output not persisted.

This review was one of four parallel deep-review passes run as part of the IMPROVEMENT_PLAN drafting session on 2026-05-21. The subagent's full report existed only in that session's tool-result memory and was not persisted to disk before the session closed.

**Findings synthesized in:** [`docs/IMPROVEMENT_PLAN.md`](../../IMPROVEMENT_PLAN.md) under `F-OPS-NN` tags (e.g., F-OPS-01 87 merged-but-not-deleted branches, F-OPS-02 daily off-VPS backup + restore validator, F-OPS-03 pip-tools/lockfile, F-OPS-04 journald + log shipping, F-OPS-05 tagged releases + rollback, F-OPS-06 operator runbook + backup operator, F-OPS-07 mypy/test coverage, F-OPS-09 flaky gitleaks, F-OPS-10 DUE-regex tripwire).

Also covers `F-FEED-01` (yfinance outage-report measurement gate), `F-DEPLOY-01` (deploy-fails-mid-way story: version skew + graceful drain + StateStore migrations), and `F-DOC-08` (CLAUDE.md slim + INCIDENTS.md extraction).

To trace any individual finding, search `docs/IMPROVEMENT_PLAN.md` for the tag — most land in Phase 0 (hygiene) or Phase 6 (ops hardening).

**Why this file exists despite the missing source:** the plan's F-tag references would otherwise be dangling. This stub keeps the references dereferenceable; readers click through and land on a pointer to the synthesized findings.
