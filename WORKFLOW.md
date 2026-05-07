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

## Stacked PR rule (shared docs files)

When opening **multiple feature branches in one session that all touch the same docs file** (most often `TODO.md`'s issue table or `CLAUDE.md`'s current-state header), expect a merge conflict on every PR after the first one lands on `develop`. Pick one of the two patterns up front:

1. **Chain the branches** — base PR 2 on PR 1's branch, PR 3 on PR 2's, etc. Conflicts auto-resolve as you go.
2. **Omit the docs edits from feature branches** — keep each feature PR scoped to code only, then open one trailing `chore/cr-cycle-tracker` PR that ticks every CR box at once, after the feature PRs merge.

Example (2026-05-03): four CR fixes (CR-04/05/08/09) each updated `TODO.md`'s issue row independently from `main`. Each merge after the first re-introduced a `<<<<<<<` block that forced a manual web-editor resolution or a force-pushed rebase. Picking pattern 1 or 2 at branch-creation time would have avoided three rebase rounds.

---

## Secret-redaction rule

When writing CHATLOG bullets, commit messages, or comments that describe the removal of a secret or sensitive literal, **never quote the actual literal** — write `<account-id>`, `<token>`, or `[redacted]` instead. Quoting the real value in the description re-introduces exactly the leak the redaction was meant to close, and the CI grep gate will catch it.

Example (2026-05-03): a CHATLOG entry for CR-11 described "redacted `<the actual account ID>` account-ID literal" — the exact phrase (with the real ID inline) caused the `DUE[0-9]{6,9}` grep gate to fail on the develop→main PR. The fix was to use `<account-id>` in both the entry and this example.

---

## CI debugging — prefer CLI to actions

When a third-party GitHub Action fails with a permissions / token error, switch to invoking the same tool via its CLI instead of fighting `permissions:` blocks. Most security/lint actions only add value (a PR comment, an annotation) on top of running their CLI — and that added value isn't worth a debugging round if the CLI alone catches the same issues that pre-push already runs.

Example (2026-05-03): `gitleaks-action@v2` failed with HTTP 403 on `pulls/{n}/commits` because the default `GITHUB_TOKEN` lacked `pull_requests:read`. Adding the workflow-level `permissions:` block didn't unblock it. Replacing the action with a one-line `curl + tar + gitleaks detect --no-git` step matched the local pre-push gate exactly and went green on the next run.

---

## Lock-reentrancy audit rule

When a previously **stateless `@staticmethod`** is converted to an instance method that touches `self._lock` (or any `threading.Lock`), grep every call site to confirm none of them already hold the lock. Python's default `threading.Lock` is non-reentrant — a recursive acquire deadlocks silently, and the symptom (pytest hangs partway through, `pytest -x` never reaches the failing assertion) is hard to read.

```bash
grep -n "with self._lock" broker/order_manager.py    # callers that hold the lock
grep -n "self\._method_name(" broker/order_manager.py # callers of the converted method
```

If a caller already holds the lock, either: (a) drop the inner acquire and rely on GIL-safe primitives (`dict.get`, `list` append) for the read, or (b) switch the lock to `threading.RLock` (intentionally — note in code).

Example (2026-05-07): `OrderManager._fill_to_result` was converted from `@staticmethod` to instance method to look up `strategy_name`. The new `with self._lock:` deadlocked `reconcile_fills`, which already wraps the call in `with self._lock:`. Pytest hung on `test_fr02_missed_fill_fires_callback_with_correct_fields` with no traceback. Fix: dropped the inner `with self._lock:`; `dict.get()` under the GIL is safe for this read.

---

## Debugging discipline

Before hypothesizing failure modes for a "stopped" or "stale" symptom, read the producer code to confirm the **expected** cadence. Most "X stopped firing" investigations are actually "X is firing on the cadence I forgot it had." Check expected behavior first, then look for failure modes.

