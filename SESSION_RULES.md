# Session Protocol — Session-Wide Rules

> **Split file note (2026-05-21):** This file is one of three split from the former `SESSION_PROTOCOL.md`. Sibling files:
> - Opening Ritual → `OPEN_SESSION_PROTOCOL.md` (loaded on first message)
> - Closing Ritual → `CLOSE_SESSION_PROTOCOL.md` (loaded on farewell signal)
>
> All historical cross-references like "see `SESSION_PROTOCOL.md` → Rule X" resolve via the navigation stub at `SESSION_PROTOCOL.md`.

> This file holds rules that fire throughout a session, not lifecycle-bound. It is **not** read at orientation — it loads just-in-time when a Trigger Guide entry fires (see the bottom of `OPEN_SESSION_PROTOCOL.md`):
> - **Additional rules** (language, build cadence, uncertainty, risk, scope creep)
> - **Rules 10–13** (context-exhaustion early-close, ADR critic-pass, subagent absence-verify, acceptance-signal verify)
> - **Hygiene Rules 1–4** (CHATLOG archival every 10, BACKLOG review every 5, scope-creep capture, sandbox `git --no-optional-locks`)
> - **Rule 5** (Pre-push verification — sandbox checks before handoff, with sub-rules)
> - **Rule 6** (Pre-push gate — manual `make pre-push`)
> - **Rules 7–9** (C-extension coverage, code-writing pipeline, script logging init)
> - **TradeBot-Specific Engineering Rules** (24 rules migrated from `WORKFLOW.md`)
> - File map (bottom)

---

## Additional rules for the chat as a whole

- **Language:** Hebrew or English in → English out. Always. No Hebrew in Claude's output, ever — including the closing-ritual recap.
- **Build cadence:** after every meaningful code change, suggest a git commit with a message.
- **Uncertainty:** if you don't know something about the user's system, market, or preferences — ASK. Do not guess.
- **Risk rules:** the `RiskManager` + `PositionSizer` sections in `docs/REFERENCE.md` (RiskManager `plan_trade`, the 2% rule, the 1:3 R/R rule) are law. Never write code that violates them. If the user asks for something that contradicts them, stop and flag it. (TradeBot has no separate `docs/RISK_MANAGEMENT.md` — the risk law lives in `docs/REFERENCE.md` and `risk/risk_manager.py`.)
- **Scope creep guard:** if the user suggests adding something outside the current phase's scope in `docs/ROADMAP.md`, note it, write it into `docs/BACKLOG.md`, and redirect to the current focus.

- **Rule 10 — Context-exhaustion early-close.** When the user explicitly flags context length ("בקונטקסט", "context is getting long", "we're running out of context", or equivalent), Claude MUST: (1) STOP immediately — no new tool calls, no new work items, no new suggestions; (2) run the full closing ritual as the very next action; (3) only after CHATLOG is written and commit commands are given does the session end. Continuing new work after a context warning is a protocol violation. **Trigger:** any explicit context-length warning from the user.

- **Rule 11 — Unbiased review is automatic, not user-triggered.** After drafting any ADR (or any plan that ships production code), before writing "ready for approval," "ready to implement," or any equivalent handoff phrase, Claude MUST run an adversarial review — preferably the `deep-review` skill (for code) or the `review-loop` skill (for plans), or a labeled in-chat `## Unbiased Review — Critic Mode` block. The review must be substantive and adversarial — not a summary of what was written. It fires even if the user does not ask for it. See also the "Unbiased CR is mandatory" engineering rule below for production-code commits. **Trigger:** any ADR draft or production-code plan that reaches a "handoff" sentence without a preceding critic-mode review.

- **Rule 12 — Subagent absence-claim verification.** When a subagent's adversarial review finding asserts that a code path is never called, never read, never imported, or otherwise unused in the project, the main thread MUST run an independent `Grep` across the cited directories before promoting the finding to Confirmed in any deliverable. Subagents read excerpts and can miss adjacent call sites; absence-evidence is fragile and load-bearing for Critical/High findings. **Trigger:** any subagent finding of the form "X is never called/read/imported/used."

   **Parallel-batch sub-rule.** When a single review produces multiple absence-claims to verify, batch the verification commands into a single parallel tool-call block (one assistant message, multiple `Grep`/`Read` calls), not serially. The dependency graph is flat — serial execution is pure round-trip waste. **Trigger:** a Rule 12 sweep with ≥3 absence-claims → one message with all verifications in parallel.

