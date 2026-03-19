import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.business.flows.appointment import AppointmentFlow
from app.business.flows.clinic_change import ClinicChangeFlow
from app.business.flows.plan_inquiry import PlanInquiryFlow
from app.business.flows.policy_renewal import PolicyRenewalFlow
from app.business.intent import IntentDetector
from app.business.layer import BusinessLayer
from app.business.models import CallbackReasoning, FlowResult, IntentResult, IntentType, SessionState
from app.business.policy_id_capture import PolicyIdCapture
from app.business.policy_store import PolicyStore
from app.business.schema_utils import to_groq_strict_schema
from app.business.session import SessionStore


class FakeCompletions:
    def __init__(self, payloads):
        self._payloads = list(payloads)

    async def create(self, **kwargs):
        if not self._payloads:
            raise RuntimeError("No fake payloads left")
        payload = self._payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
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


def _write_appointment_data(
    data_dir: Path,
    *,
    appointments: list[dict] | None = None,
    doctors: list[dict] | None = None,
    clinics: list[dict] | None = None,
    policyholders: list[dict] | None = None,
) -> None:
    data_dir.mkdir()
    (data_dir / "clinics.json").write_text(
        json.dumps(
            clinics
            or [
                {
                    "id": "clinic_001",
                    "name": "City Clinic",
                    "address": "123 Main St",
                    "available_slots": ["2026-03-20", "2026-03-21", "2026-03-24"],
                }
            ]
        ),
        encoding="utf-8",
    )
    (data_dir / "doctors.json").write_text(
        json.dumps(
            doctors
            or [
                {
                    "id": "doc_001",
                    "clinic_id": "clinic_001",
                    "name": "Patel",
                    "available_slots": ["2026-03-20", "2026-03-21"],
                }
            ]
        ),
        encoding="utf-8",
    )
    (data_dir / "appointments.json").write_text(
        json.dumps(appointments or []),
        encoding="utf-8",
    )
    (data_dir / "policyholders.json").write_text(
        json.dumps(
            policyholders
            or [
                {"policy_id": "POL-1234", "status": "active"},
                {"policy_id": "POL-2222", "status": "active"},
                {"policy_id": "POL-7777", "status": "active"},
                {"policy_id": "POL-9911", "status": "active"},
                {"policy_id": "POL-9999", "status": "active"},
                {"policy_id": "POL-1111", "status": "active"},
            ]
        ),
        encoding="utf-8",
    )


def _write_policy_flow_data(data_dir: Path) -> None:
    data_dir.mkdir()
    (data_dir / "policyholders.json").write_text(
        json.dumps(
            [
                {
                    "policy_id": "POL-1003",
                    "full_name": "Morgan Diaz",
                    "status": "active",
                    "current_plan_id": "plan_intermediate",
                    "assigned_clinic_id": "clinic_001",
                    "policy_start_date": "2025-03-01",
                    "last_renewal_date": None,
                    "last_clinic_change_date": "2025-03-01",
                }
            ]
        ),
        encoding="utf-8",
    )
    (data_dir / "plans.json").write_text(
        json.dumps(
            [
                {"id": "plan_cheap", "name": "Cheap", "monthly_price_usd": 20},
                {"id": "plan_intermediate", "name": "Intermediate", "monthly_price_usd": 40},
                {"id": "plan_expensive", "name": "Expensive", "monthly_price_usd": 80},
            ]
        ),
        encoding="utf-8",
    )
    (data_dir / "clinics.json").write_text(
        json.dumps(
            [
                {"id": "clinic_001", "name": "City Clinic"},
                {"id": "clinic_002", "name": "Riverside Health Center"},
            ]
        ),
        encoding="utf-8",
    )
    (data_dir / "doctors.json").write_text(
        json.dumps(
            [
                {
                    "id": "doc_001",
                    "clinic_id": "clinic_001",
                    "name": "Patel",
                    "available_slots": ["2026-03-20"],
                }
            ]
        ),
        encoding="utf-8",
    )
    (data_dir / "appointments.json").write_text("[]", encoding="utf-8")


@pytest.mark.asyncio
async def test_intent_detector_retry_success():
    detector = IntentDetector(
        groq_client=FakeGroqClient(
            [
                "{not-json}",
                json.dumps(
                    {
                        "intent": "APPOINTMENT",
                        "confidence": 0.9,
                        "extracted_entities": {"date": "2026-03-22"},
                        "reasoning": "User asked to book a doctor visit.",
                    }
                ),
            ]
        ),
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


@pytest.mark.asyncio
async def test_intent_detector_fallback_to_unclear_on_provider_exception():
    detector = IntentDetector(
        groq_client=FakeGroqClient([RuntimeError("invalid_json_schema"), RuntimeError("invalid_json_schema")]),
        model_name="fake-model",
    )
    store = SessionStore()
    ctx = store.get_or_create("s2-provider-error")

    result = await detector.detect("Change my plan", ctx)

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


def test_to_groq_strict_schema_strips_siblings_from_ref_nodes():
    raw_schema = {
        "type": "object",
        "properties": {
            "intent": {
                "$ref": "#/$defs/IntentType",
                "description": "Should be removed for provider compatibility.",
            }
        },
        "$defs": {
            "IntentType": {"type": "string", "enum": ["UNCLEAR"]},
        },
    }
    strict_schema = to_groq_strict_schema(raw_schema)
    assert strict_schema["properties"]["intent"] == {"$ref": "#/$defs/IntentType"}


@pytest.mark.asyncio
async def test_appointment_flow_confirm_booking_requires_explicit_final_yes(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(
        data_dir,
        appointments=[
            {
                "id": "appt_1",
                "policyholder_id": "POL-1111",
                "clinic_id": "clinic_001",
                "doctor_id": "doc_001",
                "date": "2026-03-20",
                "reason": "Existing",
                "doctor_name": "Patel",
                "status": "scheduled",
            }
        ],
    )

    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s3")
    ctx.pending_entities = {
        "date": "2026-03-21",
        "clinic": "City Clinic",
        "policy_id": "POL-7777",
        "doctor_name": "Patel",
        "doctor_confirmed": "true",
    }

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "confirm_booking",
                    "message_to_user": "Ready to book.",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                    "booking_confirmation": "unknown",
                }
            ),
            json.dumps(
                {
                    "action": "confirm_booking",
                    "message_to_user": "Your appointment is booked.",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                    "booking_confirmation": "confirmed",
                }
            )
        ]
    )

    result = await flow.execute("yes confirm", ctx, client, "fake-model")
    assert result.completed is False
    assert "Please confirm:" in result.response_text
    appointments = json.loads((data_dir / "appointments.json").read_text(encoding="utf-8"))
    assert len(appointments) == 1

    result2 = await flow.execute("yes", ctx, client, "fake-model")
    assert result2.completed is True
    appointments = json.loads((data_dir / "appointments.json").read_text(encoding="utf-8"))
    assert len(appointments) == 2
    assert appointments[-1]["clinic_id"] == "clinic_001"
    assert appointments[-1]["doctor_id"] == "doc_001"
    assert appointments[-1]["date"] == "2026-03-21"
    assert appointments[-1]["policyholder_id"] == "POL-7777"
    assert appointments[-1]["doctor_name"] == "Patel"
    assert appointments[-1]["session_id"] == "s3"
    assert result2.updated_entities["confirmed_appointment_id"] == appointments[-1]["id"]


@pytest.mark.asyncio
async def test_appointment_flow_waiting_confirmation_does_not_persist_on_ambiguous_reply(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(data_dir)

    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s3-amb")
    ctx.pending_entities = {
        "date": "2026-03-21",
        "clinic": "City Clinic",
        "policy_id": "POL-7777",
        "doctor_name": "Patel",
        "doctor_confirmed": "true",
        "booking_confirmation_pending": "true",
    }

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "clarify",
                    "message_to_user": "Can you repeat?",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                    "booking_confirmation": "unknown",
                }
            )
        ]
    )

    result = await flow.execute("maybe", ctx, client, "fake-model")

    assert result.completed is False
    assert "Reply yes to confirm or no" in result.response_text
    appointments = json.loads((data_dir / "appointments.json").read_text(encoding="utf-8"))
    assert appointments == []


@pytest.mark.asyncio
async def test_appointment_flow_rejects_invalid_policy_format(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(data_dir)

    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s4")
    ctx.pending_entities = {
        "date": "2026-03-21",
        "clinic": "City Clinic",
        "policy_id": "P O L",
        "doctor_name": "Patel",
        "doctor_confirmed": "true",
    }

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "confirm_booking",
                    "message_to_user": "Booked.",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                }
            )
        ]
    )

    result = await flow.execute("confirm", ctx, client, "fake-model")

    assert result.completed is False
    assert "Policy id must use format POL-1234" in result.response_text
    appointments = json.loads((data_dir / "appointments.json").read_text(encoding="utf-8"))
    assert appointments == []


@pytest.mark.asyncio
async def test_appointment_flow_rejects_unknown_policy_id(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(data_dir)

    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s4-unknown-policy")
    ctx.pending_entities = {
        "date": "2026-03-21",
        "clinic": "City Clinic",
        "policy_id": "POL-1004",
        "doctor_name": "Patel",
        "doctor_confirmed": "true",
    }

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "confirm_booking",
                    "message_to_user": "Booked.",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                }
            )
        ]
    )

    result = await flow.execute("confirm", ctx, client, "fake-model")

    assert result.completed is False
    assert "could not find that policy id" in result.response_text.lower()
    assert "policy_id" not in result.updated_entities
    appointments = json.loads((data_dir / "appointments.json").read_text(encoding="utf-8"))
    assert appointments == []


