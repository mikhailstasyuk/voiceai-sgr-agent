# SGR Strict Entity Linking Rewrite

## Goal
Replace name-text matching loops in appointment booking with strict schema-guided option selection for clinic/doctor/date, and require explicit doctor confirmation before booking.

## Context
- Logs showed repeated doctor-name retries when ASR produced pronunciation variants (for example, `Nuan` vs `Nguyen`).
- Flow messaging could contradict real slot availability because prompt context was static while persistence checks were conflict-aware.

## Assumptions
- Deterministic boundary checks (duplicate/slot conflict/policy format) remain mandatory.
- No frontend API contract changes are required.

## Constraints
- Keep changes inside business layer boundaries.
- Preserve strict schema validation and retry behavior.

## Implementation Steps
1. Extend appointment reasoning schema/model with canonical option outputs (`selected_clinic_id`, `selected_doctor_id`, `selected_date`) and doctor confirmation signal.
2. Build dynamic strict schema each turn so canonical selection fields are constrained to currently available options.
3. Migrate appointment flow state handling to canonical IDs (`clinic_id`, `doctor_id`) and confirmation state.
4. Compute effective doctor availability from doctor inventory minus scheduled bookings and reuse it in prompt context, listing, and validation.
5. Add doctor-confirmation gate and targeted fully-booked fallback that suggests alternative doctors with open slots.
6. Expand backend tests for strict selection, confirmation behavior, and effective-availability conflict behavior.
7. Update product and architecture docs for new behavior.

## Validation Plan
- `cd voice_backend && ../.venv/bin/python -m pytest -q tests/business/test_business_layer.py`
- `cd voice_backend && ../.venv/bin/python -m ruff check app/business tests/business/test_business_layer.py`

## Decisions / Tradeoffs
- Keep deterministic persistence guards while moving entity linking decisions into strict SGR option selection.
- Doctor choice now requires explicit confirmation before booking can complete.
- Effective slot filtering is applied during schema context construction to avoid contradictory availability prompts.

## Follow-Ups
- Add focused websocket-level integration tests using captured conversation logs.
- Consider alias metadata in doctor inventory for richer display prompts, while keeping canonical ID selection as source of truth.

## Completion Summary (2026-03-17)
- Added canonical SGR selection fields for clinic/doctor/date and doctor confirmation signal.
- Reworked appointment flow to use ID-based entity state and dynamic strict schema options.
- Added confirmation gate for doctor choices and conflict-aware effective availability in context/prompting.
- Added regression coverage for strict doctor selection, confirmation gating, and fully-booked alternative suggestions.
- Updated product and architecture docs for strict option linking and confirmation behavior.

## Validation Performed
- `cd voice_backend && ../.venv/bin/python -m pytest -q tests/business/test_business_layer.py`
- `cd voice_backend && ../.venv/bin/python -m ruff check app/business tests/business/test_business_layer.py`
