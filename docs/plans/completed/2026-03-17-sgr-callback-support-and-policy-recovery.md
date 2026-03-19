# SGR Callback Support and Policy Recovery

## Goal
Eliminate callback/policy dead-end loops by introducing schema-guided callback support routing, mode-scoped callback reasoning, and non-client handoff from policy-gated flows.

## Context
- Latest logs showed callback collection misrouting (`no` interpreted as service switch) and policy-id loop after callback cancellation.
- Product docs require robust schema-guided decisions and recovery behavior without deterministic exact-match traps.

## Assumptions
- Strict schema + Pydantic validation remains the default decision boundary.
- Deterministic logic is limited to boundary validation and persistence integrity.

## Constraints
- Keep changes inside business layer and flow boundaries.
- Preserve existing appointment confirmation, duplicate/conflict safety, and callback capture persistence.

## Implementation Steps
1. Extend intent contract with `CALLBACK_SUPPORT`.
2. Add callback-support flow with strict SGR actions for status/explanation/reschedule handling.
3. Add callback mode-scoped action schema and sanitization in callback capture (`collect_phone` vs `confirm_exit`).
4. Add appointment policy-gate signal (`unavailable_or_not_client`) and non-client handoff after refusal signals.
5. Route non-client handoff back into onboarding options instead of looping on `POL-####`.
6. Add regression tests for callback-support routing, noisy callback negatives, policy-unavailable handoff, and onboarding handoff state.
7. Update product/workflow/architecture docs.

## Validation Plan
- `cd voice_backend && ../.venv/bin/python -m pytest -q tests/business/test_business_layer.py`
- `cd voice_backend && ../.venv/bin/python -m ruff check app/business tests/business/test_business_layer.py`

## Outcome Notes
- Implemented callback-support intent and flow.
- Implemented callback mode-scoped SGR schema constraints and sanitize fallback behavior.
- Implemented policy-unavailable handoff path to non-client options in appointment flow.
- Added regression tests and updated docs.
