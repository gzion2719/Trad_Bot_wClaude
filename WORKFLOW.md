# TradeBot — Workflow Guide

This file defines how to run a Claude session on this project.
Read it at the start of every session (opening ritual Step 3).

---

## Language convention

**Hebrew or English in → English out.**
Write to Claude in whichever language you prefer. Claude always answers in English.

---

## Chat archetypes

### 1. Build (coding, debugging, deploying)
Use when: writing or fixing code, deploying to VPS, wiring new features, running tests.

Starter prompt:
```
Continuing TradeBot work. Focus today: <task from ROADMAP or BACKLOG>.
```

### 2. Research (investigating, comparing, reading docs)
Use when: evaluating a new library, investigating an IBKR API behaviour, comparing strategy approaches.

Starter prompt:
```
TradeBot research session. Question: <what you want to understand>.
Context: <relevant background, e.g. "evaluating Polygon.io vs Alpaca for live data">.
```

### 3. Unrelated
Use when: asking questions that don't belong to this project.
Just open a new chat — don't carry TradeBot context into unrelated work.

---

## When to open a fresh chat

- After ~30 exchanges in the current chat (context degrades)
- After a clear topic switch (e.g., from debugging to strategy research)
- At a sprint boundary
- If Claude contradicts an earlier decision or forgets context established earlier in the same chat

---

## End-of-session phrase (triggers closing ritual)

Any of:
- "תודה על היום" / "thanks for today" / "we're done" / "let's call it" / "see you tomorrow"
- Any goodbye emoji or closing phrase

The closing ritual is non-negotiable — it runs every time, no exceptions.

---

## Pre-push gate

**Always run before every `git push`.** This mirrors CI exactly — catches failures in seconds instead of waiting for GitHub Actions.

```bash
cd "C:\Users\galzi\OneDrive - Afiki-C\Afikim\TradeBot"
make pre-push
```

On Windows without `make`, run the steps manually (see Makefile for exact commands):
```bash
ruff check .
black --check .
mypy .
python -m tests.run_tests
```

---

## Git workflow (enforced — no exceptions)

| Branch | Purpose |
|---|---|
| `main` | Production — what runs on the VPS |
| `develop` | Integration — finished features accumulate here |
| `feature/<name>` | One branch per task, cut from `develop` |
| `hotfix/<name>` | Emergency fix, cut from `main` |

- Never push directly to `main` or `develop`
- All changes go through PRs
- PR to `develop` for features; PR to `main` only when shipping a sprint
- See `CLAUDE.md` for the full branch-protection rules

---

## Red flags — stop and re-orient

- Claude repeats a mistake you already corrected earlier in the session
- Claude contradicts a decision recorded in CLAUDE.md, CHATLOG.md, or an ADR
- Claude generates code that contradicts the architecture in CLAUDE.md
- Claude skips the planning self-critique (Step 7) and jumps straight to code

If any red flag fires: paste the relevant section of CLAUDE.md or CHATLOG.md into the chat and ask Claude to re-read it before continuing.

---

## CI test-runner guard rule

When adding `if not IS_CI:` guards to a test file, always verify with a grep **after** all edits that no `get_client()` (or equivalent broker call) remains in any section assumed to be broker-free:

```bash
grep -n "get_client()" tests/run_tests.py
```

Cross-reference every line number against the section it falls in. A section header saying "no connection needed for most" is not sufficient — check the actual call sites.

Example (2026-05-02): Section 11 header said "no connection needed for most" so its call blocks weren't guarded; 14 RM integration tests called `get_client()` inside function bodies and CI failed again.

---

## Debugging discipline

Before hypothesizing failure modes for a "stopped" or "stale" symptom, read the producer code to confirm the **expected** cadence. Most "X stopped firing" investigations are actually "X is firing on the cadence I forgot it had." Check expected behavior first, then look for failure modes.

Example (2026-05-02): dashboard "stale liveness" alarm chased a phantom BarScheduler-stopped bug for several rounds before someone asked "could it just be the weekend?" — the SMA strategy fires `on_tick()` once daily at 16:10 ET, and the 72h weekend gap exceeded a 26h threshold. Reading `main.py` first would have surfaced this immediately.

---

## Emergency protocol

If the bot is making unexpected live trades or the VPS is behaving incorrectly:

```bash
ssh chappy-vps
sudo systemctl stop tradebot
sudo journalctl -fu tradebot   # inspect what happened
```

For gateway issues:
```bash
sudo systemctl stop ibgateway
# Resolve, then:
sudo systemctl start ibgateway
sudo systemctl start tradebot
```

Do not push code changes during a live incident. Stabilise first, investigate after.

---

## Web research rule

If `WebFetch` returns 403 on the first attempt, go straight to `WebSearch` — do not retry the same domain. IBKR docs and most financial sites block direct fetches.
