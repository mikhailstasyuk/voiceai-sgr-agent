# SGR Appointment Business Layer Integration

## Goal
Insert a schema-guided business layer between ASR-final transcripts and TTS output so appointment booking is deterministic, type-safe, and stateful.

## Context
Current runtime routes ASR final text directly to free-form LLM reply generation. Task requires strict Pydantic-validated outputs for intent detection and appointment flow decisions.

## Assumptions
- Scope for this change is defined by the active plan/task instructions from that date.
- Business layer supports appointment intent only for now.
- Existing websocket event contract remains stable for frontend compatibility.

## Constraints
- Pydantic v2 models and `Field(description=...)` on all fields.
- Conversation history capped to last 10 entries for business LLM calls.
- JSON files are the persistence mechanism for clinic availability and bookings.

## Implementation Steps
1. Add `app/business` package with models, session store, intent detector, appointment flow, and business-layer state machine.
2. Add seed data files for clinics and appointments.
3. Wire business layer into websocket turn path; pass business-layer text to TTS.
4. Add backend tests for intent retry/fallback, booking persistence, and state transitions.
5. Update product docs to reflect appointment-only behavior and callback escalation.

## Validation Plan
- Run backend pytest suite.
- Verify health endpoint test still passes.
- Verify new business-layer tests pass.

## Progress Log
- 2026-03-16: initialized

## Decisions / Tradeoffs
- Reused existing TTS streaming orchestration and barge-in handling; only reply text source changed.
- Kept fallback support in `AgentSession` for non-business reply generation by making LLM optional.

## Follow-Ups / Debt
- Add integration-level websocket tests with provider mocks to fully validate callback cleanup and audio event continuity.

## Completion Summary (2026-03-16)
- Added `voice_backend/app/business` schema-guided appointment layer and data files.
- Routed ASR-final turns through `BusinessLayer.process` before TTS.
- Added backend tests for business-layer intent retry/fallback, flow persistence, and state transitions.
- Updated product requirements and workflows documentation.

## Validation Performed
- `../.venv/bin/python -m pytest -q tests/business/test_business_layer.py` (5 passed)
- `../.venv/bin/python -m ruff check app/business app/agent/session.py app/main.py tests/business/test_business_layer.py` (passed)
- Full/legacy health test currently hangs in this environment (`tests/test_main.py`) and needs separate investigation.
