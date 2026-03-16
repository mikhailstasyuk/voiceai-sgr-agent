import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.business.flows.appointment import AppointmentFlow
from app.business.intent import IntentDetector
from app.business.layer import BusinessLayer
from app.business.models import FlowResult, IntentResult, IntentType, SessionState
from app.business.schema_utils import to_groq_strict_schema
from app.business.session import SessionStore


class FakeCompletions:
    def __init__(self, payloads):
        self._payloads = list(payloads)

    async def create(self, **kwargs):
        if not self._payloads:
            raise RuntimeError("No fake payloads left")
        payload = self._payloads.pop(0)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=payload),
                )
            ]
        )


class FakeGroqClient:
    def __init__(self, payloads):
        self.chat = SimpleNamespace(completions=FakeCompletions(payloads))


@pytest.mark.asyncio
async def test_intent_detector_retry_success():
    detector = IntentDetector(
        groq_client=FakeGroqClient([
            "{not-json}",
            json.dumps(
                {
                    "intent": "APPOINTMENT",
                    "confidence": 0.9,
                    "extracted_entities": {"date": "2026-03-22"},
                    "reasoning": "User asked to book a doctor visit.",
                }
            ),
        ]),
        model_name="fake-model",
    )
    store = SessionStore()
    ctx = store.get_or_create("s1")

    result = await detector.detect("Book me for March 22", ctx)

    assert result.intent == IntentType.APPOINTMENT
    assert result.extracted_entities["date"] == "2026-03-22"


@pytest.mark.asyncio
async def test_intent_detector_fallback_to_unclear_on_double_failure():
    detector = IntentDetector(
        groq_client=FakeGroqClient(["oops", "still-bad"]),
        model_name="fake-model",
    )
    store = SessionStore()
    ctx = store.get_or_create("s2")

    result = await detector.detect("Hello?", ctx)

    assert result.intent == IntentType.UNCLEAR
    assert result.confidence == 0.0
    assert result.reasoning == "parse failure"


def test_intent_detector_schema_is_groq_strict_compatible():
    detector = IntentDetector(groq_client=FakeGroqClient([]), model_name="fake-model")
    schema = detector._intent_result_schema()
    extracted = schema["properties"]["extracted_entities"]
    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == ["confidence", "extracted_entities", "intent", "reasoning"]
    assert extracted["type"] == "object"
    assert extracted["additionalProperties"] is False
    assert sorted(extracted["required"]) == ["clinic", "date", "doctor_name", "policy_id"]
    assert sorted(extracted["properties"].keys()) == ["clinic", "date", "doctor_name", "policy_id"]


def test_to_groq_strict_schema_applies_root_and_nested_object_rules():
    raw_schema = {
        "type": "object",
        "properties": {
            "outer": {
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                },
            }
        },
    }
    strict_schema = to_groq_strict_schema(raw_schema)
    assert strict_schema["additionalProperties"] is False
    assert strict_schema["required"] == ["outer"]
    assert strict_schema["properties"]["outer"]["additionalProperties"] is False
    assert strict_schema["properties"]["outer"]["required"] == ["value"]


@pytest.mark.asyncio
async def test_appointment_flow_confirm_booking_appends_record(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "clinics.json").write_text(
        json.dumps(
            [
                {
                    "id": "clinic_001",
                    "name": "City Clinic",
                    "address": "123 Main St",
                    "available_slots": ["2026-03-20"],
                }
            ]
        ),
        encoding="utf-8",
    )
    (data_dir / "appointments.json").write_text(
        json.dumps(
            [
                {
                    "id": "appt_1",
                    "policyholder_id": "ph_1",
                    "clinic_id": "clinic_001",
                    "date": "2026-03-20",
                    "reason": "Existing",
                    "doctor_name": "Patel",
                    "status": "scheduled",
                }
            ]
        ),
        encoding="utf-8",
    )

    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s3")
    ctx.pending_entities = {
        "date": "2026-03-21",
        "clinic": "City Clinic",
        "policy_id": "PH-777",
        "doctor_name": "Patel",
    }

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "confirm_booking",
                    "message_to_user": "Your appointment is booked.",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                }
            )
        ]
    )

    result = await flow.execute("yes confirm", ctx, client, "fake-model")

    assert result.completed is True
    appointments = json.loads((data_dir / "appointments.json").read_text(encoding="utf-8"))
    assert len(appointments) == 2
    assert appointments[-1]["clinic_id"] == "clinic_001"
    assert appointments[-1]["date"] == "2026-03-21"
    assert appointments[-1]["policyholder_id"] == "PH-777"
    assert appointments[-1]["doctor_name"] == "Patel"


@pytest.mark.asyncio
async def test_business_layer_unclear_escalates_on_third_attempt():
    store = SessionStore()
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=store)

    async def always_unclear(text, session_ctx):
        return IntentResult(
            intent=IntentType.UNCLEAR,
            confidence=0.2,
            extracted_entities={},
            reasoning="Ambiguous",
        )

    layer._intent_detector.detect = always_unclear

    r1 = await layer.process("huh", "sid")
    r2 = await layer.process("what", "sid")
    r3 = await layer.process("again", "sid")

    assert "book an appointment" in r1.text
    assert "book an appointment" in r2.text
    assert "arrange a callback" in r3.text
    assert store.get_or_create("sid").state == SessionState.SCHEDULING_CALLBACK


@pytest.mark.asyncio
async def test_business_layer_in_flow_to_completed(monkeypatch):
    store = SessionStore()
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=store)

    async def appointment_intent(text, session_ctx):
        return IntentResult(
            intent=IntentType.APPOINTMENT,
            confidence=0.95,
            extracted_entities={
                "date": "2026-03-21",
                "clinic": "City Clinic",
                "policy_id": "PH-991",
                "doctor_name": "Nguyen",
            },
            reasoning="User asked for booking",
        )

    async def complete_flow(text, session_ctx, groq_client, model_name):
        return FlowResult(response_text="Booked.", completed=True)

    layer._intent_detector.detect = appointment_intent
    layer._appointment_flow.execute = complete_flow

    response = await layer.process("Book me", "sid-2")

    assert response.text == "Booked."
    ctx = store.get_or_create("sid-2")
    assert ctx.state == SessionState.COMPLETED
    assert ctx.active_flow is None
    assert len(ctx.conversation_history) == 2


@pytest.mark.asyncio
async def test_business_layer_humanizes_iso_dates_in_user_reply():
    store = SessionStore()
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=store)

    async def appointment_intent(text, session_ctx):
        return IntentResult(
            intent=IntentType.APPOINTMENT,
            confidence=0.99,
            extracted_entities={"date": "2026-03-20", "clinic": "City Clinic"},
            reasoning="Clear booking request",
        )

    async def incomplete_flow(text, session_ctx, groq_client, model_name):
        return FlowResult(
            response_text="I can book you on 2026-03-20, but I still need your policy id.",
            completed=False,
        )

    layer._intent_detector.detect = appointment_intent
    layer._appointment_flow.execute = incomplete_flow

    response = await layer.process("Book on 20 March", "sid-3")

    assert response.text == "I can book you on March 20, 2026, but I still need your policy id."
