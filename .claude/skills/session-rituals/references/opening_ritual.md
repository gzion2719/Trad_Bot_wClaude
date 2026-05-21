# Opening Ritual — Full Steps

Loaded from `SKILL.md` when the opening ritual fires. The trigger is **the first user message of the chat — full stop.** A greeting, a question, a direct work request, an emoji-only message, a one-word message — anything triggers it. There is no list of magic words.

Run the steps in order. Do NOT skip. Do NOT start substantive work until all 7 steps complete.

If the first message is itself a substantive request (e.g. "let's build X"), acknowledge it briefly, run the ritual, return to it as one of the focus options at Step 6.

---

## What counts as the user's "first message"

The first message is **only the body the user typed in this chat turn**. User-profile metadata blocks at the top of the turn — anything labelled `Name:`, `Email address:`, `Profile:`, etc. — are **never** the user's message. If the typed body is empty or is just a greeting, treat the body alone as the first message — do not synthesize one from the metadata. If the body is genuinely empty, ask the user what they want to work on, then run the ritual on their answer.

---

## Step 1 — Greet warmly

One line. Set a friendly tone before any orientation work.

## Step 2 — Verify the working environment

Confirm the right folder / workspace / repository / project context is loaded and accessible. If not, request it (use the platform's directory-request mechanism, or ask the user to attach / mount the right folder).

Tell the user explicitly: "Working environment confirmed" before proceeding.

## Step 3 — Confirm protocol is in effect

Read the SKILL.md (or this file) if you haven't already. Tell the user explicitly: "Protocol loaded" so they know you're operating under the pattern.

**File-size early-warning sub-rule.** Immediately after reading this file, check whether it required a `limit` or `offset` parameter to load (i.e. the Read tool returned a token-limit error on the first attempt). If so, surface a warning before proceeding — don't silently chunk-read a critical protocol file. A file that can't be read whole is a protocol risk and the user must know at session-open, not when a missed step surfaces later.

## Step 4 — Orient on project state (just-in-time, parallel reads)

Read only what's needed to choose a focus. Defer everything else to after Step 6.

**Step 4a (always, upfront, parallel):**

1. The project's running **session log** (last 3 entries — where we left off, most recent first)
2. The project's **plan-of-record** (which phase / week / milestone we're in)

Reads MUST run in parallel — one assistant message, multiple file-read calls. No serial reading. The two reads inform Step 6's focus options.

If the project doesn't have a session log or a plan-of-record, skip the missing read and tell the user explicitly: "I would normally read [X] here, but it doesn't exist in this project. Want me to scaffold it, or skip this step?" The ritual structure still applies; the reads adapt to what's there.