- **Rule 13 — Acceptance-signal verification before `✅`.** Before any `✅ Done` tick on a status table (e.g. `docs/ROADMAP.md` phase checkboxes, `docs/BACKLOG.md` Standing Checks) or any "Phase X DONE" claim in a CHATLOG entry, the canonical acceptance signal for that phase MUST be independently verified. "Canonical acceptance signal" = the explicit log line, metric, file presence, or output the project designates as proof-of-done — e.g. `Connected | account=<id>` in the bot log for a connection phase, `port 4001 listening` for the gateway, a green `pytest tests/ -m "not market"` run for "tests pass," `data/<strategy>_state.json` showing `schema_version: 2` for a migration. **Trigger:** any `✅` / "DONE" claim about a phase or milestone → grep / status-check / smoke-test the named signal in the actual artifact BEFORE writing the tick.

---

## Recurring Hygiene Rituals (Claude-owned)

Claude owns these — they happen automatically during the opening ritual without the user asking. The session counter is the count of dated entries in `CHATLOG.md`.

### Rule 1: CHATLOG archival (every 10 sessions)

When the count of dated entries in `CHATLOG.md` reaches a multiple of 10, during opening ritual Step 4:

1. Read all entries in `CHATLOG.md`.
2. Keep the **most recent 5 entries** in place — they're the active context window.
3. For older entries, decide per-entry whether to archive or keep:
   - **Archive (move to `docs/CHATLOG_ARCHIVE.md`, newest-first)** if the entry is routine work (built X, ran tests, all green, committed).
   - **Keep in `CHATLOG.md`** if the entry contains a decision, a non-obvious learning, a gotcha future Claude needs, or an architectural choice. When in doubt, KEEP.
4. If `docs/CHATLOG_ARCHIVE.md` doesn't exist, create it with a 2-line header.
5. Show the user the diff before committing — list which entries moved and why, in 1 line each.

**Manual trigger sub-rule.** Rule 1 also fires on user request — any message asking about CHATLOG hygiene, archival cadence, or whether stale entries should be moved triggers the same per-entry decide-then-show flow above, regardless of the count. **Trigger:** any user-initiated query about CHATLOG curation.

### Rule 2: Backlog review (every 5 sessions)

When the count of dated entries in `CHATLOG.md` is divisible by 5, during opening ritual Step 4 (just before Step 6):

1. Read `docs/BACKLOG.md`.
2. Pick 1–2 items ripe for promotion based on: (a) current ROADMAP phase is light, (b) the item complements upcoming work, or (c) the item has sat in backlog for 3+ reviews.
3. Surface them as one of the Step 6 `AskUserQuestion` options. Don't silently promote — the user decides.

### Rule 3: Backlog scope-creep capture (every session, on demand)

Whenever the user suggests something outside current ROADMAP scope, Claude:
1. Notes it briefly back to the user.
2. Adds it to `docs/BACKLOG.md` under the right category with a 1-line description and an effort estimate (S/M/L).
3. Returns to the current focus.

### Rule 4: Sandbox git commands MUST use `--no-optional-locks`

**Why:** the sandbox can *create* files in `.git/` but cannot *unlink* them. A plain `git status`/`git log` takes an opportunistic index lock, fails to remove it, and leaves `.git/index.lock` behind — which then blocks SourceTree/IDE git until manually `rm -f`'d.

**Rule:** every git read from the sandbox MUST be invoked as `git --no-optional-locks <subcommand>` (status, log, diff, show, branch, rev-parse, anything). The flag is harmless when not needed.

**Writes** (`git add`, `commit`, `push`) still cannot run from the sandbox at all — they need real `.git/` write access and run from the user's Terminal/IDE, or from the worktree per the worktree-handoff rule below.

---

### Rule 5: Pre-push verification (run in the sandbox before handing off)

**Why:** every CI cycle is ~25 sec + a chat round-trip; sandbox checks are free. The pattern to avoid is "trust beat verify" when verification was free.

**Rule:** before declaring code ready for the user to commit + push, run these in the sandbox on every changed file:

