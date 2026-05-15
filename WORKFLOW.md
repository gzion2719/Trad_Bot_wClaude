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

**TWS not running on your PC?** Broker tests (live_client fixture) will fail with ConnectionRefused. Use the CI-equivalent gate instead — it skips broker tests exactly as CI does:

```bash
GITHUB_ACTIONS=true make pre-push
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

## Multi-session UI feature slicing

When splitting a UI feature into multiple sessions, every session must ship at least one user-visible artifact — not just backend plumbing. "Backend foundation now, UI later" sessions feel invisible to a non-engineer user even when the work is correct: the dashboard looks identical after deploy and the session feels like it didn't happen.

If a session genuinely has no user-visible delta to ship, fold in the smallest UI shell — an empty tab labeled "Strategies — coming soon", a placeholder card with "0 fills yet", a status row that updates from a new endpoint. Anything beats a session that returns "no change" to the eye.

When a pre-impl reviewer (CR agent or second-opinion agent) flags this risk and the team overrides it, record the override + outcome in that session's CHATLOG bullet so the trade-off accrues evidence across sessions.

Example (2026-05-12, Dashboard Phase 5 Session 1): we shipped three new API endpoints + a single new "Strategy" column in Recent Fills. The endpoint surface was a real win (curl-verified, 30 tests). The second-opinion agent had warned: "an endpoint-only session feels invisible to a non-engineer user and may not feel like progress." We deferred the Strategies top-tab to Session 2 anyway, and after deploy the owner asked "where is the tab for each strategy?" — confirming the prediction. A 10-minute UI stub (empty tab populated from `/api/strategies`) would have made Session 1 visible.

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

## Worktree commit-handoff rule

When Claude's edits live in a worktree (`.claude/worktrees/<name>/`) and the user's shell is in the main checkout — which is the default Claude Code setup on this project — **Claude commits and pushes from the worktree itself** instead of giving the user a gate-first command block. The user runs only the steps that require their hands (click "Merge PR" in the browser, run `git pull && systemctl restart tradebot` on the VPS).

Why: the user's shell is PowerShell on Windows + the main checkout, which differs from Claude's Bash + worktree environment in two ways:
1. **Shell language.** PowerShell can't run bash heredoc (`<<'EOF'`), `$(cat <<...)`, or `cmd | git commit -F -` reliably for multi-line commit messages.
2. **Working directory.** Claude's file edits aren't in the user's `pwd`; the user must `cd` into the worktree first or git will say "nothing to commit, working tree clean" on whatever branch the main checkout last had.

Default flow when wrapping work in auto mode:
1. Claude runs `make pre-push` (or the equivalent) inside the worktree via Bash tool.
2. Claude runs `git add ... && git commit -F -` (Bash heredoc works here because Claude IS in bash) inside the worktree.
3. Claude runs `git push origin <worktree-branch>` inside the worktree.
4. Claude hands the user **only**: the PR compare URL(s), merge instruction, and the VPS deploy one-liner.

Multi-line commit messages: always use `git commit -F -` with a heredoc on Claude's side. **Never give the user a heredoc** — if for some reason the user must run the commit themselves (non-auto mode, or they explicitly ask to drive), prefer either (a) `git commit -m "single short title"` plus a follow-up `git commit --amend` once they've inspected, or (b) write the message to a tracked temp file with the Write tool and tell them `git commit -F .git/COMMIT_MSG.tmp`.

Example (2026-05-07): twice in one closing ritual, Claude handed the user a multi-line `git commit -m "$(cat <<'EOF' ... EOF)"` and assumed cwd was the worktree. First attempt failed with PowerShell parse errors; second attempt succeeded syntactically but ran in the main checkout on a stale branch and committed nothing. Both rounds were wasted; both were avoidable by Claude just running the commit itself in the worktree.

---

## Unbiased CR is mandatory after every production-code commit

After committing any production-code change (strategies, broker, risk, runtime, backtester), run an unbiased code review **before declaring the task done**. This is not optional. The user should not have to ask.

What counts as a mandatory CR trigger: any new file in `strategies/`, `broker/`, `risk/`, `runtime/`, or `backtester/`; any modification to `config/strategies.py` REGISTRY; any new `StrategyConfig` entry; **OR — independent of file path — any feature that took a pre-implementation CR also takes a post-implementation CR.** The two passes catch different classes of issue: pre-impl reviews the plan (logic, scope, missing tests); post-impl reviews the actual diff (drift from plan, off-by-one in the code, env-var leaks, redundant assertions). One does not substitute for the other.

What does NOT require a full CR: docs-only commits, CHATLOG updates, test-only changes that don't touch production paths.

Spawn the CR agent (or run an in-chat unbiased review) immediately after the commit that ships the feature. Report findings to the user before saying "done."

Example (2026-05-09): Phase B registered RSI2MR-SPY in REGISTRY, committed, and pushed — CR was skipped. User caught the omission ("did you do unbiased code review?"). The CR found 3 HIGH items (MS-A, MS-B, MS-C) that are now tracked in BACKLOG.

Example (2026-05-14): Profit-factor `+inf → null` fix touched `data/trade_log.py` — not on the file-path trigger list. Pre-impl CR ran and caught a CRITICAL ds-18 test miss. Post-impl CR was skipped on the reasoning "trigger didn't fire"; user asked, and the post-impl CR found two LOW items (redundant nan assertion; non-restorative env-var pop) that were fixed in the same branch. The fix added the "pre-impl ran → post-impl runs" trigger above.

---

## CR-to-fix transition rule

When a code review (`/ultrareview` or an in-chat unbiased review) identifies fixable findings, **do NOT auto-apply them**. The CR is one deliverable; the fix pass is a separate one that needs its own Step 7 self-critique. Specifically:

1. Present findings (✓ part of the CR).
2. Propose a fix scope as a Step 7 plan: list each fix, name files touched, flag scope creep candidates explicitly, identify smaller-increment options.
3. **Wait for explicit go on scope** — even if the user already said "yes apply fixes", treat that as authorization to plan, not authorization to code. A second "go" on the plan is required.
4. Only then edit code.

Step 7 in `SESSION_PROTOCOL.md` already covers production-code changes; this rule is its CR-pipeline corollary, written because CR fixes feel like rubber-stamp work but routinely touch core paths (this session: `OrderManager._handle_order_status`, `BaseStrategy.__init__`).

Example (2026-05-07): user said "yes apply B1+B2+tests" after a Phase-A CR; I jumped to code, expanded scope unilaterally to also include M4 + cosmetic test-helper changes, and edited 4 production-code files without restating the plan. The user flagged the procedure break ("you are not working according to procedure"). The fix pass was correct in outcome but should have been gated by a 30-second restated plan.

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

## "Verify before asking" rule

Don't ask the user procedural questions you can answer with a grep, a one-line check, or by reading a file. If the question is "is X deployed?", "did PR #N merge?", "is the new code on disk?" — run the check yourself first. Only ask the user for things they uniquely know (intent, preferences, real-world state the bot can't see). Asking the user for facts they have no way to verify is friction and reads as deflection.

Example (2026-05-11): after a VPS `git pull` that picked up two open PRs, I asked the user "did the MS-I PR get merged in the same pull?" — they correctly pushed back that they couldn't possibly know. The right move was `grep -n "capture skipped" /opt/tradebot/data/account_snapshot.py` first, then report the answer.

---

## IBKRClient method thread-safety rule

Any new method on `IBKRClient` that wraps an `ib_insync` sync wrapper which internally calls `_run()` (i.e. `qualifyContracts`, `accountSummary` on uncached first call, `reqCurrentTime`, `sleep`, anything that does `loop.run_until_complete()`) MUST auto-detect the calling thread and route via `asyncio.run_coroutine_threadsafe(coro, self._main_loop)` when invoked from a non-main thread. The auto-detect helper is `self._needs_threadsafe_route()`.

Rules:

1. **Do not introduce new `_threadsafe`-suffixed variants.** That opt-in pattern lets every new caller forget — the May 2026 PingPong zero-fills bug went latent for days because `get_market_price` and `qualify_contract` were never given the threadsafe treatment. Existing `get_account_summary_threadsafe` / `get_positions_threadsafe` are one-line aliases kept for back-compat only; delete after callers migrate.
2. **`_needs_threadsafe_route()` raises on cold-start, never silently falls through.** If called from a non-main thread before `connect()` captured the main loop (or after the loop stopped), it raises `RuntimeError` — the previous silent-fallback dropped daemon callers onto the broken sync path, which is exactly the bug class this rule exists to fix.
3. **Callers outside `IBKRClient` must not reach for `self._ib.sleep(...)`, `self._ib.qualifyContracts(...)`, or `self._ib.reqCurrentTime(...)` directly.** Go through `client.sleep(...)`, `client.qualify_contract(...)`, `client.is_alive()`. The grep tripwire test `test_ts07_no_direct_ib_sync_calls_outside_client` fails the build if a regression slips in — this is the regression that hid the bug for days because PingPong's broad `except` swallowed the resulting RuntimeError.
4. **Inside any `_async` coroutine on `IBKRClient`, every wait MUST be `await asyncio.sleep(...)`, never `self.ib.sleep(...)`.** The latter re-enters `loop.run_until_complete` from inside the loop — self-deadlock. Use `loop.time()` for deadlines, not `time.time()`.

Example (2026-05-15): PingPong's `on_tick` ran on its daemon scheduler thread and called `client.get_market_price` → `qualify_contract` → sync `ib.qualifyContracts` → `loop.run_until_complete(qualifyContractsAsync(...))` while the main loop was already running → `RuntimeWarning: coroutine 'IB.qualifyContractsAsync' was never awaited` → swallowed by PingPong's broad except. Zero fills for 5 days. The fix made auto-routing implicit across `IBKRClient`; the grep tripwire (this rule's point 3) prevents the regression class.

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

## Time-based exit test rule

Integration tests for time-bounded exits (time-stop, cooldown) **must assert the bar count at exit**, not merely that a fill occurred. Asserting fill presence only lets off-by-one errors in `_bars_held` or `_cooldown_remaining` go undetected — the test passes while the strategy holds positions one bar too long or re-enters one bar too early.

Minimum assertions for a time-stop test:
- A SELL fill exists.
- The SELL fill's `submitted_at` (or bar index derived from fill list position) falls at `entry_bar + TIME_STOP_BARS`, not `+ TIME_STOP_BARS + 1` or later.

For cooldown tests:
- The second BUY fill's bar index is ≥ `first_sell_bar + COOLDOWN_BARS + 1`.

Example (2026-05-09): `test_fi02_time_stop_produces_sell` only checked `len(sell_fills) >= 1` — the `_bars_held` off-by-one (increment after check instead of before) held positions 9 bars instead of 8. The test passed; the CR agent caught it as HIGH.

---

## Schema migration durability rule

When a `_load_state` (or any `_load_*` for a persisted file) detects an old schema version and rewrites in-memory fields to migration defaults, **immediately call the matching `_save_*` inside the same load call** so the new schema lands on disk before the next operation. Do not rely on a downstream save trigger (next ratchet, next fill, next shutdown) to durably commit the migration — those triggers can be hours or days away, and a crash in between will re-fire the migration warning every restart.

Audit checklist:
1. Is there an `if loaded_version < CURRENT_VERSION:` reset block in any `_load_*`?
2. Does that block end with a `self._save_*()` call (or equivalent fsync-then-rename helper)?
3. Add a test that writes a v(N-1) file, calls `_load_*`, then reads the file back and asserts the on-disk schema matches CURRENT_VERSION.

Example (2026-05-10): MS-B's RSI2-MR `_load_state` migrated v1 → v2 by resetting `strategy_peak_equity` and `circuit_breaker_until` in memory. The new `partial_fill_halt` field and `schema_version: 2` only landed on disk on the next save trigger — but with no peak advance and no fills scheduled, the file stayed v1 across the post-deploy restart. Caught by `cat /opt/tradebot/data/rsi2_mr_state.json` as part of standard VPS deploy verification, fixed by adding `self._save_state()` to the migration block plus `test_msb_17_v1_to_v2_migration_persists_eagerly`.

---

## "Pre-existing" deferral rule

Before deferring a code-review finding as "pre-existing — not introduced by this PR," answer this question explicitly: **does this PR make a previously stable invariant load-bearing for new code?** If yes, the finding belongs in this PR even if the underlying defect already existed.

Triggers that turn a pre-existing defect into in-scope:
- A new function reads a field that was previously only written.
- A new flag depends on integrity of state another flow can corrupt.
- A new computation builds on values that another code path can silently zero.

Example (2026-05-10): MS-B's `_get_strategy_attributed_equity` started reading `_position_shares` and `_entry_price` for the unrealized term. A partial-SELL bug (pre-existing in `on_fill(SELL)`) silently zeroed both fields. I deferred the partial-fill audit as "pre-existing → MS-K"; the user pushed back correctly because MS-B made those fields newly load-bearing. The fix (MS-K guard) shipped in the same PR.

---

## CR-finding-to-BACKLOG grounding rule

When a CR agent's finding proposes adding a new BACKLOG entry, **read the referenced source file before writing the entry description**. CR agents reason from the prompt you gave them; they do not independently open files. If your prompt contained an unverified claim ("X is silent", "Y is unhandled"), that claim survives into the CR finding, into the BACKLOG entry, and into the next session's "Next session:" planning — at no point did anyone actually check the code.

Cheap check, big payoff: a 5-second `Read` confirms the premise before a future session acts on it.

Example (2026-05-11): MS-C plan CR raised H2 — "VIX feed silent-failure gap is real" — and I added MS-C3 to BACKLOG describing it as "VIXFeed.get_latest_close() failures silently return None". Reading `data/vix_feed.py` later in the same session showed `_fire_stale_alert` already POSTs to ntfy on stale/absent cache. Cost: one extra chore branch to correct the description (commit `fc02173`). A pre-write `Read` would have caught it free.

---

## JS rate-limit gate rule

When adding a polling gate that references multiple fetch functions (e.g. `_onAcctTab ? [fetchAccount(), fetchEquity()] : []`), the comment **must name the specific endpoint(s) that are rate-limited**, not the functions. Gate comments that name functions imply all named functions are rate-limited — reviewers will not re-check each endpoint's backend definition.

Example (2026-05-06): `_onAcctTab` gate comment said "prevents fetchAccount / fetchEquity from consuming the rate limit" — but `/api/account` has no rate limit, only `/api/equity-history` does. The imprecise comment let `fetchAccount()` get swept into the gate, causing the KPI strip to show `—` on Mission Control until the user switched tabs.

---

## Pre-fixture wiring check rule

When verifying a feature against fixture or seed data — populating a DB, writing a temp file, setting an env var — **grep the code under test for the path or source it actually reads BEFORE staging the fixture**. Don't trust a prior CHATLOG observation like "X is empty" or "Y has zero rows" as authoritative; that observation may be correct on the surface while masking a wrong-file-read bug underneath.

The trigger isn't unusual data state — it's the moment you think "I'm about to populate file X so the feature shows data." Stop and confirm the feature actually consumes X. A 2-line grep beats discovering the mismatch after launching the verification, or worse, after deploy.

Example (2026-05-13): the 2026-05-12 CHATLOG noted "VPS `trades.db` has zero rows," which read as a known-empty state. While prepping local verification for Phase 5 S2, I was about to seed `trades.db` to populate the new Strategies tab. A grep of `main.py` first surfaced `TradeLog(db_path=Path("data/paper_trades.db"))` — the bot writes to `paper_trades.db`, but `dashboard/app.py` was reading the default `trades.db`. The "empty" observation was technically correct but masked a wrong-file-read bug. Fixing the dashboard wiring (one line) was bundled into S2's PR and surfaced real paper-account data on the next deploy.

---

## Invisible Unicode literal rule

When a string literal needs a non-printing or invisible Unicode character — a BOM (`U+FEFF`), zero-width space, non-breaking space, etc. — **always write the `\uXXXX` escape sequence, never the raw character**. A raw invisible char in source is undetectable on review, survives copy-paste silently, and an editor or `git` filter can strip or mangle it without anyone noticing. The escape form is self-documenting and greppable.

After writing such a literal, verify with a `repr()` check that the source contains the escape, not the raw codepoint:

```python
# in the file: ("\ufeff" + body)   ← correct, escape sequence
# NOT:         ("<raw BOM>" + body) ← invisible, will be flagged here
assert chr(0xFEFF) not in open("path.py", encoding="utf-8").read()
```

Example (2026-05-16): the CSV-export BOM prefix was typed as a literal `U+FEFF` character into `dashboard/app.py` and again into the test file. Both worked functionally but were invisible in the diff; correcting them to `"\ufeff"` cost two throwaway fix scripts. Writing the escape the first time is free.

---

## Describe-from-source rule

Before describing, recommending, or planning around any BACKLOG or ROADMAP item, **read that item's full entry in `docs/BACKLOG.md` or `docs/ROADMAP.md` \u2014 never paraphrase from the CLAUDE.md "Immediate next steps" / "Current state" summary.** Those CLAUDE.md sections are a stale-prone index: they are updated opportunistically and routinely lag a decision recorded in the detail file. A summary line is a pointer to the source, not the source.

The trigger is the moment you are about to tell the user \u2014 or yourself, in a Step 6 focus option \u2014 what an item *is* or whether it is *actionable*. One `Read` of the real entry is cheaper than contradicting a recorded decision out loud.

Example (2026-05-16): asked "what is MS-C2?", Claude described it from CLAUDE.md's summary ("design item \u2014 needs auto_adjust resolution") and offered it as a next-session option. The authoritative `docs/BACKLOG.md` entry said MS-C2 was **measurement-gated** \u2014 explicitly deferred until a 30-day yfinance-outage report runs on 2026-06-12, with "don't design or build before then." The user caught the contradiction. Reading the BACKLOG line first would have made the answer correct and removed a dead option from the focus list.

---

## Broker-state-authority rule

When a strategy (or any stateful component) tracks position / order state
alongside the broker's own view, the plan must answer ONE question
explicitly before any code: **when is the broker's view authoritative, and
when is it stale?** The classic stale window: an order is `Filled` (gone
from `get_open_orders`) but the position has not yet appeared in
`get_positions()` -- read in that gap, the broker looks flat when it isn't.

Every code path that reads broker state -- `on_start` reconcile, a
pending-timeout self-heal, an each-tick check -- must derive its behaviour
from that single stated answer, not re-reason about the race locally. If
two separate code reviews catch the same race in two different paths, the
invariant was never written down.

Example (2026-05-18, PingPongTest): the pre-impl CR caught a duplicate-order
race in a "pure broker-reconcile, no flags" design; a pending flag was added
to fix it. The post-impl CR then caught the *same* race reappear in the
pending-timeout path, which force-cleared the flag and re-placed on a
possibly-stale flat snapshot. Both are the same defect -- "the broker can
look flat right after a fill." Stating that invariant once in the plan, and
ruling that no path places an order on a flat *snapshot* (only on a
positively-confirmed position), would have closed both at plan time.

---

## Pending-flag pattern CR checklist

When a strategy uses an `_order_pending` (or any analogous "in-flight order"
boolean), the pre-impl and post-impl CRs MUST trace this exact sequence,
explicitly, and quote the result:

1. `on_tick` calls `safe_place_order(request, price)`.
2. Inside `OrderManager.place_order`, `self._client.sleep(0.5)` yields to
   the IB asyncio event loop.
3. For a fast-filling MKT order on a liquid symbol, `orderStatus=Filled`
   fires DURING that 0.5s sleep.
4. The fill event walks `_on_fill_callbacks` synchronously and calls the
   strategy's `on_fill`, which runs `_clear_pending()` and updates
   `_in_position`.
5. `place_order` returns to `on_tick`.

The CR question every time: **"after step 5, what does `on_tick` do?"** If
the answer is "unconditionally re-set `_order_pending=True`" or "stamp
`_pending_order_id` with the now-terminal id," that's a BLOCKING bug — the
strategy will be silent for at least one full timeout cycle and possibly
indefinitely (especially if the strategy_name late-write also drops the
callback — see below).

Companion invariant on the OM side: any per-order metadata that an
`on_fill` callback might READ (e.g. `_strategy_name_by_order_id`) must be
WRITTEN before `_client.sleep` is called, not after. The fill event can
read it during the sleep; a late write means the fill arrives missing the
metadata and downstream filters drop it. (2026-05-15: `BaseStrategy.
_dispatch_on_fill` filtered out a `strategy_name=None` fill for exactly
this reason.)

Locked by `tests/test_test_pingpong.py::test_pp24,test_pp25` (strategy
side) and `tests/test_multi_strategy_runner.py::test_ms12_strategy_name_set_before_sleep_so_fast_fill_carries_tag`
(OM side).
