# Closing Ritual — Full Steps

Loaded from `SKILL.md` when a farewell signal fires. The trigger is the user signaling end of session — a closing phrase like "thanks", "see you tomorrow", "we're done for today", "good night", or any semantically-equivalent close.

**Important:** task completion alone is NOT a farewell. Wait for the explicit signal from the user. Steps 7 and 8 in particular are closing-only — they never appear as standalone post-completion summaries at the end of a work block. Before writing any "In plain English" paragraph or "Next session — likely focus options" block, run this one-line self-check: "What was the user's last message? Did it contain a farewell?" If no — stop.

---

## Pre-flight: step-completeness check

Before executing Step 1, mentally enumerate all 8 closing-ritual steps and confirm each will fire: Step 1 (retrospective + score), Step 2 (compose session-log entry), Step 3 (write to disk), Step 4 (version-control status), Step 5 (commit block), Step 6 (warm close), Step 7 (plain-English recap + example), Step 8 (next-session focus preview + tool tags).

---

## Step 1 — Retrospective (critique & improvement round)

This is the **most important step of the closing ritual** — the **closing leg of the Continuous Improvement principle**. Every chat must produce an improvement bullet that ratchets the project's performance or efficiency forward; when a chat genuinely doesn't have one, say so explicitly (`none this session`) and notice the absence rather than skipping the step.

**Missed-rules self-check.** Before the retrospective, run a 30-second scan: "did any rule from the project's Trigger Guide that should have fired in this session NOT fire?" Common categories to check: (a) was an edit on a code file done WITHOUT loading the formatting / linting rules? (b) was a design document drafted WITHOUT running the adversarial critic pass? (c) was a "done" tick written WITHOUT verifying the acceptance signal? If any miss is found, note it in the "what didn't work" bullet AND classify it for any project-specific codification policy.

Take a structured look at the *session itself*, not the work product. Three bullets, in your head or on screen — no separate journal file:

- **What worked:** moves that were efficient, decisions that paid off, friction successfully avoided.
- **What didn't:** protocol slips, dead ends, things redone, places read/wrote/checked things that weren't needed, over-engineered fixes.
- **Improvement for next session:** ONE concrete, actionable change. A protocol tweak, a habit shift, a new rule of thumb. **The improvement bullet is the most important** — it's how we compound toward perfection.

**Session Score.** After the three retrospective bullets, compute and display a score. The score is a self-assessment tool — its purpose is to make waste visible and create pressure to improve. A perfect session scores 10/10. Three axes, each scored independently:

| Axis | Max | Scoring |
|------|-----|---------|
| **Code quality** | 4 | Start at 4. −1 per verification-gate round beyond the first. Floor 0. |
| **Protocol compliance** | 3 | Start at 3. −1 per protocol slip (skipped step, wrong language, missing critic pass, wrong commit-block format). Floor 0. |
| **Efficiency** | 3 | Start at 3. −1 per wasted token cluster (unnecessary re-read, dead-end approach that had a known-better path, back-and-forth caused by an avoidable assumption). Floor 0. |

Display format (markdown table — not inline prose):

| Axis | Score | Deductions |
|------|-------|------------|
| Code quality | X/4 | reason, or — |
| Protocol compliance | X/3 | reason, or — |
| Efficiency | X/3 | reason, or — |
| **Total** | **X/10** | |

**Ceiling:** [one sentence — what specific change would raise this score to 10/10 next session]

**Rules:**

- Be honest. Inflation makes the score useless. A 6/10 that identifies the right deductions is worth more than a 9/10 that papers over them.
- The Ceiling line is the most actionable part — it should be a single, concrete, next-session-executable change.
- A score of 10/10 is rare and should only appear when the session was genuinely clean: first-attempt code that passed verification, no protocol slips, no wasted reads.

**The improvement is the OUTPUT.** Where it lands depends on the project's codification policy:

- **If the project has a codification policy** (e.g., a "3-strikes" rule, a learnings log) — defer to it. The policy decides whether this improvement becomes a codified rule or an entry in the learnings log.
- **If the project has no codification policy, use the simpler two-home model:**
  1. **If the improvement is codifiable as a rule** — edit the relevant file in this same session. The edit IS the improvement.
  2. **If it's not yet codifiable** — keep it as the session-log bullet only.

Either way, **always add a `**Process improvement:**` bullet to the session-log entry generated in Step 2** so the change is discoverable from the standard orientation chain. If there's no improvement worth recording, the bullet's value is `none this session` — written explicitly, not omitted, so future sessions know we looked.

**Goal-delivery verification sub-rule.** When the session's stated goal included a measurable target (token savings, performance improvement, file size reduction, error rate, etc.), Step 1 MUST verify the actual delivery with concrete numbers BEFORE composing the session-log entry. Run the measurement, state the before/after numbers, confirm whether the target was met. "Savings confirmed" without a number is not acceptable.

Show the user the proposed improvement (and any file edits) before moving on. They approve or refine.

