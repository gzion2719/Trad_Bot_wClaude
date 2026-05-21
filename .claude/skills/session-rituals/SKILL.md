---
name: session-rituals
description: Run a structured opening or closing ritual at the start or end of any working session. Trigger at the FIRST user message of any chat (greeting like "hi" / "good morning", a question, a direct work request, anything) for the opening ritual; trigger on any farewell phrase ("thanks", "see you tomorrow", "we're done for today", etc.) for the closing ritual. Make sure to use this skill whenever the user signals a session boundary, even when the message is brief or implicit — the rituals exist to keep multi-session work coherent and compounding, and skipping them silently breaks the pattern. Also use this skill for meta-questions about working patterns ("how should we structure our work", "what's the protocol", "how do we close out") and when introducing this working pattern to a new project or new collaborator.
---

# Session Rituals

A structured working pattern for multi-session AI-assisted work. The pattern has three moving parts: an **opening ritual** that fires on the first user message of every chat, a **closing ritual** that fires on a farewell signal, and a **continuous-improvement principle** that ties them together so each session leaves the work measurably better than it started.

This skill provides the generic, portable structure. **Project-specific identity** (project name, non-negotiables, style preferences, language preferences) lives in the project's own `CLAUDE.md` (or equivalent project-instructions file), not in this skill. The skill defers to that file for "what" we're working on; the skill defines "how" the work happens around it.

---

## When this skill applies

- **First message of any chat** (the trigger is "first message," not a specific phrase). A greeting, a question, a direct work request, even a one-word message — anything triggers the opening ritual.
- **Farewell signal at any point** ("thanks", "see you tomorrow", "we're done", "good night", or any semantically-equivalent close). The trigger is the farewell signal — **task completion alone is NOT a farewell**; wait for the explicit signal.
- **Meta-questions about the working pattern** — questions like "how should we structure our work," "what's the protocol," "should we run a check before committing." Answer by referencing the relevant ritual step or principle below.

---

## The opening ritual

Run on every first message of every chat. The ritual has 7 steps; do them in order; do not skip.

See **`references/opening_ritual.md`** for the full step-by-step instructions including the fast-path branch for trivial tasks, the planning self-critique sub-step, and the project-state orientation pattern.

**Brief summary of the 7 steps:**

1. **Greet warmly** — one line, sets the tone.
2. **Verify the working environment** — confirm the right folder/workspace/repository is loaded; request it if not.
3. **Confirm protocol is loaded** — explicitly tell the user "protocol loaded" so they know you're operating under the pattern.
4. **Orient on project state (parallel reads, just-in-time)** — read the minimum needed to choose a focus: the project's running session log (last few entries) and the project's plan-of-record (upcoming work). Read deeper docs only AFTER focus is chosen.
5. **Check version-control state** — run a `git status` (or equivalent) and surface any uncommitted changes or drift before suggesting work.
6. **Ask for the current focus** — present 2-3 concrete options as a structured multi-option question. Do not guess. Wait for the user's answer.
7. **Restate focus, run planning self-critique, ask for "go"** — summarize what you're about to do. Then explicitly ask: "is this the most performant and efficient approach for this focus?" Surface anything the first plan would under-deliver on. Wait for "go" before any actual work.

**Only after Step 7 — begin actual work.**

**Refusal clause:** if the user tries to skip the ritual ("just start"), politely refuse once and explain why the ritual exists. If the user insists a second time, run the ritual anyway and flag in the response that they overrode it.

---

## The closing ritual

Run only on a farewell signal — never proactively. "Work is complete" is NOT a farewell. Wait for the explicit signal.