@pytest.mark.asyncio
async def test_appointment_flow_assembles_split_turn_policy_id_with_prefix_context(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(
        data_dir,
        policyholders=[
            {"policy_id": "POL-1005", "status": "active"},
        ],
    )

    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s4-split-policy")

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "ask_policy_id",
                    "message_to_user": "Please provide policy id.",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                    "policy_gate_signal": "missing_or_invalid",
                }
            ),
            json.dumps(
                {
                    "action": "ask_clinic",
                    "message_to_user": "Which clinic would you like?",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": "1005",
                    "extracted_doctor_name": None,
                    "policy_gate_signal": "valid",
                }
            ),
        ]
    )

    first = await flow.execute("P O L", ctx, client, "fake-model")
    ctx.conversation_history.extend(
        [
            {"role": "user", "content": "P O L"},
            {"role": "assistant", "content": first.response_text},
        ]
    )
    result = await flow.execute("one zero zero five", ctx, client, "fake-model")

    assert result.completed is False
    assert "Which clinic" in result.response_text
    assert result.updated_entities.get("policy_id") == "POL-1005"


@pytest.mark.asyncio
async def test_appointment_flow_does_not_assemble_digits_without_prefix_context(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(
        data_dir,
        policyholders=[
            {"policy_id": "POL-1005", "status": "active"},
        ],
    )

    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s4-no-prefix-policy")

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "ask_policy_id",
                    "message_to_user": "Please provide policy id.",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": "1005",
                    "extracted_doctor_name": None,
                    "policy_gate_signal": "missing_or_invalid",
                }
            )
        ]
    )

    result = await flow.execute("one zero zero five", ctx, client, "fake-model")

    assert result.completed is False
    assert "format POL-1234" in result.response_text
    assert "policy_id" not in result.updated_entities


@pytest.mark.asyncio
async def test_appointment_flow_rejects_unknown_policy_after_split_assembly(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(
        data_dir,
        policyholders=[
            {"policy_id": "POL-1005", "status": "active"},
        ],
    )

    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s4-split-policy-unknown")

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "ask_policy_id",
                    "message_to_user": "Please provide policy id.",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                    "policy_gate_signal": "missing_or_invalid",
                }
            ),
            json.dumps(
                {
                    "action": "ask_clinic",
                    "message_to_user": "Which clinic would you like?",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": "1008",
                    "extracted_doctor_name": None,
                    "policy_gate_signal": "valid",
                }
            ),
        ]
    )

    first = await flow.execute("P O L", ctx, client, "fake-model")
    ctx.conversation_history.extend(
        [
            {"role": "user", "content": "P O L"},
            {"role": "assistant", "content": first.response_text},
        ]
    )
    result = await flow.execute("one zero zero eight", ctx, client, "fake-model")

    assert result.completed is False
    assert "could not find that policy id" in result.response_text.lower()
    assert "policy_id" not in result.updated_entities


@pytest.mark.asyncio
async def test_appointment_flow_recovers_policy_from_raw_text_when_extraction_missing(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(
        data_dir,
        policyholders=[
            {"policy_id": "POL-1003", "status": "active"},
        ],
    )
    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s4-raw-policy")

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "ask_clinic",
                    "message_to_user": "Which clinic would you like?",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "selected_policy_candidate_id": None,
                    "extracted_doctor_name": None,
                    "selected_clinic_id": None,
                    "selected_doctor_id": None,
                    "selected_date": None,
                    "doctor_confirmation": "unknown",
                    "booking_confirmation": "unknown",
                    "policy_gate_signal": "valid",
                }
            )
        ]
    )

    result = await flow.execute("It's POL one zero zero three.", ctx, client, "fake-model")

    assert result.completed is False
    assert "Which clinic" in result.response_text
    assert result.updated_entities.get("policy_id") == "POL-1003"


@pytest.mark.asyncio
async def test_appointment_flow_schema_failure_with_valid_policy_moves_to_clinic_prompt(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(
        data_dir,
        policyholders=[
            {"policy_id": "POL-1003", "status": "active"},
        ],
    )
    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s4-schema-fail-policy")

    client = FakeGroqClient(
        [
            RuntimeError("json_validate_failed"),
            RuntimeError("json_validate_failed"),
        ]
    )

    result = await flow.execute("POL one zero zero three", ctx, client, "fake-model")

    assert result.completed is False
    assert "Available clinics are" in result.response_text
    assert result.updated_entities.get("policy_id") == "POL-1003"


@pytest.mark.asyncio
async def test_appointment_flow_normalizes_spoken_policy_id(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(data_dir)

    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s4b")
    ctx.pending_entities = {
        "date": "2026-03-21",
        "clinic": "City Clinic",
        "policy_id": "P O L dash one two three four",
        "doctor_name": "Patel",
        "doctor_confirmed": "true",
    }

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "confirm_booking",
                    "message_to_user": "Ready.",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                    "booking_confirmation": "unknown",
                }
            ),
            json.dumps(
                {
                    "action": "confirm_booking",
                    "message_to_user": "Booked.",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                    "booking_confirmation": "confirmed",
                }
            )
        ]
    )

    result = await flow.execute("confirm", ctx, client, "fake-model")
    assert result.completed is False
    result2 = await flow.execute("yes", ctx, client, "fake-model")
    assert result2.completed is True
    appointments = json.loads((data_dir / "appointments.json").read_text(encoding="utf-8"))
    assert appointments[-1]["policyholder_id"] == "POL-1234"


@pytest.mark.asyncio
async def test_policy_renewal_flow_resolves_policy_without_spoken_dash(tmp_path: Path):
    data_dir = tmp_path / "policy_data"
    _write_policy_flow_data(data_dir)
    policy_store = PolicyStore(data_dir=data_dir)
    flow = PolicyRenewalFlow(policy_store=policy_store)
    ctx = SessionStore().get_or_create("renew-pol")

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "list_plans",
                    "message_to_user": "Let's review plans.",
                    "extracted_policy_id": None,
                    "selected_policy_candidate_id": None,
                    "selected_plan_id": None,
                    "renewal_confirmation": "unknown",
                }
            )
        ]
    )

    result = await flow.execute("POL one zero zero three", ctx, client, "fake-model")

    assert result.completed is False
    assert result.updated_entities.get("policy_id") == "POL-1003"
    assert "Available plans are" in result.response_text


@pytest.mark.asyncio
async def test_policy_renewal_flow_requires_explicit_confirmation_before_persist(tmp_path: Path):
    data_dir = tmp_path / "policy_confirm_required"
    _write_policy_flow_data(data_dir)
    policy_store = PolicyStore(data_dir=data_dir)
    flow = PolicyRenewalFlow(policy_store=policy_store)
    ctx = SessionStore().get_or_create("renew-confirm")
    ctx.pending_entities = {"policy_id": "POL-1003", "selected_plan_id": "plan_expensive"}

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "confirm_renewal",
                    "message_to_user": "Proceeding.",
                    "extracted_policy_id": None,
                    "selected_policy_candidate_id": "__NONE__",
                    "selected_plan_id": "plan_expensive",
                    "renewal_confirmation": "unknown",
                    "handoff_intent": None,
                }
            ),
            json.dumps(
                {
                    "action": "confirm_renewal",
                    "message_to_user": "Renewed.",
                    "extracted_policy_id": None,
                    "selected_policy_candidate_id": "__NONE__",
                    "selected_plan_id": "plan_expensive",
                    "renewal_confirmation": "confirmed",
                    "handoff_intent": None,
                }
            ),
        ]
    )

    first = await flow.execute("renew it", ctx, client, "fake-model")
    assert first.completed is False
    assert "Please confirm: renew policy" in first.response_text

    second = await flow.execute("yes", ctx, client, "fake-model")
    assert second.completed is True
    assert "is renewed" in second.response_text


@pytest.mark.asyncio
async def test_policy_renewal_flow_handoff_not_overridden_by_missing_plan(tmp_path: Path):
    data_dir = tmp_path / "policy_data_handoff"
    _write_policy_flow_data(data_dir)
    policy_store = PolicyStore(data_dir=data_dir)
    flow = PolicyRenewalFlow(policy_store=policy_store)
    ctx = SessionStore().get_or_create("renew-handoff")
    ctx.pending_entities = {"policy_id": "POL-1003"}

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "handoff_intent",
                    "message_to_user": "Sure, switching to plan inquiry.",
                    "extracted_policy_id": None,
                    "selected_policy_candidate_id": "__NONE__",
                    "selected_plan_id": None,
                    "renewal_confirmation": "unknown",
                    "handoff_intent": "PLAN_INQUIRY",
                }
            )
        ]
    )

    result = await flow.execute("Which plan am I on?", ctx, client, "fake-model")

    assert result.completed is False
    assert result.response_text == "Sure, switching to plan inquiry."
    assert result.updated_entities.get("requested_flow") == "plan_inquiry"
    assert "Available plans are" not in result.response_text


@pytest.mark.asyncio
async def test_policy_renewal_flow_explains_current_plan_inline(tmp_path: Path):
    data_dir = tmp_path / "policy_data_current_plan"
    _write_policy_flow_data(data_dir)
    policy_store = PolicyStore(data_dir=data_dir)
    flow = PolicyRenewalFlow(policy_store=policy_store)
    ctx = SessionStore().get_or_create("renew-current-plan")
    ctx.pending_entities = {"policy_id": "POL-1003"}

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "explain_current_plan",
                    "message_to_user": "Let me check.",
                    "extracted_policy_id": None,
                    "selected_policy_candidate_id": "__NONE__",
                    "selected_plan_id": None,
                    "renewal_confirmation": "unknown",
                    "handoff_intent": None,
                }
            )
        ]
    )

    result = await flow.execute("Which plan am I on?", ctx, client, "fake-model")

    assert result.completed is False
    assert "Your current plan is Intermediate at $" in result.response_text
    assert "Which plan should I renew you with?" in result.response_text


@pytest.mark.asyncio
async def test_clinic_change_flow_handoff_not_blocked_by_policy_gate(tmp_path: Path):
    data_dir = tmp_path / "clinic_handoff_data"
    _write_policy_flow_data(data_dir)
    policy_store = PolicyStore(data_dir=data_dir)
    flow = ClinicChangeFlow(policy_store=policy_store, data_dir=data_dir)
    ctx = SessionStore().get_or_create("clinic-handoff")

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "handoff_intent",
                    "message_to_user": "Okay, let's renew your policy instead.",
                    "extracted_policy_id": None,
                    "selected_policy_candidate_id": "__NONE__",
                    "selected_clinic_id": None,
                    "extracted_clinic": None,
                    "handoff_intent": "POLICY_RENEWAL",
                }
            )
        ]
    )

    result = await flow.execute("Actually renew my policy", ctx, client, "fake-model")

    assert result.completed is False
    assert result.response_text == "Okay, let's renew your policy instead."
    assert result.updated_entities.get("requested_flow") == "policy_renewal"
    assert "POL-1234" not in result.response_text


