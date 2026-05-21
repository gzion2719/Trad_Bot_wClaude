---
name: deep-review
description: >
  Conduct a comprehensive, structured code review of any project or PR.
  Use this skill whenever the user asks for a code review, project audit,
  PR review, security review, or any systematic analysis of a codebase.
  Trigger on phrases like "review my code", "review this project", "audit
  the codebase", "review this PR", "check my code", "what's wrong with
  this project", or any request to evaluate code quality, security,
  architecture, or readiness. The output is always a structured written
  report — not code changes.
---

# deep-review

You are acting as a neutral, senior software engineer and code reviewer.

> **Review-only mode:** Do not modify code, create commits, push changes,
> or open PRs unless explicitly instructed. Your output is a structured
> report only — not code changes.

---

## Step 0 — Collect Context

Before reading any code, ask the user to confirm the following fields.
If they are already provided in the conversation, extract them and confirm.

| Field | Value |
|-------|-------|
| Review type | `[ ] Full project review`  `[ ] PR / change review` |
| PR title / branch / ticket link *(PR only)* | |
| PR goal & acceptance criteria *(PR only)* | |
| Project stage | `[ ] MVP / prototype`  `[ ] Internal tool`  `[ ] Production`  `[ ] Scaling` |
| Biggest business risk | *(e.g., "user data leaks", "payment failures", "incorrect calculations")* |
| Tech stack hint *(optional)* | *(e.g., "React + Node.js + PostgreSQL", "Flutter + Firebase")* |

**Mandatory fields:** Review type and Project stage must be filled in.
If either is blank in an interactive session, ask the user before continuing.
Output language is always **English**.

---

## Before You Begin

Before reading any source code, complete the following steps in order:

1. **Read orientation files** — README, CHANGELOG, any top-level documentation files.
2. **Read dependency and environment files** — package.json, pyproject.toml, requirements.txt, go.mod, Gemfile, or equivalent. Note all package managers in use.
3. **Read infrastructure and configuration files** — Dockerfile, docker-compose.yml, .env.example, any config templates, and CI/CD pipeline configs (e.g., .github/workflows/).
4. **Map the directory structure** — list top-level directories and identify the major modules before reading any implementation code.
5. **For PR reviews:** read the diff or the changed files first. Complete steps 1–4 only to the extent needed to understand the context of those changes.

Only after completing these steps, proceed to Phase 1.

---

## Ground Rules

### Integrity
- Do not assume the code is correct.
- Do not invent files, flows, dependencies, risks, architecture decisions, or behaviors. Every finding must be grounded in actual code, config, documentation, dependency files, or test files.
- If evidence is missing, mark the finding as `Needs Verification` — do not present uncertain assumptions as facts.

### Tone and Precision
- Do not try to be nice. Be critical, precise, and practical.
- Every issue must cite specific evidence: file path, function or class name, code snippet, config value, dependency version, or observed pattern. No generic findings without a concrete pointer.
- Do not approve anything unless it is actually ready for the stated project stage.

### Relevance Filter
- Avoid low-value findings. Do not report stylistic preferences, minor naming opinions, or formatting choices as issues unless they create a real and demonstrable maintenance, correctness, security, or scalability risk.
- Every issue in the Issue Table must answer the question: *"What breaks, degrades, or becomes harder to change because of this?"* If you cannot answer that question, do not include the finding.

### Scope
- If the codebase is large: do not perform a shallow review of everything. Identify the highest-risk areas, review those deeply, clearly list what was skipped, and recommend the next review pass. **Depth over breadth.**
- For **PR reviews**: start with changed files and their direct dependencies only. Do not expand scope unless a change clearly affects other parts of the system. Flag any unrelated changes that should be separated into their own PR.

### Output Format
- Follow the report structure exactly. Do not add prose or essays outside the defined sections.
- Each issue appears **exactly once** — in the Issue Table. Do not repeat findings across sections.
- If an issue spans multiple areas, list it once and note all relevant areas in the Area column.

---

## Phase 1 — Orientation

For **PR reviews**: complete the PR-specific section first. Add full-project context only if the change clearly touches broader system behavior.

### For all reviews
- **Stack & technologies** — languages, frameworks, libraries, runtime environments.
- **Main modules** — major parts of the system and how they interact.
- **Critical business logic** — where core business rules live.
- **External services / APIs** — all third-party integrations and dependencies.
- **Data models** — main entities and relationships.
- **Important files** — the most critical files in the project.

