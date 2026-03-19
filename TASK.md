**TASK: Build a Schema-Guided Business Layer for a Voice Insurance Agent — Appointment Booking Only**

**Context**

You are extending a Python voice agent backend. The backend has a WebSocket endpoint (`/ws/agent`) in `voice_backend/app/main.py`. User speech is transcribed by ASR, and the resulting text currently goes straight to an LLM for a reply. You must insert a business layer between ASR output and LLM/TTS.

The business layer handles exactly **one user intent: booking an appointment**. The business layer uses **Schema-Guided Reasoning (SGR)**: every LLM call must return a validated Pydantic model, never free-form text. This is enforced via Groq's structured output / JSON mode. Python 3.12 is used throughout. All models must use Pydantic v2.

**What Schema-Guided Reasoning means here**

Instead of asking the LLM "what should I do?" and parsing its text, you define a strict Pydantic model for every LLM decision, serialize it to a JSON schema, pass that schema to Groq as the required response format, and validate the parsed response with Pydantic. If validation fails, retry up to a fixed limit. This makes every LLM decision deterministic, type-safe, and testable.

---

**Project structure to create**
```
voice_backend/app/business/
├── __init__.py
├── models.py
├── session.py
├── intent.py
├── flows/
│   ├── __init__.py
│   └── appointment.py
├── data/
│   ├── appointments.json
│   └── clinics.json
└── layer.py
```

---

**Step 1 — models.py**

Define all Pydantic v2 models. Every field must use `Field(description="...")` so descriptions are included in the JSON schema sent to the LLM.

`IntentType`: a `str` enum with two values: `APPOINTMENT` and `UNCLEAR`.

`IntentResult`:
- `intent: IntentType = Field(description="The detected user intent. Use APPOINTMENT if the user wants to book, schedule, or arrange a medical appointment. Use UNCLEAR otherwise.")`
- `confidence: float = Field(description="Confidence score between 0.0 and 1.0.")`
- `extracted_entities: dict[str, str] = Field(description="Entities extracted from speech, e.g. {'date': '2025-08-01', 'clinic': 'City Clinic'}.")`
- `reasoning: str = Field(description="One-sentence justification for the chosen intent.")`

`SessionState`: a `str` enum with values `IDLE`, `AWAITING_CLARIFICATION`, `IN_FLOW`, `SCHEDULING_CALLBACK`, `COMPLETED`.

`SessionContext`: mutable dataclass or Pydantic model. Fields:
- `session_id: str = Field(description="Unique identifier for this WebSocket session.")`
- `state: SessionState = Field(default=SessionState.IDLE, description="Current state of the conversation.")`
- `intent_attempts: int = Field(default=0, description="Number of consecutive turns where intent was UNCLEAR.")`
- `pending_entities: dict[str, str] = Field(default_factory=dict, description="Entities collected so far for the active flow, e.g. date and clinic.")`
- `active_flow: str | None = Field(default=None, description="Name of the currently active flow, or None.")`
- `conversation_history: list[dict[str, str]] = Field(default_factory=list, description="List of {role, content} dicts, capped at last 10 turns, passed to every LLM call.")`

`FlowResult`:
- `response_text: str = Field(description="Text to speak aloud to the user.")`
- `completed: bool = Field(description="True if the flow has reached a terminal state (booking confirmed or aborted).")`
- `schedule_callback: bool = Field(default=False, description="True if the session should be escalated to a callback.")`
- `updated_entities: dict[str, str] = Field(default_factory=dict, description="Any new entities extracted during this flow turn.")`

`AgentResponse`:
- `text: str = Field(description="Final text to pass to TTS.")`
- `should_end_session: bool = Field(default=False, description="True if the WebSocket session should be closed after this response.")`

`AppointmentReasoning` (the SGR model for the appointment flow):
- `action: Literal["ask_date", "ask_clinic", "confirm_booking", "cancel"] = Field(description="Next action to take. ask_date: date is missing. ask_clinic: clinic is missing. confirm_booking: both date and clinic are present and confirmed by user. cancel: user wants to stop.")`
- `message_to_user: str = Field(description="The exact message to speak to the user for this action.")`
- `extracted_date: str | None = Field(default=None, description="ISO date string if a date was mentioned in this turn, else None.")`
- `extracted_clinic: str | None = Field(default=None, description="Clinic name or id if a clinic was mentioned in this turn, else None.")`

---

**Step 2 — data/ mock JSON files**

`clinics.json` — array of 3 entries. Each: `id`, `name`, `address`, `available_slots` (array of ISO date strings).

`appointments.json` — array, initially 2 entries. Each: `id`, `policyholder_id`, `clinic_id`, `date` (ISO), `reason`, `status` (`scheduled`/`cancelled`).

---

**Step 3 — session.py**

`SessionStore` class (not a singleton — instantiate once and pass around):
- `_sessions: dict[str, SessionContext]` in memory.
- `get_or_create(session_id: str) -> SessionContext`
- `update(ctx: SessionContext) -> None`
- `delete(session_id: str) -> None`

`SessionContext` is mutable. Flows update it in place; `BusinessLayer` saves it back via `session_store.update(ctx)` after every turn.

---

**Step 4 — intent.py**

`IntentDetector` class. Constructor takes a Groq client and `model_name: str`.

