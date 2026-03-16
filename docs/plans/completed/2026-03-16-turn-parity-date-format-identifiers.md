# Turn Parity + Friendly Dates + Required Identifiers

## Goal
Fix reply parity between UI token text and spoken TTS, present user-friendly dates in replies, and require policy id + doctor name for appointment booking.

## Context
Current flow can diverge UI/token text from spoken text due to sanitization timing, uses ISO dates in user-facing text, and confirms bookings without policy id or doctor name.

## Assumptions
- ISO dates remain internal persistence format.
- User-facing text should be human-readable for dates in both UI and TTS.
- Appointment confirmation requires: date, clinic, policy_id, doctor_name.

## Constraints
- Keep schema-guided strict validation path for all LLM decisions.
- Preserve existing websocket protocol and barge-in behavior.

## Implementation Steps
1. Extend booking schemas/entities to include policy_id and doctor_name.
2. Enforce missing-field prompting in appointment flow and persistence.
3. Normalize user-facing response text with friendly date formatting before streaming.
4. Align UI token emission and TTS input to the same normalized reply text.
5. Update tests and seed data.

## Validation Plan
- Run business tests and targeted session checks.
- Run lint on changed backend files.

## Progress Log
- 2026-03-16: initialized

## Decisions / Tradeoffs
- Date friendly formatting happens at business response boundary so both UI and TTS see the same final text.

## Follow-Ups / Debt
- Add websocket integration test asserting exact token concatenation equals synthesized text input.

## Completion Summary (2026-03-16)
- Aligned precomputed reply normalization so UI token stream and TTS consume the same canonical text.
- Added user-facing ISO date humanization at business response boundary.
- Extended appointment entity requirements to include `policy_id` and `doctor_name`.
- Updated booking persistence to store `policyholder_id` from policy id and include `doctor_name`.
- Updated product docs and added regression tests.

## Validation Performed
- `./.venv/bin/python -m ruff check voice_backend/app/agent/session.py voice_backend/app/business voice_backend/tests/business/test_business_layer.py`
- `../.venv/bin/python -m pytest -q tests/business/test_business_layer.py` (8 passed)

## Remaining Follow-Up
- Add websocket integration test to assert concatenated `llm_token` stream exactly equals synthesized text per turn.
