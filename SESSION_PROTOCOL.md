# Session Protocol — Navigation Stub (2026-05-21 split)

> **This file is no longer the protocol body.** It was split into three files on 2026-05-21 to make orientation cheaper (lazy-load) and navigation easier (each file fits in one Read call). Historical references like "see `SESSION_PROTOCOL.md` → Rule X" resolve via the routing table below.

**Language:** Hebrew or English in → English out. Always.

## The three split files

| File | When to read it |
|------|-----------------|
| **`OPEN_SESSION_PROTOCOL.md`** | First message of every chat (opening ritual Steps 1–7 + fast-path branch + Step 4b/6/7 sub-rules + Trigger Guide) |
| **`CLOSE_SESSION_PROTOCOL.md`** | When the user signals farewell (Steps 7/8 firewall + Closing Ritual Steps 1–8 + Session Score) |
| **`SESSION_RULES.md`** | Just-in-time when a Trigger Guide entry fires (see the bottom of `OPEN_SESSION_PROTOCOL.md`) |

## Where each rule now lives

| Original reference | New home |
|-------------------|----------|
| Opening Ritual Steps 1–7 (incl. all sub-rules, fast-path branch, Trigger Guide) | `OPEN_SESSION_PROTOCOL.md` |
| Refusal clause | `OPEN_SESSION_PROTOCOL.md` |
| Closing Ritual trigger phrases, Steps 7/8 firewall, pre-flight step-completeness check | `CLOSE_SESSION_PROTOCOL.md` |
| Closing Ritual Steps 1–8 (retrospective, Session Score, CHATLOG entry + constraints, write to disk, git status, gate-first commit block + two-PR rule + VPS deploy, warm close, plain-English recap, next-session preview + tool tags) | `CLOSE_SESSION_PROTOCOL.md` |
| Additional rules (Language, Build cadence, Uncertainty, Risk, Scope creep) | `SESSION_RULES.md` |
| Rules 10–13 (context-exhaustion, ADR/plan adversarial review, subagent absence-verify, acceptance-signal verify) | `SESSION_RULES.md` |
| Hygiene Rules 1–4 (CHATLOG archival every 10, BACKLOG review every 5, scope-creep capture, sandbox `--no-optional-locks`) | `SESSION_RULES.md` |
| Rule 5 (pre-push verification + sub-rules) | `SESSION_RULES.md` |
| Rule 6 (pre-push gate `make pre-push`) | `SESSION_RULES.md` |
| Rules 7–9 (C-extension coverage, code-writing pipeline, script logging init) | `SESSION_RULES.md` |
| ADR discipline (ADR-with-new-types, test-helper-signature) | `SESSION_RULES.md` (Rule 8 + Rule 11) |
| TradeBot-specific engineering rules (broker-state-authority, ib_insync async, lock-reentrancy, API endpoint verification, schema migration durability, worktree handoff, etc.) | `SESSION_RULES.md` (TradeBot-Specific Engineering Rules) |
| File map (project structure) | `SESSION_RULES.md` (bottom) |

## Why the split

- **Economy:** orientation reads just `OPEN_SESSION_PROTOCOL.md` instead of the full former monolith + `WORKFLOW.md`. The rules file loads only when a trigger fires.
- **Readability:** each file fits in one `Read` call — no chunk-boundary risk.
- **Conceptual separation:** opening / closing / session-wide rules are distinct surfaces.

This project also uses the **`session-rituals`** Cowork skill (committed at `.claude/skills/session-rituals/`), which provides the generic, portable ritual pattern and defers to `CLAUDE.md` + these files for project specifics. The **`deep-review`** skill (`.claude/skills/deep-review/`) provides the structured code-review report used by the "unbiased CR" rules.