## Step 2 — Compose the session-log entry

Strict format:

```markdown
## YYYY-MM-DD — <session title>
- <What we did, bullet 1>
- <What we did, bullet 2>
- <Key decision or learning>
- <Any blockers or open questions>
- **Process improvement:** <what we changed and which file, OR "none this session">
- **Next session:** <one sentence on what's first>
```

**Constraints — these are enforced, not aspirational:**

- **Max 5 content bullets** plus the two trailing bullets (`**Process improvement:**` + `**Next session:**`). 7 lines total under the date header. If a session genuinely produced more than 5 distinct points, pick the 5 most useful for the next session's orientation.
- **Each bullet ≤ 2 sentences.** If a bullet wants to be 4 sentences, the second half belongs in the rule file, a design doc, or backlog — not the session log.
- **`**Process improvement:**` is a 1-line pointer**, not a retelling. Format: "`Rule X gained Y sub-rule (see rule-file § Rule X)`" or "`ADR-NNNN written, see docs/adr/`". The edit IS the improvement; the session-log bullet exists to make the change discoverable, not to re-tell it.
- **Do NOT add a "meta scoreboard" bullet.** Reflective meta-content about which codifications fired is Step 1 reflection value, not Step 2 orientation value.
- **Do NOT re-tell bug stories that already live elsewhere.** If a bug birthed a rule, the rule file has the imperative + concrete trigger + session-log date pointer; the session-log entry has at most one sentence on what was caught and where the rule lives.

The orientation chain reads the session log's last 3 entries every session. Each entry's job is to tell future Claude "where we left off, what the open question is, and where to look for detail" — not to be a session diary.

## Step 3 — Write the entry to disk

- Read the project's session log file.
- Insert the new entry directly below the header block, before any existing dated entries (most recent first).
- **MANDATORY: use a file-write tool to write the entry to disk.** Composing the entry as prose in the message body without calling a file-write tool is a protocol violation — the file will not be updated and the next session will be missing context.
- Show the user the entry you wrote.

## Step 4 — Report uncommitted work

Run version-control status. List any changed / new files and suggest a commit message.

## Step 5 — Give the exact commit + push commands

Don't assume the user will remember. Paste:

```bash
cd "<path/to/project>"
<verification gate command>                 # e.g., make pre-push — runs the project's CI gate locally
git add .
git commit -m "<suggested commit message>"
git push
```

**MANDATORY commit-block format rule.** ALL commands must appear together in ONE single fenced bash code block, every time, with no exceptions. The commit message is the `-m` argument on the `git commit` line inside that block. It is NEVER sent as a separate message, a separate code block, or inline prose. If you catch yourself writing the commit message text outside a bash block, stop and rewrite the whole Step 5 output as a single block before sending.

**"Lead with" means *first content block*, not "mentioned somewhere":** in any "code is ready" handoff message, the gate-first code block MUST be the FIRST content block in the message — before any prose summary, file list, results recap, or closing-trigger nudge. The summary can come AFTER the gate, where the user reads it linearly only after seeing what to run.

## Step 6 — Close warmly

One line. "See you tomorrow." / Equivalent close in the user's preferred language (but never in a language the rendering environment can't display cleanly).

## Step 7 — Plain-English recap + concrete example

AFTER Step 6's farewell, append to the SAME message a short paragraph (3–5 sentences max) summarizing what we did this session in plain language, followed by ONE concrete example that makes the work tangible (a command we ran, a behavior we built, a decision we made). Keep it simple, no jargon dumps — the goal is a quick anchor on the day's work, not a technical brief.

Format:

```
---
**In plain English:**
<3–5 short sentences about what we did and why>

**Example:**
<one concrete example — a command, a behavior, a decision, or a snippet>
```

## Step 8 — Next-session focus preview + tool recommendation

AFTER Step 7's example block, append a 2–3 option preview of the focus choices Claude expects to offer at the next session's Step 6, AND tag each option with the recommended tool to open the next chat in (if your project uses multiple tools — e.g., a desktop conversational tool vs a CLI-bound tool).

The purpose: the user decides tool-of-choice BEFORE opening the next chat — otherwise the tool decision happens mid-session at Step 6, which is too late if they picked the wrong tool.

Format:

```
---
**Next session — likely focus options:**
- **Option A (Recommended):** <description> — *Tool: <X>* (<reason>)
- **Option B:** <description> — *Tool: <Y>*
- **Option C:** <description> — *Tool: <Z>*

**Recommended tool:** <X|Y|Z> — based on the leading candidate.
```

**Constraints:**

- 2–3 options (same scope-discipline as Step 6).
- The per-option tool tag is required, not optional — that's the whole point of the step. Skip the tool tags only if your project uses a single tool.
- The bottom line collapses to a single recommendation matching the leading candidate's tool.
- If a focus genuinely spans both tools (e.g., "draft a design doc then deploy"), name the FIRST tool needed and note the handoff in the option's reason.
