# Session Protocol — Closing Ritual

> **Split file note (2026-05-21):** This file is one of three split from the former `SESSION_PROTOCOL.md`. Sibling files:
> - Opening Ritual → `OPEN_SESSION_PROTOCOL.md` (loaded on first message)
> - Session-wide rules (Rules 1–13 + Additional rules + TradeBot engineering rules) → `SESSION_RULES.md`
>
> All historical cross-references like "see `SESSION_PROTOCOL.md` → Rule X" resolve via the navigation stub at `SESSION_PROTOCOL.md`.

> Claude: run this ritual ONLY when the user signals farewell. Do not run proactively. **Task completion is not a farewell trigger** — wait for an explicit farewell signal.

---

## Trigger: any closing farewell

Triggered by ANY farewell phrase — "תודה על היום", "see you tomorrow", "we're done", "let's call it", "thanks", a goodbye emoji, anything that signals end-of-session. Don't just say goodbye; run the ritual.

**Why it exists.** The closing ritual is NOT a session diary. It exists to make the NEXT session's first 60 seconds frictionless: read the last 3 entries, know exactly where we left off and where to look for detail. Compounding is the whole game — one concrete improvement per session × 200 sessions = a system that runs with near-zero friction.

**Steps 7/8 firewall.** Steps 7 ("Plain-English recap") and 8 ("Next-session focus preview") are closing-ritual steps — they ONLY ever appear after the user has given an explicit farewell. They NEVER appear as standalone post-completion summaries at the end of a work block. Before writing any "**In plain English:**" paragraph or "**Next session — likely focus options:**" block, run this one-line self-check: "What was the user's last message? Did it contain a farewell?" If no — stop. It does not matter how complete the work feels; task completion is not a farewell trigger.

**Pre-flight: step-completeness check.** Before executing Step 1, mentally enumerate all 8 closing-ritual steps and confirm each will fire: Step 1 (retrospective + score), Step 2 (compose CHATLOG entry), Step 3 (write to disk), Step 4 (git status), Step 5 (gate-first commit block + PR links + deploy), Step 6 (warm close), Step 7 (plain-English recap + example), Step 8 (next-session preview + tool tags).

---

## Closing Ritual (run every time)

### Step 1 — Retrospective (critique & improvement round)

This is the **most important step** — the closing leg of the Continuous Improvement principle. Before writing the day's record, take a structured look at the *session itself*, not the work product. Three bullets:

- **What worked:** moves that were efficient, decisions that paid off, friction we avoided.
- **What didn't:** protocol slips, dead ends, things we redid, places we read/wrote/checked things we didn't need, over-engineered fixes.
- **Improvement for next session:** ONE concrete, actionable change. A protocol tweak, a habit shift, a new rule of thumb. This is the most important bullet — it's how we compound.

**Session Score.** After the three bullets, compute and display a score. Its purpose is to make waste visible and create pressure to improve. A perfect session scores 10/10. Three axes, scored independently:

| Axis | Max | Scoring |
|------|-----|---------|
| **Code quality** | 4 | Start at 4. −1 per `make pre-push` round beyond the first. Floor 0. |
| **Protocol compliance** | 3 | Start at 3. −1 per protocol slip (skipped step, wrong language, missing critic pass, wrong commit-block format). Floor 0. |
| **Efficiency** | 3 | Start at 3. −1 per wasted token cluster (unnecessary re-read, dead-end approach with a known-better path, avoidable back-and-forth). Floor 0. |

Display as a table (not inline prose), after the three bullets and before the improvement write-up:

| Axis | Score | Deductions |
|------|-------|------------|
| Code quality | X/4 | reason, or — |
| Protocol compliance | X/3 | reason, or — |
| Efficiency | X/3 | reason, or — |
| **Total** | **X/10** | |

**Ceiling:** [one sentence — the single concrete change that would raise this score to 10/10 next session]

Rules: be honest — inflation makes the score useless; a 6/10 that names the right deductions beats a 9/10 that papers over them. The Ceiling line is the most actionable part. A 10/10 is rare and only appears when the session was genuinely clean (first-attempt code that passed pre-push, no protocol slips, no wasted reads).

The improvement is the OUTPUT, with two possible homes:
1. **Codifiable as a rule** (almost always) — edit the relevant file IN THIS SAME SESSION (`OPEN_SESSION_PROTOCOL.md`, `CLOSE_SESSION_PROTOCOL.md`, `SESSION_RULES.md`, `CLAUDE.md`, etc.). The edit IS the improvement; don't write a separate description. **Before editing, do a conflict check:** grep the file for related rules, confirm the new wording doesn't contradict anything there.
2. **Not yet codifiable** — keep it as the CHATLOG bullet only.

Either way, ALWAYS add a `**Process improvement:**` bullet to the CHATLOG entry (Step 2). If genuinely none, say `none this session` explicitly — never silently skip.

**Goal-delivery verification sub-rule.** When the session's stated goal included a measurable target (token savings, performance, file-size reduction, error rate), Step 1 MUST verify the actual delivery with concrete numbers BEFORE composing the CHATLOG entry. Run the measurement, state before/after, confirm whether the target was met. "Savings confirmed" without a number is not acceptable — it's the closing mirror of the Step 7 goal-quantification sub-rule.

