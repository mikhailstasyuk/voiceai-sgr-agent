# System Flows

## Voice Turn Flow
1. Client opens `/ws/agent`.
2. Client sends `start`; backend initializes ASR/LLM/TTS session.
3. Client streams PCM bytes.
4. ASR emits transcript/VAD events.
5. Backend forwards transcript events to client and sends ASR-final text to `BusinessLayer.process`.
6. Business layer runs strict-schema intent detection and appointment flow decisions.
7. Business layer returns final assistant text; backend streams TTS audio chunks to client.
8. Client plays audio while updating UI status/transcript.
9. `stop` or disconnect closes session resources and clears session context.

## Business Decision Flow
1. `SessionStore` loads or creates mutable `SessionContext` for websocket `session_id`.
2. `IntentDetector` classifies `APPOINTMENT` or `UNCLEAR` using structured output validation.
3. On `UNCLEAR`, system asks clarification and escalates to callback messaging after repeated failures.
4. On `APPOINTMENT`, `AppointmentFlow` collects required entities (`date`, `clinic`, `policy_id`, `doctor_name`).
5. On confirmation, flow appends a booking record to `app/business/data/appointments.json`.

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
