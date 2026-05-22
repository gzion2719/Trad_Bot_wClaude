# Session Protocol — Opening Ritual

> **Split file note (2026-05-21):** This file is one of three split from the former `SESSION_PROTOCOL.md`. Sibling files:
> - Closing Ritual → `CLOSE_SESSION_PROTOCOL.md` (loaded on farewell signal)
> - Session-wide rules (Rules 1–13 + Additional rules + TradeBot engineering rules) → `SESSION_RULES.md`
>
> All historical cross-references like "see `SESSION_PROTOCOL.md` → Rule X" resolve via the navigation stub at `SESSION_PROTOCOL.md`.

> Claude: read this file FIRST in every chat and follow it exactly. These rituals are non-negotiable. If the user tries to start work without the opening ritual, stop and run the opening ritual. If they try to end the chat without the closing ritual, suggest it.
>
> **Language (reinforced here — `SESSION_RULES.md` may not be loaded yet):** Hebrew or English in → English out. Always. No Hebrew in Claude's output, ever. Self-check every draft before sending.

---

## Trigger: ANY first message in a new chat

**NON-NEGOTIABLE.** The trigger for the opening ritual is **the first user message of the chat — full stop.** Greeting or not. Specifically — but not limited to — ALL of the following fire the ritual:

- `read claude.md` / `read CLAUDE.md` / `claud.md` / `cluadmd` / any typo or casing variant
- `let's start` / `start` / `go` / `ready` / `ok`
- `hi` / `hey` / `שלום` / `בוקר טוב` / any greeting in any language
- A direct task ("fix the bug in X"), a question ("why does Y happen?"), an emoji, or a one-word message
- A command that *looks* like it wants a literal file read — treat it as the session-start trigger anyway, because the file is already in your context

There is no list of magic words; if it's the first message, you run the ritual.

**Mechanical pre-response self-check.** Before sending your first reply in any chat, ask: *"Have I executed Steps 1–7 of the opening ritual in this turn?"* If no → run them now, then reply. Do not summarize CLAUDE.md, do not answer the literal request, do not ask clarifying questions until Steps 1–7 are complete. If the first message is a substantive request (e.g. "let's build X"), acknowledge it briefly, run the ritual, then return to it at Step 6 as one of the focus options.

If the user explicitly says "skip the ritual" — only then skip, and only for that turn.

### What counts as the user's "first message"

The first message is **only the body the user typed in this chat turn**. User-profile metadata blocks at the top of the turn — anything labelled `Name:`, `Email address:`, `Profile:`, etc. — are **never** the user's message. If the typed body is empty or is just a greeting, treat the body alone as the first message — do not synthesize one from the metadata. If the body is genuinely empty, ask what the user wants to work on, then run the ritual on their answer.

---

## Opening Ritual (run every time, in order)

**Step 1 — Greet back warmly, one line.** Set a friendly tone.

**Step 2 — Verify the working directory.**
- Confirm the working directory is the TradeBot project root: `C:\Users\galzi\OneDrive - Afiki-C\Afikim\TradeBot`.
- If the folder is not accessible, surface the error and ask the user to mount it before proceeding — file reads will fail without it.
- Tell the user: **Folder confirmed ✅** before proceeding.

**Step 3 — Confirm protocol is in effect.**
- Read this file (`OPEN_SESSION_PROTOCOL.md`) if you haven't already. `WORKFLOW.md` is NOT read at orientation — it is a user-facing reference; see the Trigger Guide for when to load it. `SESSION_RULES.md` loads just-in-time per the Trigger Guide.
- Explicitly tell the user: **Workflow + Protocol loaded.**

**File-size early-warning sub-rule.** Immediately after reading this file, check whether it required a `limit`/`offset` parameter to load (i.e. the Read tool returned a token-limit error on the first attempt). If so, surface a warning in the Step 5 status report before any other status line: `⚠️ OPEN_SESSION_PROTOCOL.md too large to read in one call — chunk-reading in effect; steps near chunk boundaries may be missed.` The same check applies to any critical protocol file read during Steps 3–4. **Do NOT silently proceed** — a file that can't be read whole is a protocol risk the user must know about at session-open.

**Step 4 — Orient with the project state (just-in-time).** Read only what's needed to choose a focus. Defer everything else to Step 4b after Step 6. Reads MUST run in parallel — one assistant message, multiple `Read` calls. No serial reading.