**Step 4b (just-in-time, AFTER Step 6 focus is chosen):** read only the deeper docs the chosen focus actually needs. Examples (your project's will differ):

- Focus = **safety-critical code** → read the project's risk-management doc + relevant agent / module specs
- Focus = **new feature or new module** → read the architecture doc + per-module specs
- Focus = **library / tooling choice** → read the tech-stack doc
- Focus = **first chat ever, or a major scope / vision conversation** → read the project's README / overview
- Focus = **routine continuation of last session's work** → nothing extra; go straight to work

**The principle: load just-in-time, not just-in-case.** Token budget and latency are real costs; spend them on the work, not on cargo-cult re-reads.

## Step 5 — Check version-control state

Run a `git status` (or your project's VCS equivalent). Report any uncommitted changes or drift from the main branch. Don't quietly start work on top of a dirty state.

For sandboxes that can't unlink lockfiles, use `git --no-optional-locks <subcommand>` for all read operations (status, log, diff, etc.) to avoid leaving stale lockfiles behind.

## Step 6 — Ask for the current focus

Present **2–3 concrete options** based on what you read at Step 4a. Use a structured multi-option question if your platform supports it (e.g., AskUserQuestion tool); otherwise list the options as a numbered list and ask the user to pick. Do NOT guess. Wait for the user's answer before any work.

**Example options to offer:**

- The next unchecked task in the project's plan-of-record
- Continuing something flagged as open in the last session-log entry
- A different direction the user wants to take

**Standing-checks sub-rule.** Before presenting focus options, read the "standing checks" section of the project's backlog (if it has one). Any open question there MUST be surfaced as the first option at Step 6 — before the regular focus suggestions. Once the user confirms an item is resolved, tick it `[x]` inline.

**Scope-sprawl audit sub-rule.** Before presenting options, audit the previous session log's "Next session" line. If the named work bundles ≥3 distinct deliverables OR introduces a new architectural decision surface, the Step 6 options must present the smaller-cleaner first increment as the Recommended option with the full bundle as an alternative — not the bundle as the only option.

## Step 7 — Restate the focus, run planning self-critique, ask for approval

Summarize in 1–2 sentences what you're about to do. Then run the **planning self-critique pass** — ask explicitly: "is this the most performant and efficient approach for this focus?" Surface anything the first plan would under-deliver on (missed rules, scope gaps, more efficient verification paths, simpler architecture, redundant work).

For trivial tasks (tick a box, two-line edit) a one-line acknowledgment is fine — see Fast-path branch below. For new content landing in user-facing docs, code modules, or architectural decisions, the critique must be a substantive list — not theater.

Then wait for "go". This is the **planning leg of the Continuous Improvement principle**.

**Goal-quantification sub-rule.** When the user states a goal in measurable terms ("economy", "save tokens", "faster", "smaller", "fewer X", "more efficient", "cheaper"), the Step 7 self-critique MUST quantify how the proposed approach delivers on that metric with concrete numbers BEFORE presenting the plan for approval. "This saves ~X tokens" is approval-ready; "this makes the file more readable" is not (unless readability was the stated goal).

**Runtime-resource connectivity probe sub-rule.** When the focus involves an external runtime resource (SSH host, broker API session, database connection, deployment target), the Step 7 self-critique MUST include a non-destructive connectivity / auth probe BEFORE the plan is presented for approval. If the probe fails, the session's actual shape is "bootstrap access," not "use the resource."

**External-dependency obtainability sub-rule.** When the plan depends on obtaining a specific external version, package, archive, installer, or service tier, the Step 7 self-critique MUST verify obtainability via one quick check (web search / `curl -I` / equivalent) BEFORE the plan is approved.

**ONLY after Step 7 — begin actual work.**

---

## Fast-path branch (for trivial tasks)

When the focus chosen at Step 6 is **trivial** — defined narrowly below — the opening ritual downgrades:

- **Step 4b deeper reads:** SKIP. No focus-specific deeper-docs reads needed.
- **Step 7 planning self-critique:** one-line acknowledgment of the approach instead of a substantive critique list. "Plan: do X exactly as described. Go?" is sufficient.

The full Step 7 self-critique exists to catch under-rounded plans on substantive work. Trivial work has nothing meaningful to under-round; the critique becomes theater.

**Trivial-task definition — ALL conditions must hold:**

- Touches at most ONE file.
- Changes ≤5 lines, OR is one of: doc typo fix, single-line config tweak, single-file rename, comment-only edit, ticking `[ ]` → `[x]` on a status item.
- Has zero logic implications (no new behavior, no algorithm change, no new dependency).
- Does NOT create or delete files.
- Is NOT a design document or anything that would normally trigger a full code-writing protocol.

**When in doubt, the focus is NOT trivial.** The default classification is "regular ritual." Misclassifying a non-trivial focus as trivial costs more than the reverse.

**Examples of trivial (fast-path applies):**

- "Fix the typo on line 142 of the architecture doc"
- "Change `position_size_pct: 1.0` → `0.5` in the config file"
- "Rename `foo.py` to `bar.py`"
- "Tick `[ ]` → `[x]` on a completed backlog item"

**Examples of NOT trivial (regular path):**

- "Implement feature X" (multi-file, new behavior)
- "Write an ADR for Y" (design work)
- "Add a new strategy parameter" (touches code + tests + config)
- Anything where the Step 6 option's description is longer than one sentence
- Anything that touches application logic, even a one-line change

**Scope-expansion fall-back:** if during fast-path work the scope expands beyond the trivial definition (e.g., the "one-line tweak" reveals a related bug that needs fixing), STOP at that moment and run the full Step 4b + Step 7 self-critique against the expanded scope BEFORE continuing. Scope creep on the fast-path is a protocol violation.

---

## Refusal clause

If at any point the user says "just start" or tries to skip the ritual, politely refuse once and explain: "The protocol exists so we don't get into a mess. Let me run the opening ritual — it takes 2 minutes." Then run it. If the user insists a second time, run the ritual anyway and flag in the response that they overrode it.

---

## Trigger Guide pattern (just-in-time rule loading)

Most rules in a mature project don't need to be loaded at every session start. They live in separate files and load only when their trigger fires. The Trigger Guide is a list of conditions (e.g., "about to commit," "about to edit a code file," "session count divisible by N") that map to which rules to load.

The pattern keeps orientation cheap (no monolithic rules file) while preserving every rule body verbatim in the right location, loaded only when needed.

**Strengthened catch-all:** when uncertain whether a rule applies, the cost of loading once is much smaller than the cost of missing a rule that should have fired. **When in doubt, load it.** Concrete heuristic: if you can imagine a session-recap bullet reading "we should have caught X with rule Y," and rule Y exists in any project file under the Trigger Guide, you should have loaded rule Y. Default bias is OVER-load, not under-load.

**Phase-transition full-read:** when the project transitions between major phases (e.g., development → live deployment), the opening ritual Step 4 includes a full re-read of all rule files regardless of other triggers. Phase boundaries are the highest-risk moments for "rule should have fired but didn't" misses — the safety surface changes substantially. The full re-read is cheap insurance at the moment when the cost of a miss is highest.

**Specific Trigger Guide entries are project-dependent.** Define them in the project's own protocol files; the SKILL just provides the pattern.