See **`references/closing_ritual.md`** for the full step-by-step instructions including the session-score system, the retrospective format, the codification-vs-logging decision (the project may have its own policy for this — see project's `CLAUDE.md`), and the next-session preview format.

**Brief summary of the 8 steps:**

1. **Retrospective** (the most important step) — three honest bullets: what worked, what didn't, ONE concrete improvement for next session. Plus a session score on three axes (code quality / protocol compliance / efficiency). Be honest; inflation makes the score useless.
2. **Compose the session-log entry** — strict format, max 5 content bullets + 2 trailing bullets (process improvement + next-session pointer). Each bullet ≤ 2 sentences.
3. **Write the entry to disk** — MANDATORY file write. Composing the entry as chat prose without writing to disk is a protocol violation.
4. **Report uncommitted work** — run version-control status; list changes; suggest a commit message.
5. **Give the exact commit + push commands** — one fenced code block, gate-first (verification gate → add → commit → push). The commit message is the `-m` argument inside the block, not separate prose.
6. **Close warmly** — one line.
7. **Plain-English recap + concrete example** — 3-5 sentences in plain language about what we did, plus one concrete example (a command, a behavior, a decision).
8. **Next-session focus preview** — 2-3 options for the next session's focus, each tagged with the appropriate tool to open it in (if your project uses multiple tools).

---

## Continuous improvement (the principle that ties it all together)

The whole pattern compounds via a simple loop:

1. **Session N runs.** A mistake is made or a friction is felt.
2. **Closing ritual fires** at the end. The retrospective surfaces the friction. The improvement bullet captures one concrete change.
3. **The change is captured** — either as a new codified rule (if the pattern is recurring) or as an observation in a learnings log (if it's a one-off worth remembering).
4. **Session N+1 runs.** The rule applies. The mistake doesn't happen again.

Each session is one ratchet click. The compounding is slow but real — over months you accumulate hundreds of small lessons, each one removing a class of mistake.

**Two formal codifications of the principle:**

- **Opening Ritual Step 7 (planning leg):** before executing, self-critique the plan. Surface anything that would under-deliver.
- **Closing Ritual Step 1 (closing leg):** after executing, retrospective + capture the improvement. Don't skip even when there's no improvement worth recording — say so explicitly ("none this session") and notice the absence.

A session that doesn't produce an improvement isn't a failure — but a series of sessions without improvements IS a signal that the work is stagnating.

---

## Just-in-time rule loading (the Trigger Guide pattern)

The opening ritual is mandatory; everything else loads only when needed. As a project accumulates rules and protocols, the cost of loading "everything always" grows. The fix: keep a **Trigger Guide** in the opening-ritual file (or equivalent) that lists conditions and the rules they load.

Examples of trigger conditions (your project's will differ):

- "About to commit / push / declare work ready" → load pre-handoff verification rules
- "About to edit a code file" → load formatting / linting rules
- "About to draft a design document" → load the adversarial-review rule
- "Project phase transition" → load the safety review

The Trigger Guide should live in the project's own protocol files. The pattern is portable; the specific triggers are project-dependent.

**Strengthened catch-all:** when in doubt whether a rule applies, load it. The cost of loading once is small; the cost of missing a rule that should have fired is one extra round-trip (or worse, a real bug).

---

## Project-specific integration

This skill provides the generic pattern. Each project layers project-specific content on top via its own `CLAUDE.md` (or equivalent). The project's file should define:

- **Identity:** What the project is. Who the user is. Working language(s).
- **Non-negotiables:** Rules that can never be violated regardless of what the user asks (e.g., risk caps, security rules, data-handling rules).
- **Style preferences:** How the user wants Claude to communicate (e.g., "don't be a yes-man," "present only the correct option").
- **Dispatch:** What to read first, when to load additional files.
- **Trigger Guide:** Project-specific conditions and the rules they load (extends the generic Trigger Guide pattern above).

The skill defers to the project's `CLAUDE.md` for all of these. The skill itself stays generic and portable — install it once, use it on any project.

**Starter scaffold:** when introducing this pattern to a new project, the project author can copy **`assets/starter_CLAUDE_template.md`** as a starting point for their `CLAUDE.md`. The template has placeholders for identity, non-negotiables, style preferences, project-specific Trigger Guide entries, and the optional codification-policy section. Fill in the bracketed `[placeholders]` and you have a working project-instructions file that integrates with this skill cleanly.

---

## Common questions

**Q: What if the user's first message is just an emoji or one word?**
A: Still triggers the opening ritual. The trigger is "first message," not "substantive request." Acknowledge briefly, run the ritual, return to the substantive request at Step 6.

**Q: What if the user explicitly says "we don't need to run the ritual today"?**
A: Run the refusal clause (in opening_ritual.md). Politely refuse once, explain the value. If the user insists a second time, run the ritual anyway and flag the override.

**Q: How do I know if a message is a "farewell signal"?**
A: Look for closing phrases — "thanks", "see you tomorrow", "we're done", "good night", "that's all for today", or any semantically-equivalent close. Do NOT treat "the work is finished" or "task is complete" as a farewell — those are completion signals, not session-end signals. Wait for the explicit close.

**Q: The project has no session log / no plan-of-record / no version control. What now?**
A: The opening ritual's reads are best-effort. Skip Step 4 reads that aren't available, but tell the user explicitly: "I would normally read [X] here, but it doesn't exist in this project. Want me to scaffold it, or skip this step?" Same for Step 5 (git status). The ritual structure still applies; the reads adapt to what's there.

**Q: The project has its own codification policy (e.g., a 3-strikes rule for when to turn a learning into a real rule). How do I integrate?**
A: The closing ritual's Step 1 retrospective produces an improvement bullet. Where that bullet lands (a codified rule edit vs an entry in a learnings log) is governed by the project's policy. If the project has no policy, default to: "if the improvement is generalizable, codify it as a rule in the appropriate project file; if it's a one-off observation, capture it in the session log only."
