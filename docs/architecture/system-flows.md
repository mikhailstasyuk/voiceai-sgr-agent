# System Flows

## Voice Turn Flow
1. Client opens `/ws/agent`.
2. Client sends `start`; backend initializes ASR/LLM/TTS session.
3. Client streams PCM bytes.
4. ASR emits transcript/VAD events.
5. Backend forwards transcript events to client and sends ASR-final text to `BusinessLayer.process`.
6. Business layer runs onboarding gate, strict-schema intent detection, and service flow decisions.
7. Business layer returns final assistant text; backend streams TTS audio chunks to client.
8. Client plays audio while updating UI status/transcript.
9. `stop` or disconnect closes session resources and clears session context.

## Business Decision Flow
1. `SessionStore` loads or creates mutable `SessionContext` for websocket `session_id`.
2. On first turn, onboarding gate greets the caller, asks "Are you our client?", and routes non-clients to plan info/callback paths.
3. `IntentDetector` classifies `APPOINTMENT`, `POLICY_RENEWAL`, `PLAN_INQUIRY`, `CALLBACK_SUPPORT`, or `UNCLEAR` using structured output validation.
4. On `UNCLEAR`, system asks clarification and escalates to callback messaging after repeated failures.
5. On `APPOINTMENT`, `AppointmentFlow` collects required entities (`date`, `clinic`, `policy_id`, `doctor_name`) and validates them before confirmation.
5.1. Appointment entity linking uses strict schema-guided option selection with canonical identifiers for clinic/doctor/date.
5.1.1 Clinic selection uses clinic roster and is not blocked by slot availability.
5.1.2 Slot availability checks are applied when doctor/date are selected and at booking confirmation.
5.1.3. Policy id collection/validation is gating-first: flow does not progress to clinic/doctor/date confirmation until policy id is valid.
5.1.4. Appointment policy gate validates both strict format and policyholder existence in persistence.
5.1.5. Policy id capture uses schema-guided candidate selection and can assemble split-turn `POL` prefix + 4 digits across up to 3 recent user turns.
5.1.5.1 Candidate-id schema fields use closed enums with explicit `__NONE__` sentinel when no candidate is selected.
5.1.5.2 Appointment canonical selection fields (`selected_clinic_id`, `selected_doctor_id`, `selected_date`) use closed enums with explicit `__NONE__` sentinel at provider boundary.
5.1.6. Bare 4-digit policy utterances are accepted only when recent `POL` prefix context exists.
5.2. Doctor selection requires explicit user confirmation before booking can complete.
5.3. If user asks for earliest availability, appointment flow can return earliest doctor/date options across clinics before explicit selection.
6. On `POLICY_RENEWAL`, renewal flow validates policy id, lists plan options, and applies renewal only after explicit confirmation (including optional plan switch).
6.1. Renewal flow can answer current-plan questions inline (`explain_current_plan`) and then continue plan-selection for renewal.
7. On `PLAN_INQUIRY`, inquiry flow lists plan grid and compares current plan when policy id is known.
7.1. If current-plan comparison is requested without policy id, inquiry flow asks for policy id before comparison.
7.2. Inquiry flow can capture selected target plan and hand off to renewal while preserving that selected plan in pending entities.
8. On `CALLBACK_SUPPORT`, callback-support flow handles status/explanation/reschedule decisions against callback request records.
8.1. Non-appointment flows can emit an SGR `handoff_intent` action; business layer resolves a canonical requested flow (including direct handoff to appointment) before fallback reroute.
8.2. For non-appointment flows, `handoff_intent`/`cancel` actions are applied before policy/eligibility/list fallback branches so SGR action intent is not overridden by missing-entity gates.
8.3. If `handoff_intent` resolves to the same active flow, the flow stays in-place (no reroute marker) to avoid self-handoff loops.
8.4. Business layer applies a shared stall guard keyed by flow progress markers (not raw response text) and emits a recovery prompt to avoid conversational deadlocks.
9. Policy lifecycle boundary validates strict policy format (`POL-####`) and computes renewal due date from `last_renewal_date` or `policy_start_date` (+365 days).
10. When policy id is known and due date is within 30 days, business layer prepends proactive expiry-soon notice once per session.
11. Appointment validation enforces clinic doctor-roster membership and selected date availability in the chosen doctor's slots.
12. Before persistence, appointment flow checks duplicate scheduled bookings and doctor/date slot conflicts.
13. When users are unsure about clinic/doctor selection, appointment flow can return `list_clinics` / `list_doctors`.
14. On validation failures, flows return field-specific recovery prompts.
15. Structured-output provider validation failures are handled with retry + safe fallback so a turn cannot crash the session.
16. On repeated unclear intent escalation, callback path asks for a Georgian mobile number, normalizes/validates it, asks for callback date, confirms both, and persists callback requests to `app/business/data/callback_requests.json`.
16.1. Callback reasoning schema is mode-scoped (`collect_phone`, `confirm_phone`, `collect_date`, `confirm_date`, `confirm_exit`) so invalid action transitions are prevented at the schema boundary.
16.1.1 Callback candidate selection uses closed enums with `__NONE__` sentinel when no phone candidate is selected.
16.2. Callback capture normalizes to `+995#########`, deduplicates repeated `+995` mentions during capture, then asks for explicit confirmation before callback persistence.
17. If policy is required but user repeatedly indicates no policy/not-client, flow hands off to non-client onboarding options instead of looping on strict policy format prompts.
18. If a caller later states they are not a client after onboarding completion, business layer runs onboarding reasoning for client-status correction and can re-enter non-client onboarding stage.

## Barge-In Flow
1. ASR signals speech begin or utterance begin.
2. Backend invokes session interrupt.
3. Current assistant audio/token generation is interrupted.
4. New user utterance continues as the active turn.

## Failure Handling Flow
1. Runtime/provider exception occurs.
2. Backend logs and emits status/error event.
3. Session cleanup executes in `finally` path.
4. Client transitions to `error` or `idle` state depending on closure reason.
