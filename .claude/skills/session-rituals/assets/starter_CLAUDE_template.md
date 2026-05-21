# [Project Name] — Claude Project Instructions

> **Claude: this file defines how you behave in this project. Read it first, always. Follow it exactly.**
>
> **Template note:** This is a starter scaffold from the `session-rituals` Cowork skill. Replace bracketed `[placeholders]` with your project's specifics. Delete sections you don't need; add sections you do. The dispatch to the `session-rituals` skill (in the Protocol section below) is the one part you should keep mostly as-is.

---

## Identity & Language

- **Project:** [Describe what this project is — e.g., "Internal customer-analytics dashboard," "Open-source CLI tool for parsing logs," "Mobile app for habit tracking."]
- **User:** [Your name + experience level. Helps Claude calibrate explanation depth — e.g., "Yardena Cohen. Senior backend engineer, beginner at frontend."]
- **Language:** [Your language preferences. E.g., "I write in Hebrew, you respond in English." OR "Both of us in English." OR "I write in either, you match my last message."]

---

## Protocol

This project uses the **`session-rituals`** Cowork skill for its working pattern. The skill provides:

- **Opening ritual** on the first user message of every chat (7 steps: greet → verify environment → orient on state → ask for focus → self-critique → go)
- **Closing ritual** on any farewell signal (8 steps: retrospective + score → compose session log → write to disk → commit commands → warm close → recap → next-session preview)
- **Continuous Improvement principle** that ties them together (each session leaves the project measurably better)

The skill triggers automatically on first-message and farewell-signal patterns. No explicit invocation needed in normal use.

**Project-specific orientation reads:** the opening ritual's Step 4a needs to know where your project's session log + plan-of-record live. Defaults the skill looks for:

- Session log: `CHATLOG.md` at project root (also tries `HISTORY.md`, `log.md`, `docs/sessions.md`). [Adjust to your project's actual location.]
- Plan-of-record: `docs/ROADMAP.md` (also tries `ROADMAP.md`, `docs/plan.md`, `PLAN.md`). [Adjust to your project's actual location.]

If your project doesn't have these files, the skill adapts — see SKILL.md "Common questions."

---

## Non-Negotiables

[Rules that must NEVER be violated, regardless of what the user asks. Examples below — replace with your project's:]

- [example: "Never commit secrets to git. The `.env` file must stay gitignored."]
- [example: "Never push to `main` directly. All changes go through PR review."]
- [example: "Never run destructive database commands (DROP, DELETE without WHERE) without explicit user confirmation."]

If the user ever asks for something that violates any of these, STOP and flag it. Do not comply silently.

---

## Style Preferences

[How you want Claude to communicate with you. Examples — adjust to your taste:]

- **Don't be a yes-man.** If you disagree with my framing, say so. Genuine disagreement, surfaced respectfully, is more valuable than agreement-by-default.
- **Present only the correct option.** When analysis already points to one right answer, state it — don't pad with known-inferior alternatives. Filtering options is your job, not mine.
- **Build incrementally.** Suggest a commit at every meaningful milestone.
- **Ground answers in this project's docs**, not generic best-practices, when there's a specific decision recorded.

---

## Project-Specific Trigger Guide

The skill provides a generic Trigger Guide pattern (just-in-time rule loading). Extend it here with your project's specific triggers and rules. Examples:

- **About to commit / push** → load [your pre-push verification rules — e.g., `docs/PRE_PUSH.md`]
- **About to edit a [file type] file** → load [relevant formatting / linting rules]
- **About to draft a design document** → load [adversarial review rule — e.g., `docs/REVIEW.md`]
- **Session count divisible by N** → load [hygiene rituals — session log archive, backlog review]
- **Phase transition** → load [the skill's full ritual files + this project's risk-management doc]

[Add / remove / customize per your project.]

---

## File Map

[Optional — describe your project's important files so Claude can orient quickly. Examples:]

```
[project-name]/
├── README.md              ← project vision
├── CLAUDE.md              ← this file
├── CHATLOG.md             ← session-to-session memory (skill reads last 3 entries at Step 4a)
├── docs/
│   ├── ROADMAP.md         ← plan-of-record (skill reads at Step 4a)
│   ├── BACKLOG.md         ← deferred work
│   └── adr/               ← architectural decisions
├── src/                   ← code
└── tests/                 ← tests
```

---

## Codification Policy (optional)

If your project has a policy for how learnings become rules (e.g., a "3-strikes" rule like ADR-0029 in the originating project), reference it here. The closing ritual's Step 1 will defer to it.

If your project has no such policy, the skill uses a default "two homes" model: codify generalizable lessons as rules; capture one-off observations in the session log only.

---

## Suggested initial scaffold

If you're starting a fresh project, copy this template to your project root as `CLAUDE.md`, fill in the placeholders, and create the following minimum companion files:

- `CHATLOG.md` (or your chosen session-log name) — empty for now; the closing ritual will append entries.
- `docs/ROADMAP.md` (or your chosen plan-of-record name) — at least a phase plan or milestone list.

The skill works without these, but it adapts gracefully (asks if it should scaffold them or skip the missing reads).

---

*This template is bundled with the `session-rituals` Cowork skill. The skill itself provides the ritual structure; your CLAUDE.md provides project-specific identity, rules, and customization on top.*
