# Georgian Callback Capture and SGR Candidate Selection

## Goal
Move callback capture to Georgian mobile format and remove `+1`/US assumptions while keeping schema-guided reasoning as the decision boundary.

## Context
- Recent logs show callback phone failures from duplicated country code handling and structured-output provider failures during callback turns.
- Product direction now requires Georgian mobile capture (`+995` + 9 local digits), with caller confirmation before persistence.

## Assumptions
- Callback capture is Georgian-only in this iteration.
- Valid Georgian mobile local part is exactly 9 digits and starts with `5`.
- Existing callback records do not require migration.

## Constraints
- Keep callback flow SGR-first for conversational decisions.
- Do not reintroduce exact-match deterministic branching for intent/control decisions.
- Keep persistence boundary deterministic and validated.

## Steps
1. Update callback reasoning schema/model to include schema-guided candidate selection (`selected_phone_candidate_id`).
2. Replace US normalization with Georgian mobile normalization and country-code dedup in callback digit utilities.
3. Integrate per-turn callback digit candidates (raw/extracted) and apply model-selected candidate with deterministic fallback.
4. Harden callback reasoning fallback path to tolerate provider structured-output failures without dropping digit capture turns.
5. Update callback prompts in onboarding, unclear-intent escalation, and callback-support flow to request Georgian mobile numbers.
6. Update docs (`product/requirements`, `product/user-workflows`, `architecture/system-flows`).
7. Update/add backend tests for Georgian normalization, `+995` dedup, and provider-failure resilience.

## Validation Plan
- Run backend callback/business tests:
  - `../.venv/bin/python -m pytest -q tests/business/test_business_layer.py`
- Run lint for changed backend files:
  - `../.venv/bin/python -m ruff check app/business tests/business/test_business_layer.py`

## Decisions
- Use SGR candidate id selection for phone capture candidate resolution.
- Confirm captured normalized number before callback persistence.

## Outcome
- Implemented Georgian mobile callback capture (`+995` + 9 local digits starting with `5`) in business-layer callback path.
- Added `CallbackReasoning.selected_phone_candidate_id` and mode-scoped schema guidance for phone candidate selection.
- Added callback candidate construction with `+995` dedup and deterministic fallback scoring.
- Hardened callback reasoning fallback to handle provider/runtime structured-output errors without crashing the turn.
- Updated callback prompts in onboarding/unclear escalation/callback-support to request Georgian mobile numbers.
- Updated product/workflow/architecture docs to reflect Georgian normalization and confirm-before-persist behavior.
- Added regression tests for repeated `+995` dedup and provider failure resilience.

## Validation Results
- `../.venv/bin/python -m pytest -q tests/business/test_business_layer.py` -> `45 passed`
- `../.venv/bin/python -m ruff check app/business tests/business/test_business_layer.py` -> `All checks passed` (with existing pyproject deprecation warning)