@pytest.mark.asyncio
async def test_clinic_change_flow_assembles_policy_over_three_user_turns(tmp_path: Path):
    data_dir = tmp_path / "clinic_data"
    _write_policy_flow_data(data_dir)
    policy_store = PolicyStore(data_dir=data_dir)
    flow = ClinicChangeFlow(policy_store=policy_store, data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("clinic-pol")
    ctx.conversation_history = [
        {"role": "user", "content": "P O L"},
        {"role": "assistant", "content": "Please share your policy id."},
        {"role": "user", "content": "one zero"},
        {"role": "assistant", "content": "Please continue."},
    ]

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "list_clinics",
                    "message_to_user": "Pick a clinic.",
                    "extracted_policy_id": None,
                    "selected_policy_candidate_id": None,
                    "selected_clinic_id": None,
                    "extracted_clinic": None,
                }
            )
        ]
    )

    result = await flow.execute("zero three", ctx, client, "fake-model")

    assert result.completed is False
    assert result.updated_entities.get("policy_id") == "POL-1003"
    assert "Available clinics are" in result.response_text


@pytest.mark.asyncio
async def test_plan_inquiry_flow_resolves_policy_for_compare_without_dash(tmp_path: Path):
    data_dir = tmp_path / "plan_data"
    _write_policy_flow_data(data_dir)
    policy_store = PolicyStore(data_dir=data_dir)
    flow = PlanInquiryFlow(policy_store=policy_store)
    ctx = SessionStore().get_or_create("plan-pol")

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "compare_with_current_plan",
                    "message_to_user": "Comparing now.",
                    "extracted_policy_id": None,
                    "selected_policy_candidate_id": None,
                    "selected_plan_id": None,
                }
            )
        ]
    )

    result = await flow.execute("policy id is pol one zero zero three", ctx, client, "fake-model")

    assert result.completed is False
    assert "Your current plan is Intermediate" in result.response_text


@pytest.mark.asyncio
async def test_plan_inquiry_flow_replaces_stale_policy_with_selected_candidate(tmp_path: Path):
    data_dir = tmp_path / "plan_stale_policy"
    _write_policy_flow_data(data_dir)
    policy_store = PolicyStore(data_dir=data_dir)
    flow = PlanInquiryFlow(policy_store=policy_store)
    ctx = SessionStore().get_or_create("plan-stale-policy")
    ctx.pending_entities = {"policy_id": "POL-0100"}

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "compare_with_current_plan",
                    "message_to_user": "Comparing now.",
                    "extracted_policy_id": None,
                    "selected_policy_candidate_id": "raw_explicit_1",
                    "selected_plan_id": None,
                    "handoff_intent": None,
                }
            )
        ]
    )

    result = await flow.execute("It's P O L one zero zero three.", ctx, client, "fake-model")

    assert result.completed is False
    assert result.updated_entities.get("policy_id") == "POL-1003"
    assert "Your current plan is Intermediate" in result.response_text


@pytest.mark.asyncio
async def test_plan_inquiry_flow_ignores_selected_plan_on_non_selection_action(tmp_path: Path):
    data_dir = tmp_path / "plan_ignore_selection"
    _write_policy_flow_data(data_dir)
    policy_store = PolicyStore(data_dir=data_dir)
    flow = PlanInquiryFlow(policy_store=policy_store)
    ctx = SessionStore().get_or_create("plan-ignore-selection")

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "list_plans",
                    "message_to_user": "Listing plans.",
                    "extracted_policy_id": None,
                    "selected_policy_candidate_id": "__NONE__",
                    "selected_plan_id": "plan_cheap",
                    "handoff_intent": None,
                }
            )
        ]
    )

    result = await flow.execute("one zero zero", ctx, client, "fake-model")

    assert result.completed is False
    assert "selected_plan_id" not in result.updated_entities


@pytest.mark.asyncio
async def test_plan_inquiry_flow_asks_policy_id_for_current_plan_question_without_policy(tmp_path: Path):
    data_dir = tmp_path / "plan_ask_policy"
    _write_policy_flow_data(data_dir)
    policy_store = PolicyStore(data_dir=data_dir)
    flow = PlanInquiryFlow(policy_store=policy_store)
    ctx = SessionStore().get_or_create("plan-ask-policy")

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "compare_with_current_plan",
                    "message_to_user": "Let me check your current plan.",
                    "extracted_policy_id": None,
                    "selected_policy_candidate_id": "__NONE__",
                    "selected_plan_id": None,
                    "handoff_intent": None,
                }
            )
        ]
    )

    result = await flow.execute("What plan am I on?", ctx, client, "fake-model")

    assert result.completed is False
    assert "Please share your policy id" in result.response_text


@pytest.mark.asyncio
async def test_plan_inquiry_flow_offer_renewal_carries_selected_plan(tmp_path: Path):
    data_dir = tmp_path / "plan_offer_renewal"
    _write_policy_flow_data(data_dir)
    policy_store = PolicyStore(data_dir=data_dir)
    flow = PlanInquiryFlow(policy_store=policy_store)
    ctx = SessionStore().get_or_create("plan-offer-renewal")

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "offer_renewal",
                    "message_to_user": "Great, let's renew with Intermediate.",
                    "extracted_policy_id": None,
                    "selected_policy_candidate_id": "__NONE__",
                    "selected_plan_id": "plan_intermediate",
                    "handoff_intent": None,
                }
            )
        ]
    )

    result = await flow.execute("I want to switch to intermediate", ctx, client, "fake-model")

    assert result.completed is False
    assert result.updated_entities.get("requested_flow") == "policy_renewal"
    assert result.updated_entities.get("selected_plan_id") == "plan_intermediate"


@pytest.mark.asyncio
async def test_plan_inquiry_flow_ignores_self_handoff_target(tmp_path: Path):
    data_dir = tmp_path / "plan_self_handoff"
    _write_policy_flow_data(data_dir)
    policy_store = PolicyStore(data_dir=data_dir)
    flow = PlanInquiryFlow(policy_store=policy_store)
    ctx = SessionStore().get_or_create("plan-self-handoff")

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "handoff_intent",
                    "message_to_user": "Let me continue helping with plans.",
                    "extracted_policy_id": None,
                    "selected_policy_candidate_id": "__NONE__",
                    "selected_plan_id": None,
                    "handoff_intent": "PLAN_INQUIRY",
                }
            )
        ]
    )

    result = await flow.execute("help me with plans", ctx, client, "fake-model")

    assert result.completed is False
    assert result.updated_entities.get("requested_flow") is None
    assert "continue helping with plans" in result.response_text


@pytest.mark.asyncio
async def test_plan_inquiry_flow_handoff_to_appointment_sets_requested_flow(tmp_path: Path):
    data_dir = tmp_path / "plan_to_appointment"
    _write_policy_flow_data(data_dir)
    policy_store = PolicyStore(data_dir=data_dir)
    flow = PlanInquiryFlow(policy_store=policy_store)
    ctx = SessionStore().get_or_create("plan-to-appointment")

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "handoff_intent",
                    "message_to_user": "Sure, switching to appointment booking.",
                    "extracted_policy_id": None,
                    "selected_policy_candidate_id": "__NONE__",
                    "selected_plan_id": None,
                    "handoff_intent": "APPOINTMENT",
                }
            )
        ]
    )

    result = await flow.execute("Book me with a doctor", ctx, client, "fake-model")

    assert result.completed is False
    assert result.updated_entities.get("requested_flow") == "appointment"


@pytest.mark.asyncio
async def test_appointment_flow_rejects_unknown_doctor_name(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(data_dir)

    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s5")
    ctx.pending_entities = {
        "date": "2026-03-21",
        "clinic": "City Clinic",
        "policy_id": "POL-1234",
        "doctor_name": "One zero zero one",
    }

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "confirm_booking",
                    "message_to_user": "Booked.",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                }
            )
        ]
    )

    result = await flow.execute("confirm", ctx, client, "fake-model")

    assert result.completed is False
    assert "available doctors" in result.response_text


@pytest.mark.asyncio
async def test_appointment_flow_lists_clinics_after_repeat_uncertainty(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(data_dir)
    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s5b")

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "list_clinics",
                    "message_to_user": "Here are clinics.",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                }
            )
        ]
    )
    response = await flow.execute("I'm not sure", ctx, client, "fake-model")
    assert "Available clinics are" in response.response_text
    assert "City Clinic" in response.response_text


@pytest.mark.asyncio
async def test_appointment_flow_lists_doctors_after_repeat_uncertainty(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(data_dir)
    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s5c")
    ctx.pending_entities = {"clinic": "City Clinic"}

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "list_doctors",
                    "message_to_user": "Here are doctors.",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                }
            )
        ]
    )
    response = await flow.execute("not sure", ctx, client, "fake-model")
    assert "Available doctors are" in response.response_text
    assert "Patel" in response.response_text


@pytest.mark.asyncio
async def test_appointment_flow_date_only_confirm_prompts_for_clinic(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(data_dir)
    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s5d")
    ctx.pending_entities = {"date": "2026-03-21"}

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "confirm_booking",
                    "message_to_user": "Booked.",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                }
            )
        ]
    )
    result = await flow.execute("yes", ctx, client, "fake-model")

    assert result.completed is False
    assert "policy id" in result.response_text.lower()


@pytest.mark.asyncio
async def test_appointment_flow_policy_is_asked_first_when_missing(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(data_dir)
    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s5d-policy-first")
    ctx.pending_entities = {"date": "2026-03-21", "clinic": "City Clinic"}

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "ask_date",
                    "message_to_user": "What date works for you?",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                    "selected_clinic_id": None,
                    "selected_doctor_id": None,
                    "selected_date": None,
                    "doctor_confirmation": "unknown",
                }
            )
        ]
    )

    result = await flow.execute("book please", ctx, client, "fake-model")

    assert result.completed is False
    assert "policy id" in result.response_text.lower()


