# SGR-First Flow Recovery Refactor

## Goal
Address appointment-flow contradiction loops and callback/booking control dead-ends by moving conversational branching back to schema-guided reasoning while preserving deterministic boundary validation.

## Context
Recent logs showed repeated contradictions (model says confirm/available while guardrails deny slot), stale entity carryover, callback pivot friction, and structured-output failures without robust retry in appointment flow.

## Assumptions
- Existing policy/doctor/slot validation remains deterministic at boundary points.
- Runtime remains appointment-focused with callback fallback.

## Constraints
- Keep behavior inside business layer boundaries.
- Preserve strict structured output validation for intent and flow decisions.

## Implementation Steps
1. Expanded schema actions in appointment model (`list_clinics`, `list_doctors`, `clarify`) and added callback reasoning schema.
2. Replaced callback regex-first handling with structured callback reasoning + retry.
3. Added structured retry/correction loop to appointment reasoning (matching intent detector resilience).
4. Added entity reconciliation to clear invalid clinic/doctor/date combinations and reduce stale conflicts.
5. Removed deterministic pre-LLM uncertainty short-circuit in appointment flow.
6. Added graceful post-completion handling for noise/frustration turns.
7. Updated tests and architecture docs.

## Validation Plan
- `cd voice_backend && ../.venv/bin/python -m ruff check app/business tests/business/test_business_layer.py`
- `cd voice_backend && ../.venv/bin/python -m pytest -q tests/business/test_business_layer.py`

## Progress Log
- 2026-03-17: implemented SGR-first callback + appointment flow refactor and validated regression coverage.
- 2026-03-17: implemented follow-up flow fixes from latest logs (policy normalization, callback digit buffering, safe cancel semantics, confirmed booking id propagation).

## Decisions / Tradeoffs
- Kept deterministic guards only for strict boundary validations and persistence safety.
- Added callback reasoning schema in business layer to avoid callback dead-end loops.
- Retained compact fallback messaging on repeated schema failures to keep turn latency bounded.

## Follow-Ups / Debt
- Consider introducing explicit enum types for callback substate strings (`collect_phone`, `confirm_exit`) to reduce string-literal drift.
- Consider adding integration-level websocket conversation tests using captured log scenarios.

## Completion Summary (2026-03-17)
- Appointment and callback conversation control now primarily uses schema-guided actions.
- Appointment reasoning now has retry/correction behavior for invalid structured outputs.
- Stale entity combinations are reconciled after extraction to prevent repeated invalid confirmations.
- Callback pivot to booking remains available with explicit confirmation.
- Spoken policy-id variants (for example `P O L dash one two three four`) are normalized to strict `POL-1234` format before persistence.
- Appointment reconciliation now preserves a valid date while the doctor is being clarified, preventing date-loss loops.
- Confirmed bookings now include `session_id` in persisted records and surface `confirmed_appointment_id` back to session state.
- Cancel action now updates the latest scheduled appointment only when one exists; otherwise the flow returns a non-terminal “no scheduled appointment” prompt.
- Callback digit collection now supports partial input across turns without trapping users when they restate a full phone number.

## Validation Performed
- `ruff check`: passed
- `pytest tests/business/test_business_layer.py`: 19 passed