Example (2026-05-02): dashboard "stale liveness" alarm chased a phantom BarScheduler-stopped bug for several rounds before someone asked "could it just be the weekend?" — the SMA strategy fires `on_tick()` once daily at 16:10 ET, and the 72h weekend gap exceeded a 26h threshold. Reading `main.py` first would have surfaced this immediately.

---

## ib_insync sync-vs-async rule (inside threadsafe coroutines)

When wrapping ib_insync calls in `asyncio.run_coroutine_threadsafe`, every call inside the coroutine MUST use the `*Async` variant. Sync ib_insync wrappers (e.g. `reqAllOpenOrders`, `accountSummary`, `qualifyContracts`) internally call `loop.run_until_complete()` via `IB._run()`. Inside an awaiting coroutine the loop is already running, so `_run()` raises `RuntimeError("This event loop is already running")` — exactly the failure mode the threadsafe routing was meant to prevent.

Audit checklist when reviewing/CR'ing any `run_coroutine_threadsafe` patch:
1. List every ib_insync call inside the inner coroutine (`async def _do_X` or similar).
2. For each, verify it ends in `Async` OR is a pure attribute read (`openTrades`, `wrapper.accounts`, `portfolio()` — these don't call `_run`).
3. Sync calls inside the coroutine = latent bug; will fire the next time the main loop is busy when the daemon thread schedules.

Example (2026-05-07): B-09 v1 (the May 6 sync() fix) routed correctly via `run_coroutine_threadsafe` but the inner `_do_sync()` called sync `reqAllOpenOrders()` — every nightly AutoRestartTime triggered the systemd-restart cascade. B-10 fix: `await reqAllOpenOrdersAsync()`.

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

---

## Test assertion rule

Before writing a test that asserts a response body field (e.g. `r.json().get("status") == "ok"`), read the endpoint's `return` statement first. Guessing field names costs a test failure that requires a re-run — reading the return takes 5 seconds.

**Import-binding patch rule:** When a test patches a module-level variable (e.g. `IB_PORT`, `IB_HOST`), read the import chain in the module under test first. `from config.settings import IB_PORT` in `config/validator.py` binds `IB_PORT` into `config.validator`'s namespace at import time. Patching `config.settings.IB_PORT` afterwards has no effect on `config.validator.IB_PORT`. Always patch the **consuming module's namespace** (`config.validator.IB_PORT = ...`), not the source module.

Example (2026-05-03): `test_cfg02` set `config.settings.IB_PORT = 9999` then called `validate_config()` — the validator still saw the original value because it had already bound its own reference at import time. Patching `config.validator.IB_PORT` directly fixed it.

---

## API endpoint verification (frontend → backend)

**Before writing `fetch("/api/X")` in any JS file, grep for the route definition** in the FastAPI app:

```bash
grep -n "@app.\(get\|post\|put\|delete\).*\"/api/X\"" dashboard/app.py
```

URL drift is silent and catastrophic when paired with `.catch(() => {})`. The fetch returns 404, the catch swallows it, and the side-effect (releasing a lock, logging out, etc.) never happens — but the UI looks fine. Verifying takes 5 seconds; the regression takes hours to diagnose.

Example (2026-05-04): `fetch("/api/console/lock/release", ...).catch(() => {})` 404'd on every modal close because the real route is `/api/console/release` — found by independent code review only after multiple deploys. A pre-write grep would have caught the typo immediately.

---

## JS rate-limit gate rule

When adding a polling gate that references multiple fetch functions (e.g. `_onAcctTab ? [fetchAccount(), fetchEquity()] : []`), the comment **must name the specific endpoint(s) that are rate-limited**, not the functions. Gate comments that name functions imply all named functions are rate-limited — reviewers will not re-check each endpoint's backend definition.

Example (2026-05-06): `_onAcctTab` gate comment said "prevents fetchAccount / fetchEquity from consuming the rate limit" — but `/api/account` has no rate limit, only `/api/equity-history` does. The imprecise comment let `fetchAccount()` get swept into the gate, causing the KPI strip to show `—` on Mission Control until the user switched tabs.
