# TradeBot — Session Protocol

This file defines the opening and closing ritual for every Claude session on this project.
Read this file immediately after reading CLAUDE.md at the start of every chat.

**Language:** Hebrew or English in → English out. Always.

---

## Opening Ritual

Fires on the **FIRST user message** of any chat — no magic word required.
A greeting, a question, a task, an emoji — all trigger the ritual.

### Step 1 — Greet

One warm line. Keep it brief.

### Step 2 — Verify working folder

Confirm the working directory is the TradeBot project root:
`C:\Users\galzi\OneDrive - Afiki-C\Afikim\TradeBot`

If the folder is not accessible, surface the error and ask the user to mount it before proceeding — file reads will fail without it.

Confirm: **Folder confirmed ✅** before moving on.

### Step 3 — Confirm WORKFLOW.md in effect

Read `WORKFLOW.md` if not already read in this session. One-line ack to the user.

### Step 4 — Orient (just-in-time)

Read **only** these two files upfront:
- `CHATLOG.md` — last 3 entries only (scroll to the top, read the 3 most recent dated sections)
- `docs/ROADMAP.md` — current phase and pending items

Defer deeper files (CLAUDE.md architecture sections, strategy files, broker code) to after Step 6 when the focus is chosen. Load just-in-time, not just-in-case.

### Step 5 — Git status

Run `git --no-optional-locks status` and `git branch`.

Flag any drift:
- If on `main` or `develop` directly → warn, ask which branch to create
- If there are uncommitted changes from a prior session → surface them
- If the branch name doesn't match the planned focus → note it
- **If `git log` shows merged work that CHATLOG.md doesn't mention** → previous session likely ended without closing (API error, accidental close, network drop). Offer to reconstruct the missing CHATLOG entry from git + chat transcript BEFORE starting new work. Don't pile new context on top of stale state.

### Step 6 — Ask for focus

Ask via a short question with 2–3 grounded options derived from the ROADMAP and last CHATLOG entry.

**Scope-sprawl audit sub-rule:** if the previous CHATLOG entry's "Next session:" line bundles ≥ 3 distinct deliverables, present the smallest clean first increment as the Recommended option.

Example:
> What's today's focus?
> A) (Recommended) Sunday 2FA recovery test prep — verify VNC tunnel and write recovery checklist
> B) IBKR Trusted IP whitelist (5.9)
> C) Something else — tell me

### Step 7 — Planning self-critique

Restate the chosen focus. Run a substantive critique:
- Is this the most efficient approach?
- Are any non-negotiables from CLAUDE.md touched?
- Is there a smaller cleaner first increment?
- Are there verification paths we'd miss?

For trivial focuses (log check, quick config tweak), a one-line ack is fine.
For anything touching production code, architecture, or deployment: the critique must be a real list.

Wait for "go" before proceeding.

---

## Closing Ritual

**When to run.** Triggered by ANY farewell phrase — "תודה על היום", "see you tomorrow", "we're done", "let's call it", "thanks", a goodbye emoji, anything that signals end-of-session. Don't just say goodbye; run the ritual.

**Why it exists.** The closing ritual is NOT a session diary. It exists to make the NEXT session's first 60 seconds frictionless: read the last 3 entries, know exactly where we left off and where to look for detail. The orientation chain reads it every chat. Each entry's job is "where we left off, what the open question is, where to look for the detail." Compounding is the whole game — one concrete improvement per session × 200 sessions = a system that runs perfectly with zero friction.

### Step 1 — Retrospective (the most important step)

Before writing anything for the record, take a structured look at the SESSION ITSELF — not the work product. Three bullets, in your head or on screen:

- **What worked:** moves that were efficient, decisions that paid off, friction we successfully avoided.
- **What didn't:** protocol slips, dead ends, things we redid, places we read/wrote/checked things we didn't need, over-engineered fixes.
- **Improvement for next session:** ONE concrete, actionable change. A protocol tweak, a habit shift, a new rule of thumb.

The improvement is the OUTPUT, and it has two possible homes:

