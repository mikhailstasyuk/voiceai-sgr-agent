# Appointment Integrity and Callback Hardening

## Goal
Eliminate invalid and duplicate appointment records, constrain booking choices to valid doctor/slot inventory, enforce strict policy ID format validation, and make callback escalation collect and validate a callable phone number instead of ending in a dead end.

## Context
- Current booking confirmation appends directly to `voice_backend/app/business/data/appointments.json` with no dedupe checks.
- `AppointmentFlow` currently accepts any non-empty `doctor_name` and `policy_id` when building the persisted record.
- `clinics.json` contains clinic-level `available_slots` but no doctor roster; flow prompt asks for doctor name without validating it against an inventory.
- Callback escalation path sets `SessionState.SCHEDULING_CALLBACK` and replies with static callback text, but there is no callback sub-flow collecting phone numbers.
- Existing backend tests cover happy-path booking and unclear-intent escalation, but do not cover dedupe, strict policy/phone validation, or doctor-slot constraints.

## Assumptions
- Single-agent booking scope remains appointment-only plus callback fallback (no additional intent domains in this change).
- JSON files remain the persistence mechanism for now.
- Policy ID format baseline will be `POL-` followed by 4 digits (e.g., `POL-1001`) unless product docs specify a different canonical format during implementation.
- Phone validation baseline should support normalized E.164-compatible US numbers (for example, `+15551234567`) and accepted user-friendly inputs that can be normalized into this form.

## Constraints
- Respect backend dependency/layer rules in `docs/architecture/` (business logic in `app/business`, no delivery-layer shortcuts).
- Validate/normalize all boundary inputs before persistence.
- Keep turn latency impact minimal (lightweight in-memory checks over heavy per-turn I/O where possible).
- Update product and workflow docs when behavior changes.

## Implementation Steps
1. Add an active data model for doctor availability.
   - Add `voice_backend/app/business/data/doctors.json` keyed by clinic with doctor IDs/names and available ISO slots.
   - Add a small data access helper in business layer to load clinic + doctor availability once per flow instance.

2. Harden entity validation in appointment flow before confirmation.
   - Add explicit validators in `AppointmentFlow` for:
     - `policy_id` regex format (strict)
     - `doctor_name` membership in selected clinic doctor roster
     - requested `date` membership in selected doctor slots
   - Return guided correction prompts when validation fails (instead of confirming booking).

3. Prevent duplicate appointments and slot conflicts.
   - Add a deterministic duplicate check before append (same normalized policyholder_id + clinic_id + doctor_id/name + date + `scheduled` status).
   - Add availability conflict guard so already-booked doctor/date slot cannot be booked again.
   - Keep append operation atomic enough for current file-based persistence (read-check-write in one code path with explicit failure messaging).

4. Introduce callback capture flow with phone validation.
   - Extend flow/state models to support callback-specific actions (`ask_callback_phone`, `confirm_callback`) and a `callback_phone` pending entity.
   - Replace static `SCHEDULING_CALLBACK` response with an interactive callback sub-flow that:
     - asks for phone number
     - normalizes and validates number format
     - confirms callback request only after valid number
   - Persist callback requests to a dedicated JSON store (for example `callback_requests.json`) for traceability.

5. Tighten structured output contracts.
   - Update Pydantic schemas (`AppointmentReasoning` and any callback reasoning model) to include only explicit allowed actions/fields.
   - Ensure prompt instructions force selection from provided doctors/slots and reject free-form invalid entities.

6. Expand test coverage for regressions and edge cases.
   - Add tests for:
     - invalid policy IDs rejected (`"P O L"`, malformed patterns)
     - invalid doctor names rejected (`"One zero zero one"`)
     - unavailable doctor/date slot rejected
     - duplicate appointment prevention
     - callback escalation asks for phone and validates format before completion
   - Keep existing tests green and update expectations where callback behavior changes.

7. Update docs to match behavior.
   - Update `docs/product/requirements.md` with strict policy ID, doctor-slot inventory constraints, duplicate prevention, and callback phone capture.
   - Update `docs/product/user-workflows.md` with callback phone collection/confirmation path.
   - Record any residual limitations in `docs/debt/tech-debt.md` if not fully addressed.

## Validation Plan
- Run backend unit tests: `cd voice_backend && ../.venv/bin/python -m pytest -q tests/business/test_business_layer.py`
- Run targeted lint checks on touched backend files: `cd voice_backend && ../.venv/bin/python -m ruff check app/business tests/business/test_business_layer.py`
- Manual file-level verification:
  - confirm no new duplicate entries are written for repeated confirmations
  - confirm invalid doctor/policy inputs do not persist bookings
  - confirm callback escalation now requests/validates phone and persists callback request

## Progress Log
- 2026-03-17: initialized plan after reviewing product/architecture/quality docs and current business-layer implementation.
- 2026-03-17: implemented strict appointment validation (policy format, doctor roster, doctor-slot checks), duplicate/slot-conflict guards, callback phone capture + normalization, and expanded backend tests.

## Decisions / Tradeoffs
- Prefer deterministic code-level validation over relying only on LLM adherence to prompts.
- Keep initial doctor inventory JSON-driven for low complexity; defer database migration.
- Normalize user-friendly phone input to a canonical stored format to balance UX and strict persistence.

## Follow-Ups / Debt
- Consider file-locking or storage abstraction if concurrent booking traffic increases beyond safe JSON read-modify-write assumptions.
- Consider separating policy validation rules by insurer if multiple formats are later required.

## Completion Summary (2026-03-17)
- Added doctor inventory data (`doctors.json`) and callback request persistence (`callback_requests.json`).
- Hardened `AppointmentFlow` to require valid ISO date, strict `POL-####` policy format, clinic-doctor membership, and doctor-slot availability.
- Added duplicate booking and slot-taken detection before writing to `appointments.json`.
- Replaced callback dead-end messaging with callback phone capture and validation in `BusinessLayer`.
- Updated sample `appointments.json` to remove invalid/duplicate seeded records.
- Updated product requirements/workflows docs for strict policy IDs, doctor-slot constraints, dedupe expectations, and callback phone handling.
- Expanded backend tests to cover the new constraints and callback path.

## Validation Performed
- `cd voice_backend && ../.venv/bin/python -m ruff check app/business tests/business/test_business_layer.py` (passed)
- `cd voice_backend && ../.venv/bin/python -m pytest -q tests/business/test_business_layer.py` (12 passed)