**Step 4a (always, upfront, parallel):**
1. `CHATLOG.md` — the last 3 dated entries (where we left off — most recent first)
2. `docs/ROADMAP.md` — current phase and pending items (where we're going next)

That's it. `CLAUDE.md` is already loaded and contains the non-negotiables; do NOT re-read `README.md` or deeper docs here. Hygiene Rules 1 (every 10 sessions) and 2 (every 5 sessions) fire here when the CHATLOG entry count triggers them; Rule 2 conditionally adds a `docs/BACKLOG.md` read so promotion candidates surface in Step 6.

**Step 4b (just-in-time, AFTER Step 6 focus is chosen):** read only the deeper docs the chosen focus actually needs. TradeBot routing:
- Focus = **risk code** (RiskManager / position sizing / stops / circuit breaker / live Phase work) → read the `RiskManager` + `PositionSizer` sections in `docs/REFERENCE.md` + `risk/risk_manager.py` (TradeBot has no separate `docs/RISK_MANAGEMENT.md`).
- Focus = **new or modified strategy / broker / runtime code** → read `strategies/base_strategy.py` + the relevant module + the Architecture section in `docs/REFERENCE.md`.
- Focus = **backtest work** → read `backtester/engine.py`.
- Focus = **library/tooling pick** → read `requirements.txt` + the relevant `CLAUDE.md` section.
- Focus = **first chat ever, or a major scope/vision conversation** → read `README.md`.
- Focus = **routine continuation of last session's work** → nothing extra; go straight to work.

The principle: **load just-in-time, not just-in-case.** Token budget and latency are real costs; spend them on the work, not on cargo-cult re-reads.

**Step 5 — Check git status.** Run `git --no-optional-locks fetch origin main develop` **first**, then `git --no-optional-locks status` and `git branch`. The fetch is non-negotiable before judging merge state — a check against stale remote-tracking refs flags drift that's already resolved (or misses drift that exists). Flag any drift:
- If on `main` or `develop` directly → warn, ask which branch to create.
- If there are uncommitted changes from a prior session → surface them.
- If the branch name doesn't match the planned focus → note it.
- **If `git log` shows merged work that `CHATLOG.md` doesn't mention** → a previous session likely ended without closing (API error, accidental close, network drop). Offer to reconstruct the missing CHATLOG entry from git + transcript BEFORE starting new work.

**Step 6 — Ask for Current Focus.** Present 2–3 grounded options (via `AskUserQuestion`) derived from the roadmap and last CHATLOG entry. Do NOT guess — ask. Wait for the user's answer before any work.

**Standing-checks sub-rule.** Before presenting focus options, read the `## Standing Checks` section of `docs/BACKLOG.md` (if present). Any open `[ ]` item there MUST be surfaced as the first option — before the regular focus suggestions. Once the user confirms an item is resolved, tick it `[x]` inline.

**Scope-sprawl audit sub-rule.** Audit the previous CHATLOG entry's `**Next session:**` line. If the named work bundles **≥3 distinct deliverables** OR introduces a new ADR-worthy surface, present the **smaller-cleaner first increment as the Recommended option** with the full bundle as an alternative — not the bundle as the only option. The rule applies recursively (if one bundled item is itself a ≥3-item bundle, surface its smallest first increment).

**Verify-before-recommending sub-rule.** Before presenting a previous CHATLOG's "Next session:" item as Recommended, verify it is still pending. Cheap checks first: `git log origin/main` for code/merge claims; for visibly-deployable artifacts (running services, dashboards, merged PRs) ask the user one sentence rather than trusting the doc. **Especially required when the previous CHATLOG entry is marked `RECONSTRUCTED`** — anything completed after the originating session's failure appears in neither git nor transcript, so the "Next session:" line is unverified by definition.

**Claude Code vs Cowork sub-rule.** When the focus involves SSH commands, VPS deployment, running `make pre-push` locally, or git push/pull, recommend Claude Code proactively at Step 6 — before friction appears. Cowork's sandbox is isolated from the Windows shell and SSH keys; Claude Code has direct access. **Trigger:** Step 6 focus mentions SSH, VPS, git push, `make pre-push`, or deploy.

**Step 7 — Restate Current Focus, self-critique, ask for approval.** Summarize in 1–2 sentences what you're about to do. Then run the **planning self-critique pass** — ask explicitly: "is this the most performant and efficient approach for this focus?" Surface anything the first plan would under-deliver on (missed iron rules, scope gaps, more efficient verification paths, simpler architecture, redundant work). For trivial tasks see the Fast-path branch below; for new content landing in user-facing docs, code, or architecture, it must be a substantive list — not theater. Then wait for "go". This is the **planning leg of the Continuous Improvement principle** in `CLAUDE.md`.

**Pre-implementation CR sub-rule (MANDATORY by default).** For any non-trivial plan, spawn an unbiased CR (the `review-loop` skill, the `deep-review` skill, or a one-shot `Agent` reviewer) as PART of Step 7 — **before** presenting the plan with "Go?". The CR is the last bullet of Step 7; the revised plan that comes out of it is what the user approves. This is the protocol expression of Rule 11 (automatic adversarial review) — opt-out, not opt-in.

**Mechanical self-check before any "Go?" / "Approve?" / "Ready for approval" line:** *"Have I run an unbiased CR on this plan in this turn?"* If no AND the plan is not Fast-path-trivial → run the CR now, fold findings, then present. Do NOT ask for approval without the CR step in the same turn.

**Opt-out only fires when ALL hold:** (a) plan meets the Fast-path-trivial definition (≤1 file, ≤5 lines, zero logic implications, not a doc/architecture/design surface), OR (b) the user explicitly says "skip the review for this one". A user request that *includes* "unbiased review" / "CR the plan" / "subagent review" is a strengthener (e.g. forces deep-review skill specifically), not the trigger — the CR runs either way.

**Verify-before-finalize sub-rule.** A plan section titled "pre-coding verification" or "open assumptions to check after go" is a smell. If the plan holds N "verify later" assumptions, **answer them with greps/reads BEFORE presenting the plan** — a 30-second `Read`/`Grep` is always cheaper than re-planning when an assumption turns out wrong.

**Goal-quantification sub-rule.** When the user states a goal in measurable terms ("economy", "save tokens", "faster", "smaller", "fewer X", "cheaper"), the Step 7 self-critique MUST quantify how the proposed approach delivers on that metric with concrete numbers BEFORE presenting the plan. "This saves ~X tokens per orientation" is approval-ready; "this makes it more readable" is not (unless readability was the stated goal).

**Runtime-resource connectivity probe sub-rule.** When the focus involves an external runtime resource (SSH host, broker API session, DB connection, deployment target), the Step 7 self-critique MUST include a non-destructive connectivity/auth probe BEFORE the plan is presented. The probe is one tool call (`ssh -o BatchMode=yes -o ConnectTimeout=10 chappy-vps 'true'`, equivalent). If it fails, the session's actual shape is "bootstrap access," not "use the resource."

**External-dependency obtainability sub-rule.** When the plan depends on obtaining a specific external version/package/archive/installer/service tier, the Step 7 self-critique MUST verify obtainability via one quick check (`WebSearch`/`curl -I`/equivalent) BEFORE the plan is approved.

**VPS sudo-over-SSH sub-rule.** When the focus involves `sudo` commands on the VPS via SSH, the Step 7 plan MUST tag each remote command as either non-sudo (runnable from the Bash tool / Claude Code) or interactive-sudo (handed to the user to run in their own terminal). Bash-tool SSH does not allocate a TTY and cannot satisfy interactive `sudo` prompts. **Trigger:** any VPS test/deploy/diagnose focus that may need `sudo`.

**ONLY after Step 7 — begin actual work.**

---

## Fast-path branch (for trivial tasks)

When the focus chosen at Step 6 is **trivial**, the opening ritual downgrades:
- **Step 4b deeper reads:** SKIP.
- **Step 7 planning self-critique:** one-line acknowledgment instead of a substantive critique. "Plan: do X exactly as described. Go?" is sufficient.

**Trivial-task definition — ALL must hold:** touches at most ONE file; changes ≤5 lines OR is a doc typo fix / single-line config tweak / single-file rename / comment-only edit / `[ ]`→`[x]` tick; zero logic implications; does NOT create or delete files; is NOT a design document or anything triggering the code-writing protocol.

**When in doubt, the focus is NOT trivial.** The default is the full ritual. **Scope-expansion fall-back:** if fast-path work expands beyond the trivial definition (e.g. a "one-line tweak" reveals a related bug), STOP and run the full Step 4b + Step 7 against the expanded scope before continuing.

---

## Refusal clause

If the user says "just start" or tries to skip the ritual, politely refuse once: "The protocol exists so we don't get into a mess. Let me run the opening ritual — it takes 2 minutes." Then run it. If the user insists a second time, run the ritual anyway and flag that they overrode it.

---

## Trigger Guide — when to load `SESSION_RULES.md` (and `WORKFLOW.md`)

`SESSION_RULES.md` is NOT read at session start by default — it loads **just-in-time** when one of the triggers below fires. This keeps orientation cheap (just `OPEN_SESSION_PROTOCOL.md`) while preserving every rule body in the rules file.

**Load `SESSION_RULES.md` when any of these fires:**

- **About to commit / push / declare code ready** → Rule 5 (pre-push verification) + Rule 6 (gate format + lead-with-the-gate)
- **About to `Edit`/`Write` a `.py` file** → Rule 5 sub-rules (immediate black, ruff sweep, zip-strict, SIM117, I001, type-construction grep, `__post_init__` scan)
- **About to `Edit`/`Write` a `.md`/`.yaml` file** → Rule 5 trailing-whitespace sub-rule
- **About to run a `git` command from the sandbox** → Rule 4 (`--no-optional-locks`)
- **CHATLOG dated-entry count divisible by 10** → Rule 1 (CHATLOG archival); divisible by 5 → Rule 2 (BACKLOG review)
- **User suggests something off the current ROADMAP scope** → Rule 3 (scope-creep capture)
- **About to draft an ADR / finish a plan with "ready for approval"** → Rule 11 (automatic adversarial review)
- **A subagent claims a code path is unused / never called / never imported** → Rule 12 (absence-claim verification + parallel-batch)
- **About to write `✅ Done` / claim a phase complete** → Rule 13 (acceptance-signal verification)
- **User flags context length** → Rule 10 (context-exhaustion early-close)
- **About to write any new code** → Rule 8 (spec → review → code → QA pipeline) + Rule 7 (C-extension coverage) if a C-extension/optional dep is involved
- **About to write or modify any `scripts/*.py`** → Rule 9 (script logging init)
- **About to call `web_fetch`, or instructions involving networking/secrets/Mac-or-Windows CLI** → Web research rule + relevant engineering rules
- **User asks about workflow, starter prompts, chat types, or how to open a new chat** → load `WORKFLOW.md` (user-facing reference; not read at orientation)

**TradeBot engineering-rule triggers (all bodies in `SESSION_RULES.md` → TradeBot-Specific Engineering Rules):**

- **About to track position/order state alongside the broker** (any stateful strategy, reconcile, pending-timeout, each-tick check) → **Broker-state-authority rule**
- **About to wrap ib_insync calls in `run_coroutine_threadsafe`** → **ib_insync sync-vs-async rule**
- **About to convert a `@staticmethod` to an instance method touching `self._lock`** → **Lock-reentrancy audit rule**
- **About to write `fetch("/api/X")` in dashboard JS** → **API endpoint verification rule**
- **About to write a time-stop / cooldown integration test** → **Time-based exit test rule**
- **About to migrate a persisted-state schema version** → **Schema migration durability rule**
- **About to add `if not IS_CI:` guards to a test** → **CI test-runner guard rule**
- **About to commit production code** (strategies/broker/risk/runtime/backtester, REGISTRY, new StrategyConfig) → **Unbiased CR mandatory rule** + **CR-to-fix transition rule**
- **About to defer a CR finding as "pre-existing"** → **"Pre-existing" deferral rule**
- **About to describe/plan around a BACKLOG/ROADMAP item** → **Describe-from-source rule**
- **About to populate a fixture/DB to verify a feature** → **Pre-fixture wiring check rule**
- **About to write an invisible Unicode literal (BOM, ZWSP)** → **Invisible Unicode literal rule**
- **Edits live in a worktree + user shell is the main checkout** → **Worktree commit-handoff rule**
- **Multiple feature branches touching the same docs file** → **Stacked PR rule**
- **About to ask the user a fact a grep/read could answer** → **"Verify before asking" rule**
- **Investigating a "stopped"/"stale" symptom** → **Debugging discipline rule**

**Strengthened catch-all:** when unsure whether a rule applies, **load it**. The cost of loading once is small; the cost of missing a rule that should have fired is one extra round-trip (or a real bug). Default bias is OVER-load.

**Phase-transition full-read:** when the project transitions between major phases (e.g. paper → live deployment), Step 4 includes a full re-read of all rule files regardless of other triggers. Phase boundaries are the highest-risk moments for "rule should have fired but didn't" misses.