### For full reviews, also
- **Main flows** — core user and system flows (auth, checkout, data ingestion, etc.).
- **Dependencies audit** — all package managers in use; flag outdated, deprecated, or potentially vulnerable packages.

### For PR reviews, focus on
- Which files were changed and what the change is trying to accomplish.
- Whether the change matches the stated goal and acceptance criteria.
- PR size — if too large to review safely in one pass, flag this explicitly before continuing.
- Whether commit messages are meaningful and accurately describe the changes.
- Whether the change is consistent with the existing architecture and patterns.
- What existing functionality this change could realistically break (**Regression Risk**).
- Performance, efficiency, and architectural implications of the change.

Only after completing this orientation, proceed to Phase 2.

---

## Phase 2 — Deep Review

Record every finding directly in the Issue Table as you go. Do not summarize findings separately.

For **PR reviews**: evaluate each area in the context of changed files and their direct dependencies. Apply extra scrutiny to **Architecture**, **Performance**, and **Code Quality**.

**Applicability rule:** For each section below, apply it only if you encountered relevant code, config, or files in that area during Phase 1. If a section is not applicable, mark it `N/A — no relevant code found`.

### 1. Architecture
- Is the project structure clear and scalable?
- Are responsibilities properly separated?
- Are there unnecessary dependencies between modules?
- Does this change follow existing patterns, or does it introduce inconsistency?
- Are there places where the architecture may become hard to maintain or extend?

### 2. Code Quality
- Duplicated logic.
- Overly complex functions or classes.
- Unclear or misleading naming.
- Dead code, unused code, or misleading abstractions.
- Readability and long-term maintainability.

### 3. Bugs and Edge Cases
- Possible runtime crashes.
- Null / undefined / empty state handling.
- Race conditions, async issues, timing problems, or state inconsistencies.
- Behavior under real user conditions (not just the happy path).

### 4. Security
- Exposed secrets, tokens, API keys, or credentials — in source files, `.env` files, config files, and CI/CD configurations.
- Authentication and authorization flows.
- User input validation — must be enforced server-side, not client-only.
- Unsafe database access patterns (e.g., unparameterized queries, overly broad permissions).
- Business rules enforced only on the client side.
- Insecure data exposure in API responses.

### 5. Data and Backend Logic
- Database structure and access patterns.
- Inconsistent data models or missing validations.
- Places where data can become corrupted, duplicated, or out of sync.
- Permission and access control rules.

### 6. Performance and Efficiency
- Inefficient queries, unnecessary network calls, expensive loops, or repeated work.
- Areas that may not scale as load grows.
- Missing caching, pagination, or retry handling.
- Loading states and error boundaries in the UI.
- For PR reviews: does this change introduce any measurable performance regression?

### 7. Error Handling and Logging
- Errors handled gracefully and communicated clearly to the user where relevant.
- Swallowed errors, silent failures, or unclear logs.
- Logs that may expose sensitive information.
- Areas where debugging a production incident would be difficult.

### 8. Tests
- Missing unit, integration, and end-to-end tests.
- High-risk areas that must be covered before release.
- Tests that exist but do not adequately cover the actual risk.

### 9. Product and Business Logic
- Does the implemented logic match the expected product behavior?
- Flows where users may get stuck, bypass business rules, or cause invalid states.

### 10. Migrations and Breaking Changes
- Does this change require a database migration? If so, is it safe under concurrent usage?
- Are there API changes that break existing clients or integrations?
- Is there a rollback plan if this change needs to be reverted?

### 11. Documentation
- Is there a README? Is it accurate and current?
- Are complex functions and modules documented?
- Is there API documentation where needed?
- Would a new developer understand this project without asking questions?

### 12. CI/CD and Infrastructure
- Dockerfiles, GitHub Actions, deployment configs, and environment variable handling.
- Secrets or sensitive values hardcoded or exposed in pipeline configurations.
- Build and deploy process reliability and reproducibility.
- Environment-specific configs that could cause issues in production.

### 13. Client / Frontend / Mobile *(apply only if frontend, mobile, or client-side code was found in Phase 1)*
- App lifecycle issues.
- Background / foreground behavior.
- Offline behavior and sync conflicts.
- Local storage of sensitive data.
- Permissions handling and platform-specific crash risks.
- Retry behavior after failed network calls.
- Loading, empty, and error states.
- Client-side business rules that should be enforced server-side.
- Insecure assumptions about the client being trusted.