1. **Codifiable as a rule** (it almost always is) — edit the relevant file IN THIS SAME SESSION (`SESSION_PROTOCOL.md`, `CLAUDE.md`, `WORKFLOW.md`, etc.). The edit IS the improvement; don't write a separate description of it. **Before editing, do a conflict check:** grep the file for related rules, confirm the new wording doesn't contradict anything already there.
2. **Not yet codifiable** (an observation we want to remember but can't yet generalize) — keep it as the CHATLOG bullet only.

Either way, ALWAYS add a `**Process improvement:**` bullet to the CHATLOG entry. If genuinely none, say `none this session` explicitly — never silently skip. Future-Claude needs to know we looked.

Show the user the proposed improvement (and any file edits) before moving on. They approve or refine.

### Step 2 — Generate the CHATLOG entry

Compose a 3–5 bullet summary in this exact format:

```
## YYYY-MM-DD — <session title>
- <What we did, bullet 1>
- <What we did, bullet 2>
- <Key decision or learning>
- <Any blockers or open questions>
- **Process improvement:** <what we changed and which file, OR "none this session">
- **Next session:** <one sentence on what's first>
```

Constraints — enforced, not aspirational:

- **Max 5 content bullets** plus the two trailing ones (`Process improvement` + `Next session`). 7 lines total under the date header.
- **Each bullet ≤ 2 sentences.** If a bullet wants to be 4 sentences, the second half belongs in a rule file or BACKLOG — not the CHATLOG.
- **`Process improvement` is a 1-line pointer**, not a retelling. The file edit IS the improvement; the bullet exists to make it discoverable.
- **Don't re-tell bug stories that live elsewhere.** If a bug birthed a rule, the rule file has the details; the CHATLOG entry has one sentence on what was caught and where the rule lives.
- **No meta-reflection bullets.** Reflective content about how the session went belongs in Step 1, not Step 2.

### Step 3 — Write the entry to CHATLOG.md

Insert the new entry directly below the `---` separator, before any existing dated entries (newest-first ordering). Show the user the entry you wrote.

### Step 4 — Report uncommitted work

Run `git --no-optional-locks status` from the project root.

List changed/new files and suggest a commit message.

### Step 5 — Give the exact commands the user needs (gate-first)

The handoff message LEADS with the gate-first bash block — first content block, before any prose summary, before any file list.

```bash
cd "C:\Users\galzi\OneDrive - Afiki-C\Afikim\TradeBot"
make pre-push
git add <files>
git commit -m "<suggested commit message>"
git push
```

`make pre-push` runs: `ruff check` → `black --check` → `mypy` → `python -m tests.run_tests`
It is a verbatim mirror of what `.github/workflows/ci.yml` runs.

**Mechanical pre-send self-check.** Re-read your draft's first 3 lines before sending. If they don't contain `make pre-push`, the draft is wrong — prepend it. This applies to ANY "ready to commit" handoff, not just closing ones.

**develop → main sync check.** After every PR that merges into `develop`, always prompt the user to open a follow-up PR: base `main`, compare `develop`. Do not skip this — `main` is what runs on the VPS.

### Step 6 — Close warmly

One line. In English (the project's output language).

### Step 7 — Plain-English recap + concrete example

AFTER Step 6's farewell, append to the SAME message:

```
---
**In plain English:**
<3-5 short sentences about what we did and why>

**Example:**
<one concrete example — a command, a behavior, a decision, or a snippet>
```

The recap uses English (the project's output language). The CHATLOG bullets cover technical depth; this is the human anchor.

---

## Recurring hygiene

- **Every 5 sessions** (CHATLOG entry count divisible by 5): backlog review — read `docs/BACKLOG.md`, surface 1–2 ripe items as Step 6 options.
- **Every 10 sessions**: CHATLOG archival — keep the most recent 5 entries active in `CHATLOG.md`, move older routine entries to `docs/CHATLOG_ARCHIVE.md` newest-first. Entries with decisions, non-obvious learnings, or gotchas stay in place. When in doubt, keep.
- **Git reads**: always use `git --no-optional-locks` — prevents stale `.git/index.lock`.

---

## ADR discipline

When a session introduces or reverses a significant architectural decision (new data flow, changed interface contract, deployment topology change), write an ADR in `docs/adr/NNNN-slug.md`.

- **ADR-with-new-types sub-rule:** ADRs that introduce frozen dataclasses with structural invariants get a 10-line type skeleton inline in the Decision section before the prose.
- **Test-helper-signature sub-rule:** ADRs that prescribe a test-helper or fixture signature change include the new signature as a code skeleton in the Decision section.

ADR index lives at `docs/adr/README.md`.

---

### Worked example

(TradeBot project — Python IBKR trading bot, `make pre-push` as gate, Hebrew or English in / English out.)

User says: "תודה על היום"

You produce:

> **Step 1 — Retrospective.** Worked: reading CHATLOG first saved re-explaining the Sunday 2FA context. Didn't: I read all of CLAUDE.md at once instead of deferring the architecture sections — wasted 2 round-trips. Improvement: codify "defer broker/strategy sections to after Step 6" into SESSION_PROTOCOL.md Step 4 — small edit.
>
> **Step 2 — Entry:**
>
> ```
> ## 2026-05-04 — Sunday 2FA recovery test
> - Ran Sunday 2FA recovery: VNC tunnel → IB Gateway login dialog → code from IBKR Mobile → bot reconnected in 18s.
> - Confirmed Mon–Sat restarts use cached token (no 2FA needed); documented the exact 60-second routine in CLAUDE.md.
> - Gateway logs show `Auto restart time already set to 11:59 PM` — IBC is healthy.
> - Open: ask IBKR support about push-notification IB Key for Israeli accounts (5.16).
> - **Process improvement:** SESSION_PROTOCOL.md Step 4 gains "defer broker/strategy sections to after Step 6".
> - **Next session:** IBKR Trusted IP whitelist (5.9) + begin monitoring fills vs backtest prices (6.2).
> ```
>
> **Step 3 — Inserted** into CHATLOG.md below the `---` separator, before the 2026-05-01 entry.
>
> **Step 4 — Uncommitted:** `CHATLOG.md` (+8 lines), `SESSION_PROTOCOL.md` (+1 line).
>
> **Step 5 — Commands:**
>
> ```bash
> cd "C:\Users\galzi\OneDrive - Afiki-C\Afikim\TradeBot"
> make pre-push
> git add CHATLOG.md SESSION_PROTOCOL.md
> git commit -m "docs(protocol): log 2FA recovery test + Step 4 defer-read rule"
> git push
> ```
>
> **Step 6 — See you next time.**
>
> ---
> **In plain English:** Today we tested the Sunday morning recovery routine for the first time — the weekly flow where IBKR invalidates all tokens and someone has to enter a fresh 2FA code. It worked in 18 seconds. We documented the exact steps in CLAUDE.md so any team member can do it in under a minute, and we tightened the session protocol to avoid reading files we don't need yet.
>
> **Example:** The full recovery sequence is: `ssh chappy-vps`, open a second terminal with `ssh -L 5900:localhost:5900 chappy-vps`, connect TightVNC to `localhost:5900`, enter the 6-digit code from IBKR Mobile, then confirm with `sudo journalctl -fu tradebot` that the bot shows `Connected | account=DUE090987`.