Show the user the proposed improvement (and any file edits) before moving on. They approve or refine.

### Step 2 — Generate the CHATLOG entry

Compose a 3–5 bullet summary in this exact format:

```markdown
## YYYY-MM-DD — <session title>
- <What we did, bullet 1>
- <What we did, bullet 2>
- <Key decision or learning>
- <Any blockers or open questions>
- **Process improvement:** <what we changed and which file, OR "none this session">
- **Next session:** <one sentence on what's first>
```

Constraints — enforced, not aspirational:
- **Max 5 content bullets** plus the two trailing bullets. 7 lines total under the date header.
- **Each bullet ≤ 2 sentences.** If a bullet wants to be 4 sentences, the second half belongs in a rule file, an ADR, or BACKLOG.
- **`Process improvement` is a 1-line pointer**, not a retelling. The file edit IS the improvement; the bullet makes it discoverable.
- **Don't re-tell bug stories that live elsewhere.** If a bug birthed a rule, the rule file has the detail; the CHATLOG has one sentence on what was caught and where the rule lives.
- **No meta-reflection / "compounding scoreboard" bullets.** Reflective content belongs in Step 1, not Step 2.

### Step 3 — Write the entry to CHATLOG.md

- Read `CHATLOG.md`.
- Insert the new entry directly below the `---` separator that follows the header block, before any existing dated entries (newest-first).
- **MANDATORY: use the `Edit`/`Write` tool to write the entry to disk.** Composing it as prose without a file-write is a protocol violation — the file won't update and the next session loses context. The write happens before Step 4.
- Show the user the entry you wrote.

### Step 4 — Report uncommitted work

Run `git --no-optional-locks status`. List changed/new files and suggest a commit message.

### Step 5 — Give the exact commands (gate-first)

The handoff message LEADS with the gate-first bash block — first content block, before any prose summary, before any file list.

```bash
cd "C:\Users\galzi\OneDrive - Afiki-C\Afikim\TradeBot"
make pre-push
git add <files>
git commit -m "<suggested commit message>"
git push
```

`make pre-push` runs `ruff check .` → `black --check .` → `mypy . --ignore-missing-imports --exclude 'tests/'` → `pytest tests/ -m "not market"` → `gitleaks detect` → account-ID grep. It is a verbatim mirror of `.github/workflows/ci.yml`. (TWS not running? Use `GITHUB_ACTIONS=true make pre-push` to skip broker tests exactly as CI does.)

**Mechanical pre-send self-check.** Re-read your draft before sending. Two checks, both required:
1. If the draft contains `git push`, it must also contain `make pre-push` earlier in the same message — if not, prepend it.
2. If the draft contains `git push`, it must also contain both GitHub compare URLs (feature→develop AND develop→main) in the same message — if not, add them.

These apply to ANY "ready to commit" handoff, not just closing.

**Two-PR rule — enforced every time, no exceptions.** Every time you say "open a PR", provide BOTH links in the same message, as **clickable markdown links** (not bare code blocks):
1. Feature → develop: `https://github.com/gzion2719/Trad_Bot_wClaude/compare/develop...<branch>`
2. develop → main: `https://github.com/gzion2719/Trad_Bot_wClaude/compare/main...develop`

Never use the `pull/new/<branch>` URL form — it lets GitHub default the base to `main`. Then immediately provide the VPS deploy command:

```bash
ssh chappy-vps
sudo -i
cd /opt/tradebot && git pull origin main && systemctl restart tradebot-dashboard
```

Never give one link without the other. Never omit the deploy command — `main` is what runs on the VPS. If the work lives in a worktree, follow the **Worktree commit-handoff rule** (`SESSION_RULES.md`): Claude commits + pushes from the worktree and hands the user only the PR links + merge + deploy.

### Step 6 — Close warmly

One line, in English.

### Step 7 — Plain-English recap + concrete example

AFTER Step 6's farewell, append to the SAME message:

```
---
**In plain English:**
<3–5 short sentences about what we did and why>

**Example:**
<one concrete example — a command, a behavior, a decision, or a snippet>
```

In English. The CHATLOG bullets cover technical depth; this is the human anchor.

### Step 8 — Next-session focus preview + tool recommendation

AFTER Step 7's example block, append a 2–3 option preview of the focus choices you expect to offer at the next session's Step 6, AND tag each with the recommended tool to open the next chat in (Claude Code vs Cowork). The purpose: the user decides tool-of-choice BEFORE opening the next chat — otherwise the tool decision happens mid-session at Step 6, too late if it required the other tool.

```
---
**Next session — likely focus options:**
- **Option A (Recommended):** <description> — *Tool: <Cowork|Claude Code>* (<reason>)
- **Option B:** <description> — *Tool: <Cowork|Claude Code>*
- **Option C:** <description> — *Tool: <Cowork|Claude Code>*

**Recommended tool:** <Cowork|Claude Code> — based on the leading candidate.
```

Constraints: 2–3 options (same scope-discipline as Step 6); the per-option tool tag is required — that's the point of the step. Use **Claude Code** for SSH/VPS/deploy/`make pre-push`/git-push work (it has direct shell + SSH-key access); **Cowork** for docs, research, planning, dashboards. If a focus spans both, name the FIRST tool needed and note the handoff in the reason.