@pytest.mark.asyncio
async def test_appointment_flow_policy_invalid_is_blocking_before_other_steps(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(data_dir)
    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s5d-policy-invalid")
    ctx.pending_entities = {"clinic": "City Clinic", "policy_id": "P O L"}

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "ask_clinic",
                    "message_to_user": "Which clinic?",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                    "selected_clinic_id": None,
                    "selected_doctor_id": None,
                    "selected_date": None,
                    "doctor_confirmation": "unknown",
                }
            )
        ]
    )

    result = await flow.execute("go ahead", ctx, client, "fake-model")

    assert result.completed is False
    assert "POL-1234" in result.response_text


@pytest.mark.asyncio
async def test_appointment_flow_preserves_date_when_doctor_missing(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(data_dir)
    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s5e")
    ctx.pending_entities = {
        "clinic": "City Clinic",
        "date": "2026-03-21",
        "policy_id": "POL-1234",
    }

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "clarify",
                    "message_to_user": "Please choose a doctor.",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": "Unknown Doctor",
                }
            )
        ]
    )
    result = await flow.execute("not sure", ctx, client, "fake-model")

    assert result.completed is False
    assert result.updated_entities["date"] == "2026-03-21"
    assert "doctor_name" not in result.updated_entities


@pytest.mark.asyncio
async def test_appointment_flow_handles_provider_schema_failure_without_crash(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(data_dir)
    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s5e-provider-failure")
    ctx.pending_entities = {"clinic_id": "clinic_001"}

    client = FakeGroqClient(
        [
            RuntimeError("json_validate_failed: selected_date must be null"),
            json.dumps(
                {
                    "action": "ask_policy_id",
                    "message_to_user": "Please share your policy id in POL-1234 format.",
                    "extracted_date": "2026-03-21",
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                    "selected_clinic_id": "clinic_001",
                    "selected_doctor_id": None,
                    "selected_date": "2026-03-21",
                    "doctor_confirmation": "unknown",
                }
            ),
        ]
    )

    result = await flow.execute("day after tomorrow", ctx, client, "fake-model")

    assert result.completed is False
    assert "policy id" in result.response_text.lower()
    assert result.updated_entities.get("clinic_id") == "clinic_001"


@pytest.mark.asyncio
async def test_appointment_flow_rate_limit_clears_booking_pending_and_short_circuits(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(data_dir)
    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s5e-rate-limit")
    ctx.pending_entities = {
        "clinic_id": "clinic_001",
        "doctor_id": "doc_001",
        "doctor_confirmed": "true",
        "date": "2026-03-21",
        "policy_id": "POL-1234",
        "booking_confirmation_pending": "true",
    }

    client = FakeGroqClient([RuntimeError("429 Too Many Requests: please try again in 1.5s")])
    result = await flow.execute("yes", ctx, client, "fake-model")

    assert result.completed is False
    assert result.response_text == "Sorry, we're having technical issues right now. Please call later."
    assert "booking_confirmation_pending" not in result.updated_entities
    assert ctx.provider_limited_until_epoch > time.time()

    cooldown_client = FakeGroqClient([])
    second = await flow.execute("hello?", ctx, cooldown_client, "fake-model")
    assert second.response_text == "Sorry, we're having technical issues right now. Please call later."


@pytest.mark.asyncio
async def test_appointment_flow_prevents_duplicate_booking(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(
        data_dir,
        appointments=[
            {
                "id": "appt_existing",
                "policyholder_id": "POL-1234",
                "clinic_id": "clinic_001",
                "doctor_id": "doc_001",
                "date": "2026-03-21",
                "reason": "Existing",
                "doctor_name": "Patel",
                "status": "scheduled",
            }
        ],
    )

    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s6")
    ctx.pending_entities = {
        "date": "2026-03-21",
        "clinic": "City Clinic",
        "policy_id": "POL-1234",
        "doctor_name": "Patel",
        "doctor_confirmed": "true",
    }

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "confirm_booking",
                    "message_to_user": "Booked.",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                }
            )
        ]
    )

    result = await flow.execute("confirm", ctx, client, "fake-model")

    assert result.completed is False
    assert "already scheduled" in result.response_text
    appointments = json.loads((data_dir / "appointments.json").read_text(encoding="utf-8"))
    assert len(appointments) == 1


@pytest.mark.asyncio
async def test_appointment_flow_rejects_taken_slot_for_other_policy(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(
        data_dir,
        appointments=[
            {
                "id": "appt_existing",
                "policyholder_id": "POL-9999",
                "clinic_id": "clinic_001",
                "doctor_id": "doc_001",
                "date": "2026-03-21",
                "reason": "Existing",
                "doctor_name": "Patel",
                "status": "scheduled",
            }
        ],
    )

    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s7")
    ctx.pending_entities = {
        "date": "2026-03-21",
        "clinic": "City Clinic",
        "policy_id": "POL-1234",
        "doctor_name": "Patel",
        "doctor_confirmed": "true",
    }

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "confirm_booking",
                    "message_to_user": "Booked.",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                }
            )
        ]
    )

    result = await flow.execute("confirm", ctx, client, "fake-model")

    assert result.completed is False
    assert "no longer available" in result.response_text
    appointments = json.loads((data_dir / "appointments.json").read_text(encoding="utf-8"))
    assert len(appointments) == 1


@pytest.mark.asyncio
async def test_appointment_flow_requires_doctor_confirmation_before_date(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(data_dir)

    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s7-confirm")
    ctx.pending_entities = {
        "clinic": "City Clinic",
        "policy_id": "POL-1234",
    }

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "ask_date",
                    "message_to_user": "Which date do you prefer?",
                    "selected_clinic_id": "clinic_001",
                    "selected_doctor_id": "doc_001",
                    "selected_date": None,
                    "doctor_confirmation": "unknown",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                }
            )
        ]
    )

    result = await flow.execute("patel", ctx, client, "fake-model")

    assert result.completed is False
    assert result.updated_entities["doctor_id"] == "doc_001"
    assert result.updated_entities["doctor_confirmed"] == "false"
    assert "Is that correct?" in result.response_text


@pytest.mark.asyncio
async def test_appointment_flow_allows_confirm_booking_when_doctor_confirmed_this_turn(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(data_dir)

    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s7-confirmed")
    ctx.pending_entities = {
        "clinic_id": "clinic_001",
        "doctor_id": "doc_001",
        "doctor_confirmed": "false",
        "date": "2026-03-21",
        "policy_id": "POL-2222",
    }

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "confirm_booking",
                    "message_to_user": "Ready.",
                    "selected_clinic_id": None,
                    "selected_doctor_id": None,
                    "selected_date": None,
                    "doctor_confirmation": "confirmed",
                    "booking_confirmation": "unknown",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                }
            ),
            json.dumps(
                {
                    "action": "confirm_booking",
                    "message_to_user": "Booked.",
                    "selected_clinic_id": None,
                    "selected_doctor_id": None,
                    "selected_date": None,
                    "doctor_confirmation": "unknown",
                    "booking_confirmation": "confirmed",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                }
            )
        ]
    )

    result = await flow.execute("yes", ctx, client, "fake-model")

    assert result.completed is False
    result2 = await flow.execute("yes", ctx, client, "fake-model")
    assert result2.completed is True
    assert result2.response_text == "Booked."


@pytest.mark.asyncio
async def test_appointment_flow_uses_strict_selected_doctor_id(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(
        data_dir,
        doctors=[
            {
                "id": "doc_001",
                "clinic_id": "clinic_001",
                "name": "Averey Patel",
                "available_slots": ["2026-03-20"],
            },
            {
                "id": "doc_002",
                "clinic_id": "clinic_001",
                "name": "John Doe",
                "available_slots": ["2026-03-21"],
            },
            {
                "id": "doc_003",
                "clinic_id": "clinic_001",
                "name": "Sunshine Nguyen",
                "available_slots": ["2026-03-24"],
            },
        ],
    )

    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s7-idpick")
    ctx.pending_entities = {"clinic_id": "clinic_001"}

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "confirm_doctor",
                    "message_to_user": "Did you mean Sunshine Nguyen?",
                    "selected_clinic_id": "clinic_001",
                    "selected_doctor_id": "doc_003",
                    "selected_date": None,
                    "doctor_confirmation": "unknown",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                }
            )
        ]
    )

    result = await flow.execute("sunshine nuwan", ctx, client, "fake-model")

    assert result.completed is False
    assert result.updated_entities["doctor_id"] == "doc_003"
    assert "Sunshine Nguyen" in result.response_text


