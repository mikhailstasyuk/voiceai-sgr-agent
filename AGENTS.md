# Repository Guidelines

## Purpose
This repository is set up for an agent-first workflow:
- Humans set intent, constraints, and tradeoffs.
- Codex executes implementation and validation work.

Treat repository files as the source of truth. If behavior is not documented in-repo, do not invent it.

## Source-of-Truth Order
Use this order when guidance conflicts:
1. Active task instructions (for the current change)
2. `docs/product/requirements.md`
3. `docs/architecture/` rules
4. `docs/quality/` standards
5. Existing code behavior
6. Root `README.md` for setup and run commands

Note: `TASK.md` is not a tracked source of truth in this repository.

## Documentation Map
Start from [docs/README.md](/home/rhuu/mygit/hypercheap-voiceAI/docs/README.md).

Core references:
- Beliefs and workflow principles: [docs/core-beliefs.md](/home/rhuu/mygit/hypercheap-voiceAI/docs/core-beliefs.md)
- Architecture map and boundaries: `docs/architecture/`
- Product intent and user workflows: `docs/product/`
- Planning system: `docs/plans/`
- Quality and reliability bar: `docs/quality/`
- Terms and definitions: [docs/glossary.md](/home/rhuu/mygit/hypercheap-voiceAI/docs/glossary.md)
- Debt backlog: [docs/debt/tech-debt.md](/home/rhuu/mygit/hypercheap-voiceAI/docs/debt/tech-debt.md)

## Required Agent Workflow
For non-trivial changes:
1. Read relevant docs (`product`, `architecture`, `quality`, and active plans).
2. Inspect current code paths before editing.
3. Write/update an active plan entry for multi-step work.
4. Implement changes in small, reviewable steps.
5. Run relevant existing validations (tests/build checks already used in repo).
6. Update docs/plans when behavior, assumptions, or boundaries change.
7. Summarize what changed and what was validated.

## Planning Rules
- Use `docs/plans/active/` for ongoing substantial work.
- Include: goal, context, assumptions, constraints, steps, validation plan, decisions, follow-ups.
- Move completed plans to `docs/plans/completed/` with outcome notes.
- Keep plans decision-complete; avoid “TBD” for core implementation choices.
- Put task-specific scope and tradeoffs in the relevant plan file instead of a root `TASK.md`.

## Architecture and Boundary Rules
- Follow `docs/architecture/dependency-rules.md` for allowed dependency direction.
- Put new logic in the closest domain/layer location; avoid cross-layer shortcuts.
- Validate external/config/input data at boundaries before use.
- Prefer explicit interfaces and typed structures over implicit behavior.

## Product and Behavior Rules
- Use `docs/product/requirements.md` as the baseline for expected behavior.
- If implementation changes user-visible behavior, update:
  - `docs/product/user-workflows.md`
  - any affected requirements
  - the active/completed plan notes

## Quality Expectations
- Use `docs/quality/quality-standards.md` for correctness and maintainability criteria.
- Use `docs/quality/reliability-goals.md` for latency/uptime/error handling expectations.
- Do not claim completion without listing concrete validation performed.

## Reference Repository Guidance
`sgr-agent-core/` is reference-only in this repository.
- Use it as examples of Schema Guided Reasoning patterns.
- Do not treat it as runtime source of truth for this codebase.
- Do not couple production behavior to undocumented assumptions from that reference repo.

## Escalate to Human When
Escalate when any of these apply:
- Product intent is ambiguous or conflicting.
- Security/privacy/compliance implications are unclear.
- A destructive migration or irreversible data change is required.
- Architecture boundary conflicts cannot be resolved from docs and code.
- Repeated remediation attempts fail without clear root cause.

## Change Hygiene
- Keep docs concise and cross-linked.
- Prefer updating existing docs over creating duplicates.
- Record new recurring review feedback in docs so it compounds.
- Add debt items for known gaps instead of leaving implicit TODOs.
