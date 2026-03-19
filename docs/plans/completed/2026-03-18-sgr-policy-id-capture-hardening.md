# SGR Policy ID Capture Hardening

## Goal
Make policy-ID capture robust across appointment, renewal, clinic-change, and plan-inquiry flows when STT/LLM extraction is partial or missing, while keeping strict schema-guided reasoning and deterministic boundary validation.

## Context
- Latest logs showed repeated policy loops when callers provided valid spoken IDs without explicitly saying "dash".
- Existing implementation over-relied on `extracted_policy_id` and missed recoverable IDs from raw turn text/history.

## Assumptions
- Policy format remains `POL-####`.
- Bare 4-digit values are accepted only with recent `POL` prefix context.
- Three recent user turns are sufficient for split-turn assembly.

## Constraints
- Keep SGR as control plane (strict structured outputs + schema enums).
- Keep persistence/format checks deterministic at boundaries.
- Keep unrelated workspace changes untouched.

## Implementation Steps
1. Added shared policy-candidate utility for parsing/normalization/candidate selection.
2. Extended flow reasoning models with `selected_policy_candidate_id`.
3. Integrated candidate context + enum-constrained schema fields in all client flows.
4. Resolved policy candidates via model selection first, deterministic fallback second.
5. Preserved existing policy gate checks and targeted error messages.
6. Added regression tests for no-dash parsing, split-turn recovery, and cross-flow capture.
7. Updated product/architecture/workflow docs to reflect optional spoken dash + multi-turn window.

## Validation Plan
- `cd voice_backend && ../.venv/bin/python -m pytest -q tests/business/test_business_layer.py`
- `cd voice_backend && ../.venv/bin/python -m ruff check app/business tests/business/test_business_layer.py`

## Progress Log
- 2026-03-18: initialized active plan.
- 2026-03-18: implemented shared policy-candidate capture and integrated across appointment/renewal/clinic-change/plan-inquiry.
- 2026-03-18: added regressions and updated docs.

## Decisions / Tradeoffs
- Used strict candidate-ID schema guidance for policy selection with deterministic fallback when selection is null/invalid.
- Reused one policy-capture utility across flows to avoid behavior drift.
- Kept strict persistence validation (`POL-####` + store lookup) unchanged.

## Follow-Ups / Debt
- Add websocket-level replay tests from captured logs for policy capture regressions.

## Outcome
- Added shared `PolicyIdCapture` utility and schema helper for policy candidate enum fields.
- Added `selected_policy_candidate_id` to appointment, renewal, plan inquiry, and clinic change reasoning models.
- Appointment flow now uses policy candidates from raw/history/extracted inputs and resolves via schema-selected candidate or deterministic fallback.
- Renewal, clinic-change, and plan-inquiry flows now use the same policy candidate pipeline.
- Added regressions for:
  - appointment raw-text recovery when model extraction is missing,
  - renewal no-dash policy capture,
  - clinic-change 3-turn split policy assembly,
  - plan-inquiry compare path with spoken policy id.
- Updated requirements/workflow/architecture docs for 3-turn split capture and prefix-context guard.

## Validation Results
- `cd voice_backend && ../.venv/bin/python -m pytest -q tests/business/test_business_layer.py` -> `54 passed`
- `cd voice_backend && ../.venv/bin/python -m ruff check app/business tests/business/test_business_layer.py` -> `All checks passed` (with existing pyproject deprecation warning)