def test_appointment_flow_selection_schema_uses_closed_enum_with_none_sentinel(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(data_dir)

    flow = AppointmentFlow(data_dir=data_dir)
    schema = flow._appointment_reasoning_schema({"mode": "clinic_catalog", "clinics": []})
    selected_doctor_schema = schema["properties"]["selected_doctor_id"]
    selected_date_schema = schema["properties"]["selected_date"]
    assert selected_doctor_schema["type"] == "string"
    assert selected_doctor_schema["enum"] == ["__NONE__"]
    assert selected_date_schema["type"] == "string"
    assert selected_date_schema["enum"] == ["__NONE__"]


def test_appointment_flow_policy_candidate_schema_is_nullable_string_when_empty(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(data_dir)
    flow = AppointmentFlow(data_dir=data_dir)

    schema = flow._appointment_reasoning_schema({"mode": "clinic_catalog", "clinics": []}, [])
    selected_policy_schema = schema["properties"]["selected_policy_candidate_id"]
    assert selected_policy_schema["type"] == "string"
    assert selected_policy_schema["enum"] == ["__NONE__"]


def test_appointment_flow_policy_candidate_schema_uses_closed_enum_with_sentinel(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(data_dir)
    flow = AppointmentFlow(data_dir=data_dir)

    schema = flow._appointment_reasoning_schema({"mode": "clinic_catalog", "clinics": []}, ["raw_split_1"])
    selected_policy_schema = schema["properties"]["selected_policy_candidate_id"]
    assert selected_policy_schema["type"] == "string"
    assert selected_policy_schema["enum"] == ["raw_split_1", "__NONE__"]


def test_policy_capture_does_not_assemble_from_partial_prefix_only():
    capture = PolicyIdCapture(recent_user_turn_window=3)
    candidates = capture.build_candidates(text="It's P O L.", history=[], source="raw")
    assert candidates == []


def test_policy_capture_assembles_split_policy_with_double_oh_digits():
    capture = PolicyIdCapture(recent_user_turn_window=3)
    candidates = capture.build_candidates(
        text="one double oh five",
        history=[{"role": "user", "content": "p o l"}],
        source="raw",
    )
    assert len(candidates) == 1
    assert candidates[0].normalized == "POL-1005"


def test_policy_capture_explicit_policy_drops_leading_noise_zero():
    capture = PolicyIdCapture(recent_user_turn_window=3)
    candidates = capture.build_candidates(
        text="It's P O L O one double zero uh three.",
        history=[],
        source="raw",
    )
    assert candidates
    assert candidates[0].normalized == "POL-1003"


@pytest.mark.asyncio
async def test_appointment_flow_clinic_correction_clears_stale_doctor_and_date(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(
        data_dir,
        clinics=[
            {
                "id": "clinic_001",
                "name": "City Clinic",
                "address": "123 Main St",
                "available_slots": ["2026-03-20", "2026-03-21"],
            },
            {
                "id": "clinic_002",
                "name": "Riverside Health Center",
                "address": "500 River Rd",
                "available_slots": ["2026-03-24", "2026-03-25"],
            },
        ],
        doctors=[
            {
                "id": "doc_001",
                "clinic_id": "clinic_001",
                "name": "Morgan Patel",
                "available_slots": ["2026-03-20", "2026-03-21"],
            },
            {
                "id": "doc_002",
                "clinic_id": "clinic_002",
                "name": "Avery Patel",
                "available_slots": ["2026-03-24", "2026-03-25"],
            },
        ],
    )

    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s7-clinic-correction")
    ctx.pending_entities = {
        "clinic_id": "clinic_001",
        "doctor_id": "doc_001",
        "doctor_confirmed": "true",
        "date": "2026-03-21",
        "policy_id": "POL-1234",
    }

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "ask_doctor_name",
                    "message_to_user": "Sure, switching to Riverside. Which doctor do you want there?",
                    "selected_clinic_id": "clinic_002",
                    "selected_doctor_id": "doc_001",
                    "selected_date": "2026-03-21",
                    "doctor_confirmation": "unknown",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                }
            )
        ]
    )

    result = await flow.execute("actually Riverside Health Center", ctx, client, "fake-model")

    assert result.completed is False
    assert result.updated_entities["clinic_id"] == "clinic_002"
    assert "doctor_id" not in result.updated_entities
    assert "date" not in result.updated_entities
    assert "doctor_confirmed" not in result.updated_entities
    assert "switching to Riverside" in result.response_text


@pytest.mark.asyncio
async def test_appointment_flow_full_doctor_slot_conflict_suggests_alternatives(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(
        data_dir,
        appointments=[
            {
                "id": "appt_existing",
                "policyholder_id": "POL-9999",
                "clinic_id": "clinic_001",
                "doctor_id": "doc_001",
                "date": "2026-03-21",
                "reason": "Existing",
                "doctor_name": "Patel",
                "status": "scheduled",
            }
        ],
        doctors=[
            {
                "id": "doc_001",
                "clinic_id": "clinic_001",
                "name": "Patel",
                "available_slots": ["2026-03-21"],
            },
            {
                "id": "doc_002",
                "clinic_id": "clinic_001",
                "name": "Nguyen",
                "available_slots": ["2026-03-24"],
            },
        ],
    )

    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s7-alt")
    ctx.pending_entities = {
        "clinic_id": "clinic_001",
        "doctor_id": "doc_001",
        "doctor_confirmed": "true",
        "date": "2026-03-21",
        "policy_id": "POL-1234",
    }

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "confirm_booking",
                    "message_to_user": "Booked.",
                    "selected_clinic_id": None,
                    "selected_doctor_id": None,
                    "selected_date": None,
                    "doctor_confirmation": "unknown",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                }
            )
        ]
    )

    result = await flow.execute("confirm", ctx, client, "fake-model")

    assert result.completed is False
    assert "no open dates" in result.response_text
    assert "Nguyen" in result.response_text
    assert "doctor_id" not in result.updated_entities


@pytest.mark.asyncio
async def test_appointment_flow_selected_clinic_without_open_dates_prompts_clinic_switch(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(
        data_dir,
        appointments=[
            {
                "id": "appt_1",
                "policyholder_id": "POL-1001",
                "clinic_id": "clinic_001",
                "doctor_id": "doc_001",
                "date": "2026-03-20",
                "reason": "Existing",
                "doctor_name": "Avery Patel",
                "status": "scheduled",
            },
            {
                "id": "appt_2",
                "policyholder_id": "POL-1002",
                "clinic_id": "clinic_001",
                "doctor_id": "doc_001",
                "date": "2026-03-21",
                "reason": "Existing",
                "doctor_name": "Avery Patel",
                "status": "scheduled",
            },
            {
                "id": "appt_3",
                "policyholder_id": "POL-1003",
                "clinic_id": "clinic_001",
                "doctor_id": "doc_002",
                "date": "2026-03-24",
                "reason": "Existing",
                "doctor_name": "Morgan Nguyen",
                "status": "scheduled",
            },
        ],
    )

    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s7-clinic-open")
    ctx.pending_entities = {"policy_id": "POL-1234", "clinic_id": "clinic_001"}

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "ask_doctor_name",
                    "message_to_user": "Which doctor would you like to see at City Clinic?",
                    "selected_clinic_id": "clinic_001",
                    "selected_doctor_id": None,
                    "selected_date": None,
                    "doctor_confirmation": "unknown",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                }
            )
        ]
    )

    result = await flow.execute("Who has the earliest date?", ctx, client, "fake-model")

    assert result.completed is False
    assert result.progress_key == "clinic_no_open_dates"
    assert "City Clinic has no open appointment dates" in result.response_text
    assert (
        "Riverside Health Center" in result.response_text
        or "schedule a callback" in result.response_text
    )
    assert "clinic_id" in result.updated_entities
    assert "doctor_id" not in result.updated_entities


@pytest.mark.asyncio
async def test_appointment_flow_cancel_without_scheduled_appointment_is_non_terminal(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(data_dir)

    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s7b")
    ctx.pending_entities = {
        "date": "2026-03-21",
        "clinic": "City Clinic",
        "policy_id": "POL-1234",
        "doctor_name": "Patel",
    }

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "cancel",
                    "message_to_user": "Okay, canceled.",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                }
            )
        ]
    )

    result = await flow.execute("cancel it", ctx, client, "fake-model")

    assert result.completed is False
    assert "no scheduled appointment" in result.response_text


@pytest.mark.asyncio
async def test_appointment_flow_list_earliest_availability_action_returns_cross_clinic_options(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(
        data_dir,
        clinics=[
            {"id": "clinic_001", "name": "City Clinic"},
            {"id": "clinic_002", "name": "Riverside Health Center"},
            {"id": "clinic_003", "name": "Northside Medical"},
        ],
        doctors=[
            {
                "id": "doc_001",
                "clinic_id": "clinic_001",
                "name": "Avery Patel",
                "available_slots": ["2026-03-24"],
            },
            {
                "id": "doc_002",
                "clinic_id": "clinic_002",
                "name": "Morgan Nguyen",
                "available_slots": ["2026-03-20"],
            },
            {
                "id": "doc_003",
                "clinic_id": "clinic_003",
                "name": "Sofia Rivera",
                "available_slots": ["2026-03-28"],
            },
        ],
        policyholders=[{"policy_id": "POL-1003", "status": "active"}],
    )
    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s7-earliest")
    ctx.pending_entities = {"policy_id": "POL-1003", "clinic_id": "clinic_003"}

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "list_earliest_availability",
                    "message_to_user": "Let me check earliest options.",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "selected_policy_candidate_id": "__NONE__",
                    "extracted_doctor_name": None,
                    "selected_clinic_id": "__NONE__",
                    "selected_doctor_id": "__NONE__",
                    "selected_date": "__NONE__",
                    "doctor_confirmation": "unknown",
                    "booking_confirmation": "unknown",
                    "policy_gate_signal": "valid",
                }
            )
        ]
    )

    result = await flow.execute("Who has the earliest available date across clinics?", ctx, client, "fake-model")

    assert result.completed is False
    assert result.progress_key == "need_doctor"
    assert "Morgan Nguyen" in result.response_text
    assert "Riverside Health Center" in result.response_text
    assert "2026-03-20" in result.response_text


@pytest.mark.asyncio
async def test_appointment_flow_clarify_progress_key_is_state_aware(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(
        data_dir,
        policyholders=[{"policy_id": "POL-1003", "status": "active"}],
    )
    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s7-clarify-progress")
    ctx.pending_entities = {
        "policy_id": "POL-1003",
        "clinic_id": "clinic_001",
        "doctor_id": "doc_001",
        "doctor_confirmed": "false",
    }

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "clarify",
                    "message_to_user": "I had trouble processing that. Please restate your clinic, doctor, date, or policy id.",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "selected_policy_candidate_id": "__NONE__",
                    "extracted_doctor_name": None,
                    "selected_clinic_id": "__NONE__",
                    "selected_doctor_id": "__NONE__",
                    "selected_date": "__NONE__",
                    "doctor_confirmation": "unknown",
                    "booking_confirmation": "unknown",
                    "policy_gate_signal": "unknown",
                }
            )
        ]
    )

    result = await flow.execute("sorry?", ctx, client, "fake-model")

    assert result.completed is False
    assert result.progress_key == "confirm_doctor"


