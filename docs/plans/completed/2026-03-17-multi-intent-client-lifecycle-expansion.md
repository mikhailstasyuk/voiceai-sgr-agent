# Multi-Intent Client Lifecycle Expansion

## Goal
Expand business logic from appointment-only to multi-intent service handling with client onboarding gate, annual policy renewal support, expiry-soon notices, plan inquiry, and clinic change.

## Context
- Product now requires free-form routing across appointments, policy renewals, plan inquiry, and clinic change.
- First interaction must ask "Are you our client?" with non-client callback and plan-info paths.
- Renewal and clinic-change eligibility depend on persisted policy lifecycle dates.

## Assumptions
- Renewal due window is 365 days.
- Expiry-soon notice threshold is 30 days.
- Plan prices are fixed: cheap 29, intermediate 59, expensive 99 USD per month.
- Policy storage is current-state oriented (not event-sourced).

## Constraints
- Keep schema-guided intent and flow decisions with strict JSON validation.
- Preserve existing appointment and callback reliability behavior.
- Keep changes in business layer boundaries.

## Implementation Steps
1. Add persisted mock policy/plans data and reusable policy store helpers.
2. Extend intent model/router and add flows for renewal, plan inquiry, and clinic change.
3. Add onboarding gate and expiry-soon policy notices in BusinessLayer.
4. Update product and architecture docs to new behavior.
5. Extend backend tests for onboarding, routing, and lifecycle notices.

## Validation Plan
- `cd voice_backend && ../.venv/bin/python -m pytest -q tests/business/test_business_layer.py`
- `cd voice_backend && ../.venv/bin/python -m ruff check app/business tests/business/test_business_layer.py`

## Progress Log
- 2026-03-17: initialized plan and started implementation edits.

## Decisions / Tradeoffs
- Use deterministic lifecycle calculations (`last_renewal_date`/`policy_start_date`, `last_clinic_change_date`) at boundary level.
- Use SGR structured output for each service flow while keeping deterministic persistence guards.

## Follow-Ups / Debt
- Consider adding event history for compliance/audit if product scope expands beyond mock data.

## Outcome Notes
- 2026-03-17: implemented onboarding gate, multi-intent routing, renewal/plan/clinic flows, lifecycle data store, and expiry-soon notices.
- Updated docs baseline for product/workflow/architecture to reflect new business scope.
- Validation passed:
  - `cd voice_backend && ../.venv/bin/python -m pytest -q tests/business/test_business_layer.py` (32 passed)
  - `cd voice_backend && ../.venv/bin/python -m ruff check app/business tests/business/test_business_layer.py` (passed, existing `pyproject.toml` deprecation warning only)