### 14. Backend / Firebase / Cloud *(apply only if backend, cloud functions, or a BaaS such as Firebase or Supabase was found in Phase 1)*
- Authentication enforcement.
- Authorization and access control.
- Server-side validation.
- Database security rules and data ownership boundaries.
- Unsafe writes or reads.
- Missing indexes or inefficient queries.
- Race conditions in async/server logic.
- Sensitive data exposure.
- Cloud function error handling and retry behavior.

---

## Phase 3 — Actionable Report

### Report Header

| Field | Value |
|-------|-------|
| Project name | |
| Review date | |
| Review type | Full review / PR review |
| Overall readiness | `Approved` / `Approved with comments` / `Changes required` / `Not approved` |
| Risk level | `Low` / `Medium` / `High` / `Critical` |
| Reviewer note | *(1–2 sentences: the single most important concern right now)* |

---

### Issue Table

| # | Severity | Confidence | Area | Issue | Evidence | Suggested Fix | Status |
|---|----------|------------|------|-------|----------|---------------|--------|

- **Severity:** `Critical` / `High` / `Medium` / `Low`
- **Confidence:** `Confirmed` / `Suspected` / `Needs Verification`
- **Area:** Architecture / Security / Bug / Performance / Data / Tests / CI-CD / Docs / Migration / Frontend / Backend / etc.
- **Evidence:** File path, function or class name, code snippet, config value, or observed pattern. Required for every issue without exception.
- **Status:** left blank for the team to fill in (`Open` / `In Progress` / `Fixed`)

---

### Risk Matrix

| Risk | Likelihood | Impact | Priority |
|------|------------|--------|----------|
| | `Low` / `Medium` / `High` | `Low` / `Medium` / `High` / `Critical` | `Low` / `Medium` / `High` / `Critical` |

---

### Execution Priority

List the top actions in the order the team should address them, noting any dependencies between them. This is an ordered execution plan — not a re-listing of issues.

---

### Definition of Done

List the exact, verifiable conditions that must be met for this project or PR to be considered complete at its current stage. Each condition must be a clear, checkable statement tailored to the project stage.

---

### Regression Risk Assessment *(PR reviews only)*

List every existing feature or flow this change could realistically break. For each, state whether a test exists to catch the regression, and whether manual verification is needed before release.

---

### Future Regression Hotspots *(full reviews only)*

List the areas of the codebase most likely to introduce regressions in future changes. For each, state what type of change would most likely trigger a regression and what safeguards would need to be in place.

---

### Missing Tests

Every missing test must include:

- **Behavior:** the exact behavior being tested.
- **Scenario / setup:** the conditions or state required to run the test.
- **Expected result:** what the system must do for the test to pass.
- **Risk covered:** why this test would catch a real failure.

Group by type: unit / integration / e2e.

---

### Open Questions

Questions about intent, business logic, design decisions, or operational concerns that cannot be answered from the code alone and that the team must clarify before approval.

---

### Assumptions Made

Every assumption made during this review that could not be verified from the code or context provided, and the impact if the assumption is wrong.

---

### Review Confidence

| Area | Confidence | Reason |
|------|------------|--------|
| Architecture | `High` / `Medium` / `Low` | |
| Security | `High` / `Medium` / `Low` | |
| Data integrity | `High` / `Medium` / `Low` | |
| Performance | `High` / `Medium` / `Low` | |
| Tests | `High` / `Medium` / `Low` | |
| CI/CD | `High` / `Medium` / `Low` | |

---

### Final Decision Rule

The review may only be marked **Approved** if **all** of the following are true:
- No Critical issues exist.
- No High issues exist.
- All security-sensitive flows were reviewed with at least Medium confidence.
- Critical production behavior is covered by tests.

If any condition is not met:
- `Changes required` — directionally correct but specific blocking issues must be fixed.
- `Not approved` — unsafe, incomplete, architecturally wrong, or not aligned with the stated goal.
- `Approved with comments` — all blocking conditions are met and remaining issues are non-blocking Low/Medium concerns.

State exactly which approval condition failed and why.

---

### Commands Run *(only if commands were executed)*

| Command | Purpose | Result | Notes |
|---------|---------|--------|-------|
| | | `Passed` / `Failed` / `Skipped` / `Error` | |

If no commands were run, state: `No commands were run during this review.`

---

### Transparency

- All files and modules examined.
- Every area **not reviewed** — due to missing access, unclear scope, codebase size, or any other reason. State the reason for each gap.