@pytest.mark.asyncio
async def test_business_layer_unclear_escalates_to_callback_phone_capture(tmp_path: Path):
    store = SessionStore()
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=store)
    layer._callback_requests_path = tmp_path / "callback_requests.json"
    layer._callback_requests_path.write_text("[]", encoding="utf-8")

    async def always_unclear(text, session_ctx):
        return IntentResult(
            intent=IntentType.UNCLEAR,
            confidence=0.2,
            extracted_entities={},
            reasoning="Ambiguous",
        )

    layer._intent_detector.detect = always_unclear
    layer._reason_callback = (
        lambda text, session_ctx, candidate_ids, candidates_context: _callback_reasoning_for_test(text, session_ctx)
    )
    seeded = store.get_or_create("sid")
    seeded.onboarding_stage = "completed"
    seeded.is_known_client = True

    r1 = await layer.process("huh", "sid")
    r2 = await layer.process("what", "sid")
    r3 = await layer.process("again", "sid")
    r4 = await layer.process("123", "sid")
    r5 = await layer.process("598781523", "sid")
    r6 = await layer.process("yes", "sid")
    r7 = await layer.process("2026-03-25", "sid")
    r8 = await layer.process("yes", "sid")

    assert "appointments" in r1.text
    assert "policy renewals" in r2.text
    assert "Georgian mobile number" in r3.text
    assert "need about 6 more digits" in r4.text
    assert "I captured +995598781523" in r5.text
    assert "What date works best for your callback" in r6.text
    assert "I captured March 25, 2026" in r7.text
    assert "+995598781523" in r8.text

    ctx = store.get_or_create("sid")
    assert ctx.state == SessionState.COMPLETED
    callback_requests = json.loads(layer._callback_requests_path.read_text(encoding="utf-8"))
    assert len(callback_requests) == 1
    assert callback_requests[0]["session_id"] == "sid"
    assert callback_requests[0]["phone"] == "+995598781523"
    assert callback_requests[0]["callback_date"] == "2026-03-25"


@pytest.mark.asyncio
async def test_business_layer_callback_mode_can_switch_back_to_booking(tmp_path: Path):
    store = SessionStore()
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=store)
    layer._callback_requests_path = tmp_path / "callback_requests.json"
    layer._callback_requests_path.write_text("[]", encoding="utf-8")

    async def detect_with_pivot(text, session_ctx):
        if "book" in text.lower():
            return IntentResult(
                intent=IntentType.APPOINTMENT,
                confidence=0.9,
                extracted_entities={},
                reasoning="Booking intent",
            )
        return IntentResult(
            intent=IntentType.UNCLEAR,
            confidence=0.2,
            extracted_entities={},
            reasoning="Ambiguous",
        )

    async def execute_flow(text, session_ctx, groq_client, model_name):
        return FlowResult(response_text="Sure, let's book your appointment.", completed=False)

    layer._intent_detector.detect = detect_with_pivot
    layer._appointment_flow.execute = execute_flow
    layer._reason_callback = (
        lambda text, session_ctx, candidate_ids, candidates_context: _callback_reasoning_for_test(text, session_ctx)
    )
    seeded = store.get_or_create("sid-pivot")
    seeded.onboarding_stage = "completed"
    seeded.is_known_client = True

    await layer.process("huh", "sid-pivot")
    await layer.process("what", "sid-pivot")
    r3 = await layer.process("again", "sid-pivot")
    r4 = await layer.process("book an appointment instead", "sid-pivot")
    r5 = await layer.process("yes", "sid-pivot")

    assert "Georgian mobile number" in r3.text
    assert "switch to booking now" in r4.text
    assert "book your appointment" in r5.text
    ctx = store.get_or_create("sid-pivot")
    assert ctx.state == SessionState.IN_FLOW


@pytest.mark.asyncio
async def test_business_layer_callback_collects_and_confirms_date_before_finalizing(tmp_path: Path):
    store = SessionStore()
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=store)
    layer._callback_requests_path = tmp_path / "callback_requests.json"
    layer._callback_requests_path.write_text("[]", encoding="utf-8")

    seeded = store.get_or_create("sid-cb-auto")
    seeded.onboarding_stage = "completed"
    seeded.is_known_client = True
    seeded.state = SessionState.SCHEDULING_CALLBACK
    seeded.callback_mode = "collect_phone"

    async def ask_phone_reasoning(text, session_ctx, candidate_ids, candidates_context):
        if session_ctx.callback_mode in {"confirm_phone", "confirm_date"} and text.strip().lower() in {"yes", "yeah", "yep"}:
            return CallbackReasoning(
                action="confirm_callback",
                message_to_user="Thanks. Callback confirmed.",
                extracted_phone=None,
            )
        return CallbackReasoning(
            action="ask_phone",
            message_to_user="Please continue with your phone number.",
            extracted_phone=None,
        )

    layer._reason_callback = ask_phone_reasoning

    r1 = await layer.process("Plus nine nine five.", "sid-cb-auto")
    r2 = await layer.process("Five nine eight.", "sid-cb-auto")
    r3 = await layer.process("Seven eight one.", "sid-cb-auto")
    r4 = await layer.process("Five two three.", "sid-cb-auto")
    r5 = await layer.process("yes", "sid-cb-auto")
    r6 = await layer.process("2026-03-26", "sid-cb-auto")
    r7 = await layer.process("yes", "sid-cb-auto")

    assert "need about 9 more digits" in r1.text
    assert "need about 6 more digits" in r2.text
    assert "need about 3 more digits" in r3.text
    assert "I captured +995598781523" in r4.text
    assert "What date works best for your callback" in r5.text
    assert "I captured March 26, 2026" in r6.text
    assert "+995598781523" in r7.text

    ctx = store.get_or_create("sid-cb-auto")
    assert ctx.state == SessionState.COMPLETED
    callback_requests = json.loads(layer._callback_requests_path.read_text(encoding="utf-8"))
    assert len(callback_requests) == 1
    assert callback_requests[0]["phone"] == "+995598781523"
    assert callback_requests[0]["callback_date"] == "2026-03-26"


@pytest.mark.asyncio
async def test_business_layer_callback_prefers_raw_digits_over_partial_extracted_phone(tmp_path: Path):
    store = SessionStore()
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=store)
    layer._callback_requests_path = tmp_path / "callback_requests.json"
    layer._callback_requests_path.write_text("[]", encoding="utf-8")

    seeded = store.get_or_create("sid-cb-source")
    seeded.onboarding_stage = "completed"
    seeded.is_known_client = True
    seeded.state = SessionState.SCHEDULING_CALLBACK
    seeded.callback_mode = "collect_phone"

    async def mixed_reasoning(text, session_ctx, candidate_ids, candidates_context):
        if session_ctx.callback_mode in {"confirm_phone", "confirm_date"}:
            return CallbackReasoning(
                action="confirm_callback",
                message_to_user="Thanks. Callback confirmed.",
                extracted_phone=None,
            )
        return CallbackReasoning(
            action="ask_phone",
            message_to_user="Please continue with your phone number.",
            extracted_phone="599",
        )

    layer._reason_callback = mixed_reasoning

    response = await layer.process("598781523", "sid-cb-source")
    response2 = await layer.process("yes", "sid-cb-source")
    response3 = await layer.process("2026-03-26", "sid-cb-source")
    response4 = await layer.process("yes", "sid-cb-source")

    assert "I captured +995598781523" in response.text
    assert "What date works best for your callback" in response2.text
    assert "I captured March 26, 2026" in response3.text
    assert "+995598781523" in response4.text
    callback_requests = json.loads(layer._callback_requests_path.read_text(encoding="utf-8"))
    assert len(callback_requests) == 1
    assert callback_requests[0]["phone"] == "+995598781523"
    assert callback_requests[0]["callback_date"] == "2026-03-26"


@pytest.mark.asyncio
async def test_business_layer_callback_does_not_persist_before_number_confirmation(tmp_path: Path):
    store = SessionStore()
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=store)
    layer._callback_requests_path = tmp_path / "callback_requests.json"
    layer._callback_requests_path.write_text("[]", encoding="utf-8")

    seeded = store.get_or_create("sid-cb-confirm-order")
    seeded.onboarding_stage = "completed"
    seeded.is_known_client = True
    seeded.state = SessionState.SCHEDULING_CALLBACK
    seeded.callback_mode = "collect_phone"

    async def ask_only(text, session_ctx, candidate_ids, candidates_context):
        return CallbackReasoning(
            action="ask_phone",
            message_to_user="Please continue with your phone number.",
            extracted_phone=None,
        )

    layer._reason_callback = ask_only

    response = await layer.process("598781523", "sid-cb-confirm-order")

    assert "I captured +995598781523" in response.text
    callback_requests = json.loads(layer._callback_requests_path.read_text(encoding="utf-8"))
    assert callback_requests == []
    ctx = store.get_or_create("sid-cb-confirm-order")
    assert ctx.callback_mode == "confirm_phone"


@pytest.mark.asyncio
async def test_business_layer_callback_deduplicates_repeated_995_prefix(tmp_path: Path):
    store = SessionStore()
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=store)
    layer._callback_requests_path = tmp_path / "callback_requests.json"
    layer._callback_requests_path.write_text("[]", encoding="utf-8")

    seeded = store.get_or_create("sid-cb-995-dedupe")
    seeded.onboarding_stage = "completed"
    seeded.is_known_client = True
    seeded.state = SessionState.SCHEDULING_CALLBACK
    seeded.callback_mode = "collect_phone"

    async def ask_only(text, session_ctx, candidate_ids, candidates_context):
        return CallbackReasoning(
            action="ask_phone",
            message_to_user="Please continue with your phone number.",
            extracted_phone=None,
        )

    layer._reason_callback = ask_only

    r1 = await layer.process("plus nine nine five", "sid-cb-995-dedupe")
    r2 = await layer.process("nine nine five five nine eight seven eight one five two three", "sid-cb-995-dedupe")

    assert "need about 9 more digits" in r1.text
    assert "I captured +995598781523" in r2.text
    ctx = store.get_or_create("sid-cb-995-dedupe")
    assert ctx.callback_digits_buffer == "598781523"


@pytest.mark.asyncio
async def test_business_layer_callback_provider_failure_still_captures_digits(tmp_path: Path):
    store = SessionStore()
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=store)
    layer._callback_requests_path = tmp_path / "callback_requests.json"
    layer._callback_requests_path.write_text("[]", encoding="utf-8")

    seeded = store.get_or_create("sid-cb-provider-fail")
    seeded.onboarding_stage = "completed"
    seeded.is_known_client = True
    seeded.state = SessionState.SCHEDULING_CALLBACK
    seeded.callback_mode = "collect_phone"

    async def broken_structured(schema_name, schema, messages):
        raise RuntimeError("provider failure")

    layer._call_structured = broken_structured

    response = await layer.process("598781523", "sid-cb-provider-fail")
    assert "I captured +995598781523" in response.text


def test_business_layer_callback_candidates_strip_country_code():
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=SessionStore())
    candidates = layer._build_callback_phone_candidates(
        digits_chunk="995598781523",
        current_buffer="",
        source="raw",
    )
    selected = layer._pick_best_callback_candidate(candidates)
    assert selected is not None
    assert selected["buffer"] == "598781523"
    assert selected["normalized"] == "+995598781523"


def test_business_layer_callback_candidates_prefer_mobile_prefix():
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=SessionStore())
    raw = layer._build_callback_phone_candidates(
        digits_chunk="398781523",
        current_buffer="",
        source="raw",
    )
    extracted = layer._build_callback_phone_candidates(
        digits_chunk="598781523",
        current_buffer="",
        source="extracted",
    )
    selected = layer._pick_best_callback_candidate(raw + extracted)
    assert selected is not None
    assert selected["source"] == "extracted"
    assert selected["buffer"] == "598781523"


def test_business_layer_extract_phone_digits_parses_tens_words():
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=SessionStore())
    assert layer._extract_phone_digits("Thirty four.") == "34"
    assert layer._extract_phone_digits("fifty six") == "56"
    assert layer._extract_phone_digits("ninety") == "90"


@pytest.mark.asyncio
async def test_business_layer_in_flow_to_completed():
    store = SessionStore()
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=store)

    async def appointment_intent(text, session_ctx):
        return IntentResult(
            intent=IntentType.APPOINTMENT,
            confidence=0.95,
            extracted_entities={
                "date": "2026-03-21",
                "clinic": "City Clinic",
                "policy_id": "POL-9911",
                "doctor_name": "Nguyen",
            },
            reasoning="User asked for booking",
        )

    async def complete_flow(text, session_ctx, groq_client, model_name):
        return FlowResult(response_text="Booked.", completed=True)

    layer._intent_detector.detect = appointment_intent
    layer._appointment_flow.execute = complete_flow
    seeded = store.get_or_create("sid-2")
    seeded.onboarding_stage = "completed"
    seeded.is_known_client = True

    response = await layer.process("Book me", "sid-2")

    assert response.text == "Booked."
    ctx = store.get_or_create("sid-2")
    assert ctx.state == SessionState.COMPLETED
    assert ctx.active_flow is None
    assert len(ctx.conversation_history) == 2


async def _callback_reasoning_for_test(text: str, session_ctx) -> CallbackReasoning:
    lowered = text.lower()
    if "book" in lowered:
        return CallbackReasoning(
            action="confirm_switch_to_booking",
            message_to_user=(
                "It sounds like you want to book an appointment instead. "
                "Should I stop callback scheduling and switch to booking now?"
            ),
            extracted_phone=None,
            selected_phone_candidate_id=None,
        )
    if lowered.strip() == "yes" and getattr(session_ctx, "callback_mode", None) == "confirm_exit":
        return CallbackReasoning(
            action="switch_to_booking",
            message_to_user="Switching back to booking now.",
            extracted_phone=None,
            selected_phone_candidate_id=None,
        )
    if lowered.strip() == "yes" and getattr(session_ctx, "callback_mode", None) == "confirm_phone":
        return CallbackReasoning(
            action="confirm_callback",
            message_to_user="Thanks. Callback confirmed.",
            extracted_phone=None,
            selected_phone_candidate_id=None,
        )
    if lowered.strip() == "yes" and getattr(session_ctx, "callback_mode", None) == "confirm_date":
        return CallbackReasoning(
            action="confirm_callback",
            message_to_user="Thanks. Callback confirmed.",
            extracted_phone=None,
            selected_phone_candidate_id=None,
            extracted_callback_date=getattr(session_ctx, "callback_date_iso", None),
        )
    if getattr(session_ctx, "callback_mode", None) == "collect_date":
        return CallbackReasoning(
            action="ask_callback_date",
            message_to_user="Please share callback date in YYYY-MM-DD format.",
            extracted_phone=None,
            selected_phone_candidate_id=None,
            extracted_callback_date=text.strip(),
        )
    digits = "".join(char for char in text if char.isdigit())
    if len(digits) in {9, 12}:
        return CallbackReasoning(
            action="confirm_callback",
            message_to_user="Thanks. Callback confirmed.",
            extracted_phone=text,
            selected_phone_candidate_id=None,
        )
    return CallbackReasoning(
        action="ask_phone",
        message_to_user="Please share a Georgian mobile number. You can say plus nine nine five, then nine digits.",
        extracted_phone=None,
        selected_phone_candidate_id=None,
    )


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
    seeded = store.get_or_create("sid-3")
    seeded.onboarding_stage = "completed"
    seeded.is_known_client = True

    response = await layer.process("Book on 20 March", "sid-3")

    assert response.text == "I can book you on March 20, 2026, but I still need your policy id."


@pytest.mark.asyncio
async def test_business_layer_onboarding_starts_with_client_question():
    store = SessionStore()
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=store)

    response = await layer.process("hello", "sid-onboarding")

    assert response.text == "Hello, thanks for calling. Are you our client?"


@pytest.mark.asyncio
async def test_business_layer_onboarding_non_client_plan_info_then_callback():
    store = SessionStore()
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=store)
    async def fake_reason(text, session_ctx):
        lowered = text.lower()
        if "no" in lowered:
            return SimpleNamespace(action="not_client", message_to_user="")
        if "plan" in lowered or "yes" in lowered:
            return SimpleNamespace(action="plans_then_callback", message_to_user="")
        return SimpleNamespace(action="clarify", message_to_user="")

    layer._reason_onboarding = fake_reason

    r1 = await layer.process("hi", "sid-nc")
    r2 = await layer.process("no", "sid-nc")
    r3 = await layer.process("yes tell me plans", "sid-nc")

    assert r1.text.startswith("Hello, thanks for calling.")
    assert "Would you like to become one?" in r2.text
    assert "Our plans are:" in r3.text
    assert "What Georgian mobile number should we call?" in r3.text


@pytest.mark.asyncio
async def test_business_layer_onboarding_handles_noisy_negative_with_sgr():
    store = SessionStore()
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=store)

    async def fake_reason(text, session_ctx):
        return SimpleNamespace(action="not_client", message_to_user="Nope")

    layer._reason_onboarding = fake_reason

    r1 = await layer.process("hello", "sid-onboarding-noisy")
    r2 = await layer.process("uh no", "sid-onboarding-noisy")

    assert r1.text.startswith("Hello, thanks for calling.")
    assert "Would you like to become one?" in r2.text


@pytest.mark.asyncio
async def test_business_layer_completed_client_status_correction_reenters_non_client_onboarding():
    store = SessionStore()
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=store)

    async def fake_reason(text, session_ctx, stage_override=None):
        return SimpleNamespace(action="not_client", message_to_user="")

    layer._reason_onboarding = fake_reason

    seeded = store.get_or_create("sid-client-correct")
    seeded.onboarding_stage = "completed"
    seeded.is_known_client = True
    seeded.state = SessionState.IDLE

    response = await layer.process("I am not your client.", "sid-client-correct")

    assert "Would you like to become one?" in response.text
    ctx = store.get_or_create("sid-client-correct")
    assert ctx.is_known_client is False
    assert ctx.onboarding_stage == "awaiting_become_client"
    assert ctx.active_flow is None


@pytest.mark.asyncio
async def test_business_layer_routes_policy_renewal_intent_to_renewal_flow():
    store = SessionStore()
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=store)
    seeded = store.get_or_create("sid-renew")
    seeded.onboarding_stage = "completed"
    seeded.is_known_client = True

    async def renewal_intent(text, session_ctx):
        return IntentResult(
            intent=IntentType.POLICY_RENEWAL,
            confidence=0.93,
            extracted_entities={"policy_id": "POL-1001"},
            reasoning="Renewal request",
        )

    async def renewal_flow(text, session_ctx, groq_client, model_name):
        return FlowResult(response_text="Renewal flow hit.", completed=False)

    layer._intent_detector.detect = renewal_intent
    layer._policy_renewal_flow.execute = renewal_flow

    response = await layer.process("renew my policy", "sid-renew")
    assert "Renewal flow hit." in response.text


@pytest.mark.asyncio
async def test_business_layer_routes_callback_support_intent_to_callback_support_flow():
    store = SessionStore()
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=store)
    seeded = store.get_or_create("sid-cb-support")
    seeded.onboarding_stage = "completed"
    seeded.is_known_client = True
    seeded.state = SessionState.COMPLETED

    async def support_intent(text, session_ctx):
        return IntentResult(
            intent=IntentType.CALLBACK_SUPPORT,
            confidence=0.95,
            extracted_entities={},
            reasoning="Callback support request",
        )

    async def support_flow(text, session_ctx, groq_client, model_name):
        return FlowResult(response_text="Your callback is queued.", completed=False)

    layer._intent_detector.detect = support_intent
    layer._callback_support_flow.execute = support_flow

    response = await layer.process("Did you schedule my callback?", "sid-cb-support")

    assert "queued" in response.text
    ctx = store.get_or_create("sid-cb-support")
    assert ctx.active_flow == "callback_support"
    assert ctx.state == SessionState.IN_FLOW


@pytest.mark.asyncio
async def test_business_layer_reroutes_from_active_flow_back_to_intent_entry():
    store = SessionStore()
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=store)
    seeded = store.get_or_create("sid-reroute")
    seeded.onboarding_stage = "completed"
    seeded.is_known_client = True
    seeded.state = SessionState.IN_FLOW
    seeded.active_flow = "plan_inquiry"

    async def reroute_flow(text, session_ctx, groq_client, model_name):
        return FlowResult(response_text="Switching.", completed=False, updated_entities={"requested_flow": "__intent_reroute__"})

    async def renewal_intent(text, session_ctx):
        return IntentResult(
            intent=IntentType.POLICY_RENEWAL,
            confidence=0.9,
            extracted_entities={},
            reasoning="Renewal request",
        )

    async def renewal_flow(text, session_ctx, groq_client, model_name):
        return FlowResult(response_text="Renewal flow hit.", completed=False)

    layer._plan_inquiry_flow.execute = reroute_flow
    layer._intent_detector.detect = renewal_intent
    layer._policy_renewal_flow.execute = renewal_flow

    response = await layer.process("I need renewal", "sid-reroute")

    assert "Renewal flow hit." in response.text
    ctx = store.get_or_create("sid-reroute")
    assert ctx.active_flow == "policy_renewal"
    assert ctx.state == SessionState.IN_FLOW


@pytest.mark.asyncio
async def test_business_layer_handoff_hint_sets_requested_flow_directly():
    store = SessionStore()
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=store)
    seeded = store.get_or_create("sid-direct-flow-switch")
    seeded.onboarding_stage = "completed"
    seeded.is_known_client = True
    seeded.state = SessionState.IN_FLOW
    seeded.active_flow = "plan_inquiry"

    async def plan_flow(text, session_ctx, groq_client, model_name):
        return FlowResult(
            response_text="Switching to renewal context.",
            completed=False,
            updated_entities={"requested_flow": "policy_renewal"},
        )

    layer._plan_inquiry_flow.execute = plan_flow

    response = await layer.process("renew instead", "sid-direct-flow-switch")

    assert response.text == "Switching to renewal context."
    ctx = store.get_or_create("sid-direct-flow-switch")
    assert ctx.active_flow == "policy_renewal"
    assert ctx.state == SessionState.IN_FLOW


@pytest.mark.asyncio
async def test_business_layer_handoff_hint_supports_direct_appointment_switch():
    store = SessionStore()
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=store)
    seeded = store.get_or_create("sid-direct-appt-switch")
    seeded.onboarding_stage = "completed"
    seeded.is_known_client = True
    seeded.state = SessionState.IN_FLOW
    seeded.active_flow = "plan_inquiry"

    async def plan_flow(text, session_ctx, groq_client, model_name):
        return FlowResult(
            response_text="Switching to appointment.",
            completed=False,
            updated_entities={"requested_flow": "appointment"},
        )

    layer._plan_inquiry_flow.execute = plan_flow

    response = await layer.process("book instead", "sid-direct-appt-switch")

    assert response.text == "Switching to appointment."
    ctx = store.get_or_create("sid-direct-appt-switch")
    assert ctx.active_flow == "appointment"
    assert ctx.state == SessionState.IN_FLOW


@pytest.mark.asyncio
async def test_business_layer_repeated_same_flow_response_uses_stall_recovery():
    store = SessionStore()
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=store)
    seeded = store.get_or_create("sid-stall-recovery")
    seeded.onboarding_stage = "completed"
    seeded.is_known_client = True
    seeded.state = SessionState.IN_FLOW
    seeded.active_flow = "plan_inquiry"

    async def stalled_plan_flow(text, session_ctx, groq_client, model_name):
        return FlowResult(
            response_text="Please share your policy id.",
            completed=False,
            updated_entities={},
            progress_key="need_policy_id",
        )

    layer._plan_inquiry_flow.execute = stalled_plan_flow

    r1 = await layer.process("first", "sid-stall-recovery")
    r2 = await layer.process("second", "sid-stall-recovery")
    r3 = await layer.process("third", "sid-stall-recovery")

    assert r1.text == "Please share your policy id."
    assert r2.text == "Please share your policy id."
    assert "reset this step" in r3.text.lower()


@pytest.mark.asyncio
async def test_business_layer_appointment_stall_recovery_is_step_aware():
    store = SessionStore()
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=store)
    seeded = store.get_or_create("sid-stall-appointment")
    seeded.onboarding_stage = "completed"
    seeded.is_known_client = True
    seeded.state = SessionState.IN_FLOW
    seeded.active_flow = "appointment"

    async def stalled_appointment_flow(text, session_ctx, groq_client, model_name):
        return FlowResult(
            response_text="Which doctor would you like to see at City Clinic?",
            completed=False,
            updated_entities={"policy_id": "POL-1003", "clinic_id": "clinic_001"},
            progress_key="need_doctor",
        )

    layer._appointment_flow.execute = stalled_appointment_flow

    r1 = await layer.process("first", "sid-stall-appointment")
    r2 = await layer.process("second", "sid-stall-appointment")
    r3 = await layer.process("third", "sid-stall-appointment")
    r4 = await layer.process("fourth", "sid-stall-appointment")

    assert "Which doctor would you like" in r1.text
    assert "Which doctor would you like" in r2.text
    assert "Which doctor would you like" in r3.text
    assert "choose a doctor" in r4.text.lower()
    assert "policy id" not in r4.text.lower()


@pytest.mark.asyncio
async def test_business_layer_callback_capture_ignores_noisy_no_as_switch_request():
    store = SessionStore()
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=store)
    seeded = store.get_or_create("sid-cb-noise")
    seeded.onboarding_stage = "completed"
    seeded.is_known_client = True
    seeded.state = SessionState.SCHEDULING_CALLBACK
    seeded.callback_mode = "collect_phone"

    async def fake_callback_reasoning(text, session_ctx, candidate_ids, candidates_context):
        return CallbackReasoning(
            action="confirm_switch_to_booking",
            message_to_user="Should I switch to booking?",
            extracted_phone=None,
            selected_phone_candidate_id=None,
        )

    layer._reason_callback = fake_callback_reasoning

    response = await layer.process("No uh nein.", "sid-cb-noise")

    assert "continue with your georgian mobile number" in response.text.lower()
    ctx = store.get_or_create("sid-cb-noise")
    assert ctx.state == SessionState.SCHEDULING_CALLBACK


@pytest.mark.asyncio
async def test_appointment_flow_policy_unavailable_handoffs_to_non_client_options(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_appointment_data(data_dir)

    flow = AppointmentFlow(data_dir=data_dir)
    store = SessionStore()
    ctx = store.get_or_create("s-policy-unavail")

    client = FakeGroqClient(
        [
            json.dumps(
                {
                    "action": "ask_policy_id",
                    "message_to_user": "Please provide policy id.",
                    "extracted_date": None,
                    "extracted_clinic": None,
                    "extracted_policy_id": None,
                    "extracted_doctor_name": None,
                    "policy_gate_signal": "unavailable_or_not_client",
                }
            ),
        ]
    )

    result = await flow.execute("I do not have one", ctx, client, "fake-model")

    assert result.completed is False
    assert result.updated_entities.get("handoff") == "non_client_options"
    assert "not a client yet" in result.response_text


@pytest.mark.asyncio
async def test_business_layer_non_client_handoff_reenters_onboarding_stage():
    store = SessionStore()
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=store)
    seeded = store.get_or_create("sid-handoff")
    seeded.onboarding_stage = "completed"
    seeded.is_known_client = True

    async def appt_intent(text, session_ctx):
        return IntentResult(
            intent=IntentType.APPOINTMENT,
            confidence=0.9,
            extracted_entities={},
            reasoning="Appointment request",
        )

    async def appt_flow(text, session_ctx, groq_client, model_name):
        return FlowResult(
            response_text="No problem. We can continue as non-client.",
            completed=False,
            updated_entities={"handoff": "non_client_options"},
        )

    layer._intent_detector.detect = appt_intent
    layer._appointment_flow.execute = appt_flow

    _ = await layer.process("Book appointment", "sid-handoff")

    ctx = store.get_or_create("sid-handoff")
    assert ctx.state == SessionState.IDLE
    assert ctx.active_flow is None
    assert ctx.onboarding_stage == "awaiting_become_client"
    assert ctx.is_known_client is False


@pytest.mark.asyncio
async def test_business_layer_expiry_notice_prepends_when_policy_due_soon():
    store = SessionStore()
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=store)
    seeded = store.get_or_create("sid-expiry")
    seeded.onboarding_stage = "completed"
    seeded.is_known_client = True
    seeded.pending_entities["policy_id"] = "POL-1001"

    async def appointment_intent(text, session_ctx):
        return IntentResult(
            intent=IntentType.APPOINTMENT,
            confidence=0.9,
            extracted_entities={"policy_id": "POL-1001"},
            reasoning="Booking",
        )

    async def appt_flow(text, session_ctx, groq_client, model_name):
        return FlowResult(response_text="Please choose a clinic.", completed=False)

    layer._intent_detector.detect = appointment_intent
    layer._appointment_flow.execute = appt_flow

    response = await layer.process("book", "sid-expiry")
    assert "due for renewal" in response.text


@pytest.mark.asyncio
async def test_business_layer_does_not_restore_booking_context_for_non_appointment_intent():
    store = SessionStore()
    layer = BusinessLayer(groq_client=FakeGroqClient([]), model_name="fake-model", session_store=store)

    seeded = store.get_or_create("sid-no-leak")
    seeded.onboarding_stage = "completed"
    seeded.is_known_client = True
    seeded.state = SessionState.COMPLETED
    seeded.last_booking_context = {
        "policy_id": "POL-1003",
        "clinic_id": "clinic_003",
        "doctor_id": "doc_005",
        "date": "2026-03-23",
    }

    captured: dict[str, str] = {}

    async def renewal_intent(text, session_ctx):
        return IntentResult(
            intent=IntentType.POLICY_RENEWAL,
            confidence=0.92,
            extracted_entities={},
            reasoning="Renewal request",
        )

    async def renewal_flow(text, session_ctx, groq_client, model_name):
        captured.update(session_ctx.pending_entities)
        return FlowResult(response_text="Renewal.", completed=False)

    layer._intent_detector.detect = renewal_intent
    layer._policy_renewal_flow.execute = renewal_flow

    _ = await layer.process("renew", "sid-no-leak")

    assert "clinic_id" not in captured
    assert "doctor_id" not in captured
    assert "date" not in captured