1. **`python -m black --check <file>`** — formatting. Apply black's diff if it disagrees; don't ask.
2. **`python -m ruff check <file>`** — lint. Apply auto-fixes; manually patch the rest.
3. **`python -m mypy <file> --ignore-missing-imports`** — types. (TradeBot's gate uses `--ignore-missing-imports --exclude tests/`, NOT `--strict`.)

If the sandbox can't run a check (Python-version mismatch, missing dep), say so explicitly and fall back to `py_compile` + AST + manual review. Don't quietly skip. Note: TradeBot has **no `.venv`** yet (Sprint 5.2) — use the system `python -m ...`, not `.venv/bin/python`. Coverage (`pytest-cov`) is installed by `make install-dev` but the gate does **not** enforce `--cov`.

**Generic sub-rules** (apply to any Python project):

- **Immediate black after each edit.** After every `Edit`/`Write` on a `.py` file, run `python -m black --fast <file>` in the same message. "Before declaring code ready" creates a batching trap. **Trigger:** every `.py` `Edit`/`Write`.
- **Trailing-whitespace grep on text edits.** Before declaring any `Edit` to a `.md`/`.yaml`/non-`.py` file done, run `grep -n " $" <file>` and remove trailing whitespace. **Trigger:** any non-`.py` `Edit`/`Write`.
- **Sandbox ruff pre-handoff sweep.** When a session touches 4+ Python files, run `python -m ruff check <all-edited-files>` after all edits. Apply `--fix` first. **Trigger:** any session with 4+ `.py` edits.
- **`zip()` strict parameter at write time.** Every `zip()` call MUST include explicit `strict=True`/`strict=False`. B905 is 100% deterministic. **Trigger:** any `zip(` written/edited.
- **Nested-`with` SIM117 flatten at write time.** When an outer `with` block's body is only an inner `with`, flatten to `with A, B:` immediately. **Trigger:** any such nested pair.
- **I001 repair: always `ruff --fix` first.** Any I001 (unsorted imports) → `ruff check --fix <file>` immediately; never manually re-order. **Trigger:** any I001 error.
- **Black `--diff` first diagnostic.** When the gate fails citing black on a file, FIRST run `python -m black --diff <file>` — don't read and guess. **Trigger:** any gate failure citing black.
- **Project-internal type construction grep.** Before constructing any project-defined class in a test file (or accessing an attribute on a returned instance), grep/read its `__init__`/dataclass field list to verify exact kwarg/field names. Memory is not a substitute. **Trigger:** any `SomeClass(...)` or `returned_val.field` in a test where `SomeClass` is project-defined.
- **`__post_init__` validator scan.** After grepping a class's field list, read its `__post_init__` for value-range validators; use "just-valid" defaults in test helpers. **Trigger:** any project-defined class with a constructor used in a test.
- **Third-party-library source-read.** Before calling a method on a newly-added or being-patched third-party lib (`ib_insync`, `yfinance`, `exchange-calendars`, etc.), inspect the *installed* source for the exact signature. Never guess kwarg names from memory. **Trigger:** any first call or compatibility patch against a non-stdlib package.
- **Sandbox python-version stub.** Before a smoke battery importing from project modules, run `python --version`; if < 3.12 and a module imports `from datetime import UTC`, use inline stubs instead of real imports. **Trigger:** any sandbox smoke battery for a project module.
- **PEP-563 unquoted-annotation check.** If a file opens with `from __future__ import annotations`, NEVER quote annotations (ruff UP037). **Trigger:** before writing any `param: "Type"` annotation.
- **Mock call-count sweep.** When adding a new `.publish()`/`.send()`/mock-instrumented call to an already-tested method, grep the test file for `assert_called_once_with`/`call_count` on that mock and update in the same commit. **Trigger:** any new call to a mock-instrumented object.
- **Production-call-site kwarg sweep.** When adding a kwarg to an existing class `__init__`, grep ALL call sites: `grep -rn "ClassName(" --include="*.py" .` (incl. `scripts/`). **Trigger:** any new optional kwarg on an existing `__init__`.
- **Config-value grep sweep.** When `config/settings.py` (or any config the test suite reads) renames/replaces a string value, `grep -rn "<old_value>" tests/` before declaring done. **Trigger:** any config string rename.
- **Fix-known, stop investigating.** Once a failure is diagnosed and a correct fix is in hand, apply it — don't trace secondary symptoms consistent with the root cause. **Trigger:** as soon as you can state "the fix is X."

> **Not imported from the sibling project:** sub-rules tied to YuTom-only code surfaces (`IndicatorSnapshot` schema sweeps, `EVENT_PAYLOAD_TYPES` registry, bus-citizen filter alignment, `event_topics.py` publish-site audits, TalTal/TA-Lib, mplfinance) were intentionally omitted — TradeBot has none of those modules. Reintroduce the relevant pattern if/when such a surface is built.

---

### Rule 6: Pre-push gate (`make pre-push`)

**Why:** Rule 5 verifies in the sandbox, but the sandbox can't always run the full pytest suite (Python-version mismatch, no TWS). CI is the source of truth, but every CI cycle costs ~25 sec + a round-trip. `make pre-push` runs the same checks locally before the push leaves the machine.

**Rule:** every push runs `make pre-push` first — **NOT the individual tools piecemeal.** `make pre-push` runs the gitleaks secret scan + the account-ID grep, neither of which is invoked when you run `ruff check . && black --check . && mypy ... && pytest ...` by hand. Running tools individually is a Rule 6 violation even if all four piecemeal checks pass. **Trigger:** any session that runs ruff/black/mypy/pytest individually before a push (codified 2026-05-22 after a `DU`+8-zeros test fixture matched the account-ID regex and broke CI on a PR that had passed piecemeal checks locally).

It is a verbatim mirror of `.github/workflows/ci.yml`:

```bash
cd "C:\Users\galzi\OneDrive - Afiki-C\Afikim\TradeBot"
make pre-push
```

`make pre-push` runs, in order:
1. `ruff check .`
2. `black --check .`
3. `mypy . --ignore-missing-imports --exclude 'tests/'`
4. `pytest tests/ -m "not market"`
5. `gitleaks detect --redact --no-git -s .`
6. account-ID leak grep (`DUE[0-9]{6,9}` across tracked text files → fails if found)

**TWS not running on your PC?** Broker tests (live_client fixture) fail with ConnectionRefused. Use the CI-equivalent gate, which skips broker tests exactly as CI does via `pytest.mark.skipif(IS_CI, ...)` (`tests/conftest.py`):

```bash
GITHUB_ACTIONS=true make pre-push
```

**No auto pre-commit hook yet.** TradeBot has no `.pre-commit-config.yaml`, so the gate is **manual** — run it before every push. (Auto-enforcement via a pre-push git hook is a future item, Sprint 5.2-adjacent.)

**Lead-with-the-gate (every "ready to commit" handoff, not only closing).** Any message that declares code ready MUST have the gate-first ```bash``` block as the **first content block** — before any prose summary, file list, or results recap. Concrete shape every time: `cd ...` → `make pre-push` → (only if green) `git add` → `git commit -m "..."` → `git push`. Mechanical pre-send self-check: re-read the draft's first 3 lines; if they don't contain a ```bash``` block starting with `cd ...` + `make pre-push`, prepend it before sending.

**Multi-file slice commit ordering.** When a slice spans files that import each other, commit the dependency (imported file) before the dependent (importing file). **Trigger:** any slice with ≥2 files where one imports another.

---

### Rule 7 — C-Extension / optional-dependency coverage strategy

When a new module imports a C-extension or optional dependency that won't be present in the pre-push test environment, the test plan MUST specify a stub/mock strategy BEFORE writing the first line of the module.

1. **Soft-import:** `try: import lib except ImportError: lib = None`
2. **Raise at instantiation**, not import: `if lib is None: raise ImportError(...)` in `__init__`
3. **Inject a `sys.modules` stub** in the test file before the first import of the module.

**`# type: ignore[code]` verification:** read the exact error code from mypy's output before writing the comment; never guess. **Trigger:** any new module importing a C-extension or optional dep.

---

### Rule 8 — Code Writing Protocol (spec → review → code → review → QA → deploy)

Before writing any code, execute this pipeline in order. For trivial changes, steps 1–2 reduce to a single sentence — the rule still fires.

1. **Full spec** — bullet list: what it does, inputs/outputs, edge cases, files touched, tests needed. For ADR-worthy work, the ADR IS the spec; start at step 2.
2. **Critic-mode spec review** — `## Unbiased Review — Critic Mode`. Try to break the spec. Surface gaps, ambiguities, missing edge cases, rule violations. Amend.
3. **Improve spec.**
4. **Write code per spec.** Don't invent scope.
5. **Critic-mode code review** — read adversarially: wrong names, missing error handling, type mismatches, Rule 5 sub-rules not applied, missing tests.
6. **Fix.**
7. **Critic-mode QA** — run Rule 5 sandbox checks. Fix failures.
8. **Fix.**
9. **Deploy** — Rule 6: `make pre-push` → `git add` → `git commit` → `git push`.

Review steps are adversarial, not confirmatory. Labeling the mode switch is mandatory.

**Edit-before-Read sub-rule.** Before the first `Edit` on any file, the `Read` tool must have been called on it. Viewing via bash (`cat`/`tail`/`grep`) does NOT satisfy the Edit tool's requirement. **Trigger:** before any `Edit`.

**Measure-before-gate sub-rule.** Before setting/updating any numeric gate value (threshold, window, `fail_under`), run the measurement first to confirm the baseline. **Trigger:** any numeric-threshold edit.

**Trigger:** before writing any code (new file, spec-to-code transition).

---

### Rule 9 — Script logging initialisation

Any script under `scripts/` that uses the project logger must initialise logging (call `setup_logging()` / `init_logging(console=True)`) as the first line of `main()` — before any log call, before loading config. Without it, log output is silently discarded. **Trigger:** any new/modified `scripts/*.py` that imports the logger → verify logging init at the top of `main()`.

---

## TradeBot-Specific Engineering Rules

> Migrated verbatim from `WORKFLOW.md` (2026-05-21). These are hard-won, TradeBot-specific lessons. They load just-in-time via the Trigger Guide in `OPEN_SESSION_PROTOCOL.md`.

### CI test-runner guard rule

When adding `if not IS_CI:` guards to a test file, always verify with a grep **after** all edits that no `get_client()` (or equivalent broker call) remains in any section assumed to be broker-free:

```bash
grep -n "get_client()" tests/run_tests.py
```

Cross-reference every line number against the section it falls in. A section header saying "no connection needed for most" is not sufficient — check the actual call sites. Example (2026-05-02): Section 11 header said "no connection needed for most" so its call blocks weren't guarded; 14 RM integration tests called `get_client()` inside function bodies and CI failed again.

### Multi-session UI feature slicing

When splitting a UI feature into multiple sessions, every session must ship at least one user-visible artifact — not just backend plumbing. "Backend foundation now, UI later" sessions feel invisible to a non-engineer user even when the work is correct.

If a session genuinely has no user-visible delta, fold in the smallest UI shell — an empty tab "Strategies — coming soon", a placeholder card "0 fills yet", a status row from a new endpoint. When a pre-impl reviewer flags this risk and the team overrides it, record the override + outcome in that session's CHATLOG bullet. Example (2026-05-12, Dashboard Phase 5 S1): shipped three endpoints + one new column; the second-opinion agent warned an endpoint-only session feels invisible; we deferred the tab and the owner asked "where is the tab for each strategy?" — confirming the prediction.

### Stacked PR rule (shared docs files)

When opening multiple feature branches in one session that all touch the same docs file (often `TODO.md`'s issue table or `CLAUDE.md`'s current-state header), expect a merge conflict on every PR after the first lands on `develop`. Pick one up front:
1. **Chain the branches** — base PR 2 on PR 1's branch, etc. Conflicts auto-resolve.
2. **Omit the docs edits from feature branches** — keep each feature PR code-only, then one trailing `chore/cr-cycle-tracker` PR ticks every CR box at once.

Example (2026-05-03): four CR fixes each updated `TODO.md`'s issue row independently from `main`; each merge after the first re-introduced a conflict block.

### Secret-redaction rule

When writing CHATLOG bullets, commit messages, or comments that describe removal of a secret/sensitive literal, **never quote the actual literal** — write `<account-id>`, `<token>`, or `[redacted]`. Quoting the real value re-introduces the leak; the CI `DUE[0-9]{6,9}` grep gate will catch it. Example (2026-05-03): a CHATLOG entry quoted the real account ID inline and failed the develop→main PR gate.

### CI debugging — prefer CLI to actions

When a third-party GitHub Action fails with a permissions/token error, switch to invoking the same tool via its CLI instead of fighting `permissions:` blocks. Example (2026-05-03): `gitleaks-action@v2` failed HTTP 403 on `pulls/{n}/commits`; replacing it with a one-line `curl + tar + gitleaks detect --no-git` step matched the local gate and went green.

### Worktree commit-handoff rule

When Claude's edits live in a worktree (`.claude/worktrees/<name>/`) and the user's shell is in the main checkout — the default Claude Code setup on this project — **Claude commits and pushes from the worktree itself** instead of giving a gate-first command block. The user runs only the steps that require their hands (click "Merge PR", run the VPS deploy).

Why: the user's shell is PowerShell on Windows + the main checkout, which differs from Claude's Bash + worktree environment in two ways: (1) PowerShell can't run bash heredoc reliably for multi-line commit messages; (2) Claude's edits aren't in the user's `pwd`.

Default flow in auto mode: Claude runs `make pre-push` in the worktree → `git add ... && git commit -F -` (heredoc on Claude's side) → `git push origin <worktree-branch>` → hands the user only the PR compare URL(s), merge instruction, and VPS deploy one-liner. **Never give the user a heredoc.** Example (2026-05-07): twice in one closing ritual a multi-line `git commit -m "$(cat <<'EOF'...)"` was handed to the user; first failed with PowerShell parse errors, second committed nothing on a stale branch.

### Unbiased CR is mandatory after every production-code commit

After committing any production-code change (strategies, broker, risk, runtime, backtester), run an unbiased code review (the `deep-review` skill, the `review-loop` skill, or an in-chat unbiased review) **before declaring the task done**. Not optional; the user should not have to ask.

Mandatory CR triggers: any new file in `strategies/`, `broker/`, `risk/`, `runtime/`, `backtester/`; any modification to `config/strategies.py` REGISTRY; any new `StrategyConfig` entry; **OR — independent of file path — any feature that took a pre-implementation CR also takes a post-implementation CR.** The two passes catch different classes of issue: pre-impl reviews the plan; post-impl reviews the diff. NOT required: docs-only commits, CHATLOG updates, test-only changes not touching production paths. Example (2026-05-09): Phase B registered RSI2MR-SPY, committed, pushed — CR skipped; user caught it; CR found 3 HIGH items.

### CR-to-fix transition rule

When a CR identifies fixable findings, **do NOT auto-apply them**. The CR is one deliverable; the fix pass is separate and needs its own Step 7 self-critique:
1. Present findings.
2. Propose a fix scope as a Step 7 plan: each fix, files touched, scope-creep candidates flagged, smaller-increment options.
3. **Wait for explicit go on scope** — even if the user already said "yes apply fixes", treat that as authorization to plan, not to code. A second "go" on the plan is required.
4. Only then edit code.

Example (2026-05-07): user said "yes apply B1+B2+tests"; Claude jumped to code, expanded scope to M4 + cosmetic changes, edited 4 production files without restating the plan; user flagged the procedure break.

### Lock-reentrancy audit rule

When a previously stateless `@staticmethod` is converted to an instance method that touches `self._lock` (or any `threading.Lock`), grep every call site to confirm none already hold the lock. Python's default `threading.Lock` is non-reentrant — a recursive acquire deadlocks silently (symptom: pytest hangs partway with no traceback).

```bash
grep -n "with self._lock" broker/order_manager.py
grep -n "self\._method_name(" broker/order_manager.py
```

If a caller already holds the lock: either drop the inner acquire (rely on GIL-safe `dict.get`/`list` append) or switch to `threading.RLock` (intentionally, noted in code). Example (2026-05-07): `OrderManager._fill_to_result` converted to instance method deadlocked `reconcile_fills`.

### Debugging discipline

Before hypothesizing failure modes for a "stopped"/"stale" symptom, read the producer code to confirm the **expected** cadence. Most "X stopped firing" investigations are "X is firing on the cadence I forgot it had." Example (2026-05-02): a dashboard "stale liveness" alarm chased a phantom BarScheduler-stopped bug for several rounds before someone asked "could it just be the weekend?" — the SMA strategy fires `on_tick()` once daily at 16:10 ET; the 72h weekend gap exceeded the 26h threshold.

### "Verify before asking" rule

Don't ask the user procedural questions you can answer with a grep, a one-line check, or a file read. If the question is "is X deployed?", "did PR #N merge?", "is the new code on disk?" — run the check yourself first. Only ask the user for things they uniquely know (intent, preferences, real-world state the bot can't see). Example (2026-05-11): asked "did the MS-I PR get merged in the same pull?"; the user correctly pushed back that they couldn't know — the right move was `grep -n "capture skipped" /opt/tradebot/data/account_snapshot.py`.

### ib_insync sync-vs-async rule (inside threadsafe coroutines)

When wrapping ib_insync calls in `asyncio.run_coroutine_threadsafe`, every call inside the coroutine MUST use the `*Async` variant. Sync ib_insync wrappers (`reqAllOpenOrders`, `accountSummary`, `qualifyContracts`) internally call `loop.run_until_complete()` via `IB._run()`; inside an awaiting coroutine the loop is already running, so `_run()` raises `RuntimeError("This event loop is already running")`.

Audit checklist for any `run_coroutine_threadsafe` patch: (1) list every ib_insync call in the inner coroutine; (2) verify each ends in `Async` OR is a pure attribute read (`openTrades`, `wrapper.accounts`, `portfolio()`); (3) sync calls inside the coroutine = latent bug. Example (2026-05-07): B-09 v1 routed correctly but the inner `_do_sync()` called sync `reqAllOpenOrders()`; every nightly AutoRestartTime triggered the systemd-restart cascade. Fix: `await reqAllOpenOrdersAsync()`.

### Web research rule

If `WebFetch` returns 403 on the first attempt, go straight to `WebSearch` — do not retry the same domain. IBKR docs and most financial sites block direct fetches. The same applies in the Cowork sandbox: `web_fetch` on any domain outside the egress allowlist fails immediately; start with `WebSearch` for IBKR/broker/third-party docs.

### Test assertion rule

Before writing a test that asserts a response body field (e.g. `r.json().get("status") == "ok"`), read the endpoint's `return` statement first. Guessing field names costs a test failure that requires a re-run.

**Import-binding patch rule:** When a test patches a module-level variable (e.g. `IB_PORT`, `IB_HOST`), read the import chain in the module under test first. `from config.settings import IB_PORT` in `config/validator.py` binds `IB_PORT` into `config.validator`'s namespace at import time; patching `config.settings.IB_PORT` afterwards has no effect. Always patch the **consuming module's namespace** (`config.validator.IB_PORT`). Example (2026-05-03): `test_cfg02` set `config.settings.IB_PORT = 9999`; the validator still saw the original value.

### API endpoint verification (frontend → backend)

Before writing `fetch("/api/X")` in any JS file, grep for the route definition in the FastAPI app:

```bash
grep -n "@app.\(get\|post\|put\|delete\).*\"/api/X\"" dashboard/app.py
```

URL drift is silent and catastrophic when paired with `.catch(() => {})`: the fetch 404s, the catch swallows it, the side-effect never happens, but the UI looks fine. Example (2026-05-04): `fetch("/api/console/lock/release", ...).catch(() => {})` 404'd on every modal close because the real route is `/api/console/release`.

### Time-based exit test rule

Integration tests for time-bounded exits (time-stop, cooldown) **must assert the bar count at exit**, not merely that a fill occurred. Asserting fill presence only lets off-by-one errors in `_bars_held`/`_cooldown_remaining` go undetected.

Minimum assertions for a time-stop test: a SELL fill exists; the SELL fill's bar index falls at `entry_bar + TIME_STOP_BARS`, not `+ TIME_STOP_BARS + 1`. For cooldown: the second BUY's bar index is ≥ `first_sell_bar + COOLDOWN_BARS + 1`. Example (2026-05-09): `test_fi02_time_stop_produces_sell` only checked `len(sell_fills) >= 1`; the `_bars_held` off-by-one held positions 9 bars instead of 8.

### Schema migration durability rule

When a `_load_state` (or any `_load_*`) detects an old schema version and rewrites in-memory fields to migration defaults, **immediately call the matching `_save_*` inside the same load call** so the new schema lands on disk before the next operation. Don't rely on a downstream save trigger (next ratchet, next fill, next shutdown) — those can be hours/days away, and a crash in between re-fires the migration warning every restart.

Audit: (1) is there an `if loaded_version < CURRENT_VERSION:` reset block in any `_load_*`? (2) does it end with `self._save_*()`? (3) add a test that writes a v(N-1) file, calls `_load_*`, reads back, asserts on-disk schema == CURRENT_VERSION. Example (2026-05-10): MS-B's RSI2-MR `_load_state` migrated v1→v2 in memory only; the file stayed v1 across the post-deploy restart.

### "Pre-existing" deferral rule

Before deferring a CR finding as "pre-existing — not introduced by this PR," answer explicitly: **does this PR make a previously stable invariant load-bearing for new code?** If yes, the finding belongs in this PR even if the defect already existed.

Triggers that turn pre-existing into in-scope: a new function reads a field previously only written; a new flag depends on integrity of state another flow can corrupt; a new computation builds on values another path can silently zero. Example (2026-05-10): MS-B's `_get_strategy_attributed_equity` started reading `_position_shares`/`_entry_price`; a pre-existing partial-SELL bug zeroed both; deferring the audit was wrong because MS-B made those fields newly load-bearing.

### CR-finding-to-BACKLOG grounding rule

When a CR agent's finding proposes adding a new BACKLOG entry, **read the referenced source file before writing the entry description**. CR agents reason from the prompt you gave them; they don't independently open files. An unverified claim in your prompt survives into the finding, the BACKLOG entry, and the next session's planning. Example (2026-05-11): MS-C plan CR raised "VIX feed silent-failure gap"; reading `data/vix_feed.py` later showed `_fire_stale_alert` already POSTs to ntfy — one extra chore branch to correct the description.

### JS rate-limit gate rule

When adding a polling gate that references multiple fetch functions (e.g. `_onAcctTab ? [fetchAccount(), fetchEquity()] : []`), the comment **must name the specific endpoint(s) that are rate-limited**, not the functions. Gate comments that name functions imply all named functions are rate-limited. Example (2026-05-06): a gate comment said "prevents fetchAccount / fetchEquity from consuming the rate limit" — but `/api/account` has no rate limit, only `/api/equity-history` does; the imprecise comment showed `—` on Mission Control until tab switch.

### Pre-fixture wiring check rule

When verifying a feature against fixture/seed data — populating a DB, writing a temp file, setting an env var — **grep the code under test for the path/source it actually reads BEFORE staging the fixture**. Don't trust a prior CHATLOG observation like "X is empty" as authoritative; it may be correct on the surface while masking a wrong-file-read bug.

The trigger is the moment you think "I'm about to populate file X so the feature shows data." Example (2026-05-13): the CHATLOG noted "VPS `trades.db` has zero rows"; a grep of `main.py` first surfaced `TradeLog(db_path=Path("data/paper_trades.db"))` — the bot writes `paper_trades.db` but `dashboard/app.py` read the default `trades.db`. The "empty" observation masked a wrong-file-read bug.

### Invisible Unicode literal rule

When a string literal needs a non-printing/invisible Unicode character (BOM `U+FEFF`, zero-width space, NBSP), **always write the `\uXXXX` escape, never the raw character**. A raw invisible char is undetectable on review, survives copy-paste silently, and an editor/git filter can strip it unnoticed. After writing such a literal, verify with a `repr()`/`assert chr(0xFEFF) not in open(...).read()` check. Example (2026-05-16): the CSV-export BOM prefix was typed as a literal `U+FEFF`; correcting to `"﻿"` cost two throwaway fix scripts.

### Describe-from-source rule

Before describing, recommending, or planning around any BACKLOG or ROADMAP item, **read that item's full entry in `docs/BACKLOG.md` or `docs/ROADMAP.md` — never paraphrase from the `CLAUDE.md` "Immediate next steps" / "Current state" summary.** Those CLAUDE.md sections are a stale-prone index, updated opportunistically and routinely lagging a decision recorded in the detail file. Example (2026-05-16): asked "what is MS-C2?", Claude described it from CLAUDE.md's summary and offered it as a next-session option; the authoritative `docs/BACKLOG.md` entry said it was measurement-gated and explicitly deferred until 2026-06-12.

### Broker-state-authority rule

When a strategy (or any stateful component) tracks position/order state alongside the broker's own view, the plan must answer ONE question explicitly before any code: **when is the broker's view authoritative, and when is it stale?** The classic stale window: an order is `Filled` (gone from `get_open_orders`) but the position has not yet appeared in `get_positions()` — read in that gap, the broker looks flat when it isn't.

Every code path that reads broker state — `on_start` reconcile, a pending-timeout self-heal, an each-tick check — must derive its behaviour from that single stated answer, not re-reason about the race locally. If two separate code reviews catch the same race in two different paths, the invariant was never written down. Example (2026-05-18, PingPongTest): the pre-impl CR caught a duplicate-order race in a "pure broker-reconcile" design; a pending flag fixed it; the post-impl CR then caught the *same* race in the pending-timeout path. Stating the invariant once ("no path places an order on a flat *snapshot*, only on a positively-confirmed position") would have closed both at plan time.

---

## File map

```
TradeBot/
├── README.md                    ← project vision
├── CLAUDE.md                    ← session handoff, identity, non-negotiables (read first)
├── SESSION_PROTOCOL.md          ← navigation stub (post-2026-05-21 split)
├── OPEN_SESSION_PROTOCOL.md     ← Opening Ritual (read first on every chat)
├── CLOSE_SESSION_PROTOCOL.md    ← Closing Ritual (read on farewell)
├── SESSION_RULES.md             ← Rules 1-13 + Additional rules + TradeBot engineering rules (this file)
├── WORKFLOW.md                  ← user-facing reference (chat archetypes, git, emergency)
├── CHATLOG.md                   ← session-to-session memory (active log)
├── SESSIONS.md                  ← legacy session log (superseded by CHATLOG.md)
├── TODO.md                      ← sprint-by-sprint task tracker
├── Makefile                     ← make pre-push = local CI mirror
├── .github/workflows/ci.yml     ← CI: ruff → black → mypy → pytest → gitleaks → account-ID grep
├── .claude/skills/              ← committed project skills (session-rituals, deep-review)
├── config/                      ← settings.py, validator.py, logging_config.py, strategies.py
├── docs/
│   ├── ROADMAP.md               ← phased plan (read at Step 4a)
│   ├── BACKLOG.md               ← deferred work + Standing Checks
│   ├── adr/                     ← architectural decision records
│   ├── runbook-2fa-recovery.md  ← Sunday 2FA routine
│   └── setup.md
├── broker/ risk/ data/ strategies/ backtester/ models/ runtime/ dashboard/
└── tests/                       ← pytest suite (run_tests.py legacy runner still present)
```
