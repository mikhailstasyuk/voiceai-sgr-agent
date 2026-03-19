# Flow Recovery Hardening for Callback and Booking Guidance

## Goal
Fix conversational dead ends across callback scheduling and appointment booking recovery paths.

## Context
- Callback collection trapped users in phone validation loop even when they pivoted back to booking.
- Clinic/doctor selection required explicit option requests and did not recover smoothly from repeated uncertainty.
- Appointment confirmation returned a generic failure message that caused lockups after partial entity collection.

## Assumptions
- Existing strict policy and doctor-slot constraints remain unchanged.
- Deterministic business-layer guardrails should override fragile LLM behavior where needed.

## Constraints
- Keep changes in business layer/flow boundaries.
- Preserve existing intent model (`APPOINTMENT`/`UNCLEAR`).

## Implementation Steps
1. Added callback sub-state handling with exit confirmation and appointment-flow resume path.
2. Added uncertainty counters and deterministic clinic/doctor option listing after repeated uncertainty.
3. Replaced generic booking-confirmation failure response with field-specific recovery prompts.
4. Expanded backend tests for callback pivot, repeated uncertainty, and date-only lockup regression.
5. Updated product and architecture docs to reflect new behavior.

## Validation Plan
- `cd voice_backend && ../.venv/bin/python -m ruff check app/business tests/business/test_business_layer.py`
- `cd voice_backend && ../.venv/bin/python -m pytest -q tests/business/test_business_layer.py`

## Progress Log
- 2026-03-17: implemented callback pivot recovery, uncertainty option surfacing, and targeted booking validation messaging.

## Decisions / Tradeoffs
- Chosen behavior for callback pivot: ask for explicit confirmation before leaving callback mode.
- Chosen behavior for option surfacing: list clinic/doctor options after repeated uncertainty, and on explicit options request.
- Kept deterministic rules local to business flow to reduce latency and reduce reliance on prompt compliance.

## Follow-Ups / Debt
- Consider promoting callback sub-state from string literals to an enum-backed model if callback logic expands.

## Completion Summary (2026-03-17)
- Callback flow now allows switching back to booking with confirmation and resume of booking intent.
- Appointment flow now lists clinic/doctor options after repeated uncertainty and no longer dead-ends on generic validation errors.
- Added coverage for the reported regressions and related recovery paths.

## Validation Performed
- `ruff check`: passed
- `pytest tests/business/test_business_layer.py`: 16 passed
