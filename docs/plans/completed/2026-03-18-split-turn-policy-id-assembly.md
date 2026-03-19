# Split-Turn Policy ID Assembly

## Goal
Fix appointment policy-id capture so callers can say `POL` prefix and the 4 digits across adjacent turns.

## Context
- Latest logs showed repeated reprompts when caller said `P O L` and then digits (`1005`) separately.
- Current normalization handles spoken expansions in one turn but does not assemble across turns.

## Steps
1. Add prefix-context detection from recent user turns in appointment flow.
2. Assemble `POL-####` when extracted policy token is 4 digits and prefix context exists.
3. Preserve strict behavior without prefix context.
4. Keep persistence lookup enforcement for assembled ids.
5. Add regressions for split-turn success, no-prefix guard, and unknown assembled policy.
6. Update docs for split-turn policy capture behavior.

## Validation
- `../.venv/bin/python -m pytest -q tests/business/test_business_layer.py`
- `../.venv/bin/python -m ruff check app/business tests/business/test_business_layer.py`

## Outcome
- Appointment flow now detects recent user policy-prefix context (`POL`/`P O L`) and can assemble `POL-####` from a subsequent 4-digit extraction.
- Split-turn assembly remains guarded: standalone 4 digits without prefix context are not accepted as policy id.
- Assembled IDs still pass through persistence lookup; unknown assembled IDs are rejected with targeted retry messaging.
- Added regressions for split-turn success, no-prefix guard, and unknown assembled policy handling.
- Updated product and architecture docs for split-turn policy capture behavior.

## Validation Results
- `../.venv/bin/python -m pytest -q tests/business/test_business_layer.py` -> `50 passed`
- `../.venv/bin/python -m ruff check app/business tests/business/test_business_layer.py` -> `All checks passed` (with existing pyproject warning)
