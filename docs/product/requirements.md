# Product Requirements (Current Baseline)

## Functional Requirements
- The backend exposes a health endpoint and a voice agent WebSocket endpoint.
- The client can establish a realtime session and stream microphone audio.
- The system emits user transcript updates to the client.
- The system streams assistant audio output back to the client.
- Assistant reply generation must pass through a business layer between ASR-final text and TTS.
- The business layer supports service intents: appointment booking, policy renewal, and plan inquiry.
- The business layer also supports callback-support intent (callback status, explanation, and rescheduling requests).
- At session start, assistant must greet the caller and ask: "Are you our client?"
- If caller is not a client, assistant must offer either (a) plan info then callback scheduling, or (b) callback scheduling immediately.
- Intent detection and business flow decisions must use strict schema-guided model outputs validated by Pydantic v2 models.
- Appointment confirmation requires date, clinic, policy id, and doctor name.
- Appointment intake must request and validate policy id before progressing to clinic/doctor/date collection unless a valid policy id is already captured.
- For appointments, a policy id is valid only if it both matches `POL-####` format and exists in persisted policyholder data.
- During policy-id capture, assistant should use schema-guided policy candidate selection and accept spoken `POL` ids with or without explicitly saying `dash`.
- Policy candidate selection fields in strict schemas should use closed choice sets with an explicit `__NONE__` sentinel when no candidate is selected.
- Appointment selection fields (`selected_clinic_id`, `selected_doctor_id`, `selected_date`) in strict schemas should use closed choice sets with explicit `__NONE__` sentinel when no canonical option is selected.
- During appointment policy-id capture, if caller provides `POL` prefix and 4 digits across up to the recent 3 user turns, assistant should assemble and validate `POL-####`.
- Bare 4-digit utterances must not be accepted as policy id unless recent `POL` prefix context exists.
- If user repeatedly indicates they do not have a policy id or are not a client during client-only flows, assistant must stop strict policy-id looping and hand off to non-client options (plan info + callback choices).
- Appointment entity linking should prefer canonical option selection (clinic/doctor/date) from strict schema-constrained choices rather than free-form text matching.
- Clinic selection must use clinic roster only and must not be blocked by current slot availability.
- Doctor/date availability checks must be enforced when doctor is selected and when date is proposed/confirmed.
- Doctor selection must be explicitly confirmed by the user before booking confirmation can complete.
- Policy IDs must match strict format `POL-####` before booking can be confirmed.
- Policy lifecycle must track `policy_start_date` and `last_renewal_date`; policy renewal is due every 365 days from last renewal (or start date when no renewal exists).
- If a valid policy id is known and renewal due date is within 30 days, assistant should proactively notify user that policy expires soon.
- Policy renewal flow must support renewing with current plan or switching to another available plan in the same confirmed action.
- Renewal persistence must require explicit caller confirmation for the selected plan/policy pair before writing updates.
- During policy renewal, if caller asks which plan they are currently on, assistant should answer from policy data and continue renewal without forcing a flow reset.
- Plan inquiry flow must provide the plan grid with monthly USD prices and support comparing with current plan when policy id is available.
- If caller asks for current-plan details without a known policy id, plan inquiry must ask for policy id before comparison.
- For "change/switch/select plan" requests, intent should route into plan inquiry first, and plan inquiry should hand off to renewal after plan selection.
- Doctor selection must come from a predefined clinic doctor roster and selected date must be in that doctor's available slots.
- Booking persistence must prevent duplicate scheduled appointments and reject already-taken doctor/date slots.
- Availability shown to users must be conflict-aware (already-booked slots filtered out from offered options).
- Structured-output/schema generation failures in appointment flow must degrade gracefully (targeted retry/fallback prompt) without crashing the active session turn.
- User-facing responses should present dates in human-readable format while internal persistence remains ISO format.
- After three consecutive unclear intent detections, the assistant must escalate to callback scheduling messaging.
- Callback scheduling must collect and validate a callable phone number before completion.
- Callback scheduling must target Georgian mobile numbers and normalize to `+995#########` format.
- Callback scheduling must deduplicate repeated `+995` country code mentions while capturing local digits.
- Callback scheduling finalization should occur only after the caller confirms the captured normalized number.
- Callback scheduling must collect callback date and persist callback records only after explicit date confirmation.
- During callback phone collection, users must be able to switch back to appointment booking with explicit confirmation.
- During callback phone collection, generic negative/noisy utterances must not trigger booking-switch prompts unless user explicitly requests switching services.
- While inside renewal/plan/callback-support flows, users must be able to pivot to another service; flow reasoning should trigger an intent reroute instead of repeating the same flow response.
- In non-appointment flows, structured `handoff_intent`/`cancel` actions must be honored before missing-entity fallback prompts or eligibility checks.
- `handoff_intent` normalization must be centralized so all flows use the same target-flow map (including `APPOINTMENT`) and self-handoffs are ignored consistently.
- Business layer must detect repeated no-progress responses in an active flow and return a recovery prompt instead of looping indefinitely.
- If SGR selects a new policy candidate while an invalid/stale policy id exists in state, system should replace the stale value instead of preserving it.
- In completed sessions, callback status/explanation questions should route to callback-support behavior instead of appointment booking policy gates.
- If a caller previously confirmed as client later states they are not a client, assistant should re-enter non-client onboarding options.
- For clinic/doctor selection uncertainty, assistant should be able to list available options without requiring a direct options request.
- If caller asks for earliest availability, appointment flow should provide earliest doctor/date options across clinics and then ask for explicit clinic/doctor selection.
- Validation failures during appointment confirmation must return targeted next-step prompts instead of a generic dead-end message.
- The session supports barge-in behavior when user speech resumes.
- The client supports start/stop controls and clear status transitions.

## Non-Functional Requirements
- Prioritize low-latency turn response.
- Keep runtime/provider behavior configurable through environment settings.
- Maintain compatibility with local development setup documented in root `README.md`.

## Documentation Requirements
- Any user-visible behavior change must update this file or `user-workflows.md`.
- Any architecture boundary change must update `docs/architecture/`.
