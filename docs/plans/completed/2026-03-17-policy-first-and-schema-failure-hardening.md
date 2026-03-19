# Policy-First Intake and Schema-Failure Hardening

## Goal
Fix live appointment-flow failures by preventing strict-schema provider errors from crashing turns, and enforce policy-id-first intake ordering.

## Context
- Latest websocket session (`ws_20260317T145434Z_137190000805120.log`) crashed on provider-side schema validation when `selected_date` was emitted before doctor selection.
- Conversation behavior asked for date first and looped, instead of collecting policy id first.

## Implementation Steps
1. Hardened appointment reasoning retry path to catch provider errors and return non-crashing fallback reasoning.
2. Added selection sanitization so out-of-context `selected_*` values are dropped before state merge.
3. Added policy-id-first gating response in appointment flow execution.
4. Added regression tests for policy-first behavior and provider schema-failure resilience.
5. Updated product/workflow/architecture docs to codify policy-first ordering and graceful schema-failure fallback.

## Validation Performed
- `cd voice_backend && ../.venv/bin/python -m pytest -q tests/business/test_business_layer.py`
- `cd voice_backend && ../.venv/bin/python -m ruff check app/business tests/business/test_business_layer.py`

## Completion Summary
- Appointment turn no longer crashes on provider json-schema failures in reasoning calls.
- Policy id is now requested/validated before progressing through other appointment collection steps.
- Added explicit regression coverage for both behaviors.
