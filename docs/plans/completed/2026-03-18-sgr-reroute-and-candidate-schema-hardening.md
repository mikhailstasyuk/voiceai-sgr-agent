# SGR Reroute and Candidate Schema Hardening

## Goal
Remove recurring strict-schema failures and flow lock loops while preserving SGR-first conversational control and deterministic boundary validation.

## Context
- Latest session logs showed repeated provider `json_validate_failed` errors on `selected_policy_candidate_id` despite valid candidate context.
- Logs also showed lock behavior in active non-appointment flows (repeated same response with no reroute path).
- A caller correction "I'm not your client" during completed/idle state was not re-entering non-client onboarding.

## Decisions
1. Candidate-selection strict schema fields now use closed enums plus `__NONE__` sentinel (no nullable union contract for provider-facing selection fields).
2. Candidate resolution sanitizes model-selected IDs against provided candidates; unknown or sentinel values map to `None`.
3. Policy candidate construction is suppressed once a verified policy is already captured, unless current turn signals policy correction.
4. Added `handoff_intent` action for non-appointment service flows and business-layer reroute sentinel handling.
5. Added client-status correction in completed onboarding state using onboarding SGR reasoning when client-status language is detected.
6. Removed unconditional restore of stale booking context for non-appointment intents; added flow-scoped pending-entity pruning.

## Validation
- `PYTHONPATH=. pytest tests/business/test_business_layer.py` (61 passed)
- `python -m compileall app/business tests/business/test_business_layer.py`

## Outcome
- Provider-facing candidate-selection contracts are stricter and explicit.
- Non-appointment flows can pivot back to intent routing without dead-end repetition.
- Mid-session client-status correction re-enters non-client onboarding path.
- Session entity carryover between unrelated flows is reduced.
