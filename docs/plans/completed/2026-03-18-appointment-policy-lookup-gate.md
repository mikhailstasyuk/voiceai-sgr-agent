# Appointment Policy Lookup Gate

## Goal
Prevent appointment flow from progressing when a policy id is correctly formatted but missing from persisted policyholder records.

## Context
- Latest logs showed appointment intake accepted `POL-1004` and proceeded to clinic selection.
- Renewal/clinic-change flows already enforce persistence lookup for policy ids.

## Steps
1. Add policy-store-backed existence check to appointment policy gate.
2. Return targeted unknown-policy retry message and clear invalid policy entity.
3. Add regression tests for unknown policy id rejection and keep valid-id booking paths green.
4. Update product and architecture docs to codify appointment policy existence gating.

## Validation
- `../.venv/bin/python -m pytest -q tests/business/test_business_layer.py`
- `../.venv/bin/python -m ruff check app/business tests/business/test_business_layer.py`

## Outcome
- Appointment flow policy gate now validates both policy id format and policyholder existence in persistence.
- Unknown policy ids are rejected with targeted retry messaging and removed from pending entities.
- Added regression coverage for unknown policy id handling.
- Updated product and architecture docs to reflect appointment policy existence gating.

## Validation Results
- `../.venv/bin/python -m pytest -q tests/business/test_business_layer.py` -> `47 passed`
- `../.venv/bin/python -m ruff check app/business tests/business/test_business_layer.py` -> `All checks passed` (with existing pyproject warning)
