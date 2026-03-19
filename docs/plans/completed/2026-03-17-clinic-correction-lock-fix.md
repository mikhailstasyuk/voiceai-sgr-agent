# Clinic Correction Lock Fix

## Goal
Prevent appointment-flow lock loops when the user corrects clinic choice mid-flow, while preserving policy-id-first intake and SGR-guided selections.

## Context
- Latest websocket logs showed repeated schema validation failures for `selected_doctor_id` when no doctor options were valid for the turn.
- On correction turns, stale clinic state could persist, causing repeated clarification loops.

## Assumptions
- Strict output structure remains required.
- Option membership can be validated deterministically after parse.

## Constraints
- Keep existing flow action contract and policy-id-first gating.
- Avoid introducing non-deterministic persistence behavior.

## Implementation Steps
1. Keep selection fields parse-stable (`string|null`) and encode allowed options in prompt/schema descriptions.
2. Enforce allowed-option membership in deterministic sanitization.
3. Reset downstream doctor/date state when clinic changes in-turn and ignore stale doctor/date selection from same turn.
4. Add regression tests for schema stability and clinic-correction reset behavior.

## Validation Plan
- Run `pytest -q tests/business/test_business_layer.py`.
- Run `ruff check app/business tests/business/test_business_layer.py`.

## Progress Log
- 2026-03-17: initialized and implemented code/test changes.

## Decisions / Tradeoffs
- Chosen approach: stable parse shape + deterministic membership checks, to avoid provider hard-fail loops.
- Tradeoff: schema no longer hard-enforces dynamic doctor/date enums at provider boundary.

## Follow-Ups / Debt
- Evaluate adding separate soft-link field (e.g., `selected_doctor_name`) to improve STT-variant linkage quality.

## Outcome Notes
- 2026-03-17: implemented schema-stable selection fields for doctor/date, clinic-correction state reset, and regression tests.
- Validation passed:
  - `../.venv/bin/python -m pytest -q tests/business/test_business_layer.py` (28 passed)
  - `../.venv/bin/python -m ruff check app/business tests/business/test_business_layer.py` (passed, existing `pyproject.toml` deprecation warning only)
