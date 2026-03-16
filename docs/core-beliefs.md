# Core Beliefs

These principles guide repository changes in an agent-first workflow.

## Humans Steer, Agents Execute
- Humans define outcome, constraints, and tradeoffs.
- Agents implement, validate, and document within those boundaries.

## Repo Is The System Of Record
- Durable knowledge must live in this repository.
- Off-repo context is treated as non-authoritative unless copied into docs here.

## Small Maps Beat Large Blobs
- Keep `AGENTS.md` short and routing-focused.
- Store detail in focused topic docs and cross-link them.

## Explicit Boundaries
- Prefer clear domain and layer boundaries over style debates.
- Keep dependency direction predictable.
- Validate data at boundaries rather than inferring unknown shapes.

## Validation Before Completion
- Every meaningful change must include explicit validation evidence.
- Avoid “done” claims without tests/build/manual checks tied to the change.

## Continuous Cleanup
- Capture known debt in `docs/debt/tech-debt.md`.
- Move repeated review feedback into docs and conventions.
- Keep active plans current; archive finished plans.