Async method: `detect(text: str, session_ctx: SessionContext) -> IntentResult`.

- Build a system prompt that explains the two intents (`APPOINTMENT`, `UNCLEAR`) and embeds `IntentResult.model_json_schema()` as the required response format.
- Pass the last 10 turns from `session_ctx.conversation_history` plus the new utterance.
- Use Groq's `response_format` / JSON mode.
- Parse and validate with `IntentResult.model_validate(parsed_json)`.
- On validation failure, retry once with an error-correction prompt.
- On second failure, return `IntentResult(intent=IntentType.UNCLEAR, confidence=0.0, extracted_entities={}, reasoning="parse failure")`.

---

**Step 5 — flows/appointment.py**

`AppointmentFlow` class. Constructor loads `clinics.json` once.

Async method: `execute(text: str, session_ctx: SessionContext, groq_client, model_name: str) -> FlowResult`.

Logic:
1. Merge any entities from `session_ctx.pending_entities` with whatever the LLM extracts this turn.
2. Call Groq with `AppointmentReasoning.model_json_schema()` as required response format. System prompt includes the list of available clinics and slots, current collected entities, and conversation history (last 10 turns).
3. Validate response as `AppointmentReasoning`.
4. Merge `extracted_date` and `extracted_clinic` into `session_ctx.pending_entities` (mutate in place).
5. On `action == "confirm_booking"`: write a new entry to `appointments.json` (read → append → write). Return `FlowResult(response_text=reasoning.message_to_user, completed=True)`.
6. On `action == "cancel"`: return `FlowResult(response_text=reasoning.message_to_user, completed=True)`.
7. On `ask_date` or `ask_clinic`: return `FlowResult(response_text=reasoning.message_to_user, completed=False)`.

Writing to `appointments.json`: read current contents with `json.loads(Path(...).read_text())`, append new record, write back with `Path(...).write_text(json.dumps(..., indent=2))`. No async I/O needed.

---

**Step 6 — layer.py**

`BusinessLayer` class. Constructor takes: `groq_client`, `model_name: str`, `session_store: SessionStore`. Instantiates `IntentDetector` and `AppointmentFlow` once.

Main async method: `process(text: str, session_id: str) -> AgentResponse`.

Logic:

1. `ctx = session_store.get_or_create(session_id)` — `ctx` is mutable.
2. Append `{"role": "user", "content": text}` to `ctx.conversation_history`. Cap history at last 10 entries.
3. Branch on `ctx.state`:

   **IDLE or COMPLETED**:
   - Run `IntentDetector.detect(text, ctx)`.
   - If `UNCLEAR`: increment `ctx.intent_attempts`. If >= 3, return `AgentResponse(text="I'm having trouble understanding. Let me arrange a callback for you.")` and set `ctx.state = SessionState.SCHEDULING_CALLBACK`. Otherwise set `ctx.state = SessionState.AWAITING_CLARIFICATION` and return a clarification prompt.
   - If `APPOINTMENT`: reset `ctx.intent_attempts = 0`, store any `extracted_entities` into `ctx.pending_entities`, set `ctx.active_flow = "appointment"`, set `ctx.state = SessionState.IN_FLOW`, fall through to flow execution.

   **AWAITING_CLARIFICATION**:
   - Run `IntentDetector.detect(text, ctx)`.
   - If still `UNCLEAR`: increment `ctx.intent_attempts`. Same escalation logic as above.
   - If `APPOINTMENT`: reset, set state to `IN_FLOW`, fall through to flow execution.

   **IN_FLOW**:
   - Call `AppointmentFlow.execute(text, ctx, groq_client, model_name)`.
   - If `result.completed`: set `ctx.state = SessionState.COMPLETED`.
   - Build `AgentResponse(text=result.response_text)`.

4. After every branch: append `{"role": "assistant", "content": response_text}` to `ctx.conversation_history`. Call `session_store.update(ctx)`. Return the `AgentResponse`.

---

**Step 7 — Wiring into WebSocket**

In `voice_backend/app/main.py`:

- Instantiate `SessionStore` once at module level.
- Create a Groq client at startup.
- Instantiate `BusinessLayer(groq_client, model_name, session_store)` at module level.

Inside `ws_agent`:
- Derive `session_id = str(id(ws))`.
- In `on_asr_final(text)`: call `response = await business_layer.process(text, session_id)`. Pass `response.text` directly to the TTS pipeline, bypassing `BasetenChat.stream_reply`.
- On WebSocket disconnect: call `session_store.delete(session_id)`.

---

**Constraints**

- Every LLM call must return a validated Pydantic model. No string parsing of LLM output outside the structured output path.
- All Pydantic v2 syntax: `model_validate`, `model_json_schema`, `Field(description="...")` on every field.
- `SessionContext` is mutable. Flows update it in place; `BusinessLayer` persists it via `session_store.update(ctx)` after every turn.
- `conversation_history` is capped at 10 turns and passed to every LLM call.
- JSON files are the only persistence. `clinics.json` is read once at startup. `appointments.json` is read and written on each booking.
- No async file I/O: use `Path(...).read_text()` / `Path(...).write_text(...)`.
- Flow classes and `IntentDetector` accept the Groq client and model name as arguments; they do not create them internally.