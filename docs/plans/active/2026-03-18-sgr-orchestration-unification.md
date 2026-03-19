# SGR Orchestration Unification

## Goal
Stabilize UX by removing split flow-routing logic and enforcing one schema-guided transition path across appointment, renewal, plan inquiry, and callback support.

## Context
Recent logs showed repeated prompts, missed handoffs, and inconsistent behavior when flows emitted `requested_flow`/`handoff_intent` decisions. Structured output parsing and handoff mapping were duplicated per flow.

## Assumptions
- Clinic-change flow is intentionally deprecated from active routing for the current product scope.
- SGR remains the primary source of action/selection decisions.
- Deterministic logic is boundary-only (validation, normalization, side-effect gating).

## Constraints
- Keep compatibility with current tests and persistence files.
- Preserve strict schema-output behavior.

## Implementation Steps
1. Add shared SGR runtime utility for structured JSON parsing and handoff normalization.
2. Route all flow and intent structured calls through the shared utility.
3. Centralize requested-flow transition handling in business layer with a single normalization path.
4. Add flow-stall guard in business layer to break repeated-response loops.
5. Tighten appointment selection schema to closed enums with `__NONE__` sentinel when options are known/unknown.
6. Add/adjust tests for appointment handoff routing, strict selection schema behavior, stall recovery, onboarding greeting, and callback phone+date confirmation.

## Validation Plan
- Run business-layer and flow tests covering handoff transitions and schema behavior.
- Run focused regression tests for repeated-response loop handling.

## Decisions
- Include `APPOINTMENT` in shared handoff mapping so non-appointment flows can hand off directly without intent reclassification.
- Drop stale/invalid `requested_flow` hints centrally to avoid persistent stale transition state.
- Trigger stall recovery only after repeated identical assistant responses without entity progress.
- Callback persistence now requires two confirmed structured steps: normalized phone first, then callback date.

## Follow-Ups
- Evaluate moving remaining mode-specific callback transitions into the same arbiter contract.
- Add telemetry counters for normalized/ignored requested-flow hints.

## Progress Log
- 2026-03-18: Implemented clinic-availability loop hardening in appointment flow:
  - clinic roster is no longer filtered by open slots at clinic-selection stage
  - deterministic guards handle clinic/doctor no-open-date states
  - appointment flow now emits progress keys broadly for stall detection
- 2026-03-18: Implemented progress-key-aware stall recovery messaging in business layer for appointment steps.
- 2026-03-18: Updated canonical product/architecture docs to reflect clinic-vs-date availability boundaries and callback mode stages.
- 2026-03-18: Switched appointment provider-facing selection fields (`selected_clinic_id`, `selected_doctor_id`, `selected_date`) to closed enums with `__NONE__` sentinel and added earliest-availability action path.
