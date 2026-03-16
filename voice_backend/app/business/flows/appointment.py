from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..models import AppointmentReasoning, FlowResult, SessionContext
from ..schema_utils import to_groq_strict_schema

log = logging.getLogger("hypercheap.business.flow.appointment")


class AppointmentFlow:
    def __init__(self, data_dir: Path | None = None) -> None:
        t0 = time.perf_counter()
        base_dir = data_dir or (Path(__file__).resolve().parent.parent / "data")
        self._data_dir = base_dir
        self._clinics_path = self._data_dir / "clinics.json"
        self._appointments_path = self._data_dir / "appointments.json"
        self._clinics = json.loads(self._clinics_path.read_text(encoding="utf-8"))
        log.info(
            "[flow:init] action=read_file path=%s records=%d latency_ms=%.2f",
            self._clinics_path,
            len(self._clinics),
            (time.perf_counter() - t0) * 1000.0,
        )

    async def execute(
        self,
        text: str,
        session_ctx: SessionContext,
        groq_client: Any,
        model_name: str,
    ) -> FlowResult:
        execute_t0 = time.perf_counter()
        current_entities = dict(session_ctx.pending_entities)
        history = list(session_ctx.conversation_history[-10:])

        messages = [
            {
                "role": "system",
                "content": (
                    "You are the appointment booking flow controller. Choose the next action and provide "
                    "a user-facing message. Required entities are: date (ISO), clinic, policy_id, doctor_name. "
                    "Use ask_date/ask_clinic/ask_policy_id/ask_doctor_name when any required field is missing. "
                    "Use confirm_booking only when all required entities are present and user confirmed. "
                    "Return JSON only."
                ),
            },
            {"role": "system", "content": f"Available clinics and slots: {json.dumps(self._clinics)}"},
            {"role": "system", "content": f"Collected entities so far: {json.dumps(current_entities)}"},
            *history,
        ]
        if not history or history[-1].get("role") != "user" or history[-1].get("content") != text:
            messages.append({"role": "user", "content": text})

        log.info(
            "[flow:request] session_id=%s flow=appointment model=%s messages=%s",
            session_ctx.session_id,
            model_name,
            messages,
        )
        reasoning = await self._reason(messages, groq_client, model_name, session_ctx.session_id)
        log.info(
            "[flow:reasoning] session_id=%s action=%s message_to_user=%r extracted_date=%r extracted_clinic=%r",
            session_ctx.session_id,
            reasoning.action,
            reasoning.message_to_user,
            reasoning.extracted_date,
            reasoning.extracted_clinic,
        )

        if reasoning.extracted_date:
            session_ctx.pending_entities["date"] = reasoning.extracted_date
        if reasoning.extracted_clinic:
            session_ctx.pending_entities["clinic"] = reasoning.extracted_clinic
        if reasoning.extracted_policy_id:
            session_ctx.pending_entities["policy_id"] = reasoning.extracted_policy_id
        if reasoning.extracted_doctor_name:
            session_ctx.pending_entities["doctor_name"] = reasoning.extracted_doctor_name

        if reasoning.action == "confirm_booking":
            booking = self._build_booking_record(session_ctx.pending_entities)
            if booking is None:
                log.info(
                    (
                        "[flow:decision] session_id=%s action=confirm_booking "
                        "blocked=missing_or_invalid_entities entities=%s"
                    ),
                    session_ctx.session_id,
                    session_ctx.pending_entities,
                )
                return FlowResult(
                    response_text=(
                        "I still need a valid date, clinic, policy id, and doctor name to confirm the booking."
                    ),
                    completed=False,
                    updated_entities=dict(session_ctx.pending_entities),
                )
            self._append_appointment(session_ctx.session_id, booking)
            log.info(
                "[flow:decision] session_id=%s action=confirm_booking persisted_booking=%s",
                session_ctx.session_id,
                booking,
            )
            log.info(
                "[flow:done] session_id=%s latency_ms=%.2f",
                session_ctx.session_id,
                (time.perf_counter() - execute_t0) * 1000.0,
            )
            return FlowResult(
                response_text=reasoning.message_to_user,
                completed=True,
                updated_entities=dict(session_ctx.pending_entities),
            )

        if reasoning.action == "cancel":
            log.info("[flow:decision] session_id=%s action=cancel", session_ctx.session_id)
            log.info(
                "[flow:done] session_id=%s latency_ms=%.2f",
                session_ctx.session_id,
                (time.perf_counter() - execute_t0) * 1000.0,
            )
            return FlowResult(
                response_text=reasoning.message_to_user,
                completed=True,
                updated_entities=dict(session_ctx.pending_entities),
            )

        log.info("[flow:decision] session_id=%s action=%s", session_ctx.session_id, reasoning.action)
        log.info(
            "[flow:done] session_id=%s latency_ms=%.2f",
            session_ctx.session_id,
            (time.perf_counter() - execute_t0) * 1000.0,
        )
        return FlowResult(
            response_text=reasoning.message_to_user,
            completed=False,
            updated_entities=dict(session_ctx.pending_entities),
        )

    async def _reason(
        self,
        messages: list[dict[str, str]],
        groq_client: Any,
        model_name: str,
        session_id: str,
    ) -> AppointmentReasoning:
        t0 = time.perf_counter()
        completion = await groq_client.chat.completions.create(
            model=model_name,
            messages=messages,
            stream=False,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "appointment_reasoning",
                    "strict": True,
                    "schema": to_groq_strict_schema(AppointmentReasoning.model_json_schema()),
                },
            },
            temperature=0,
            top_p=1,
        )

        if not completion.choices:
            raise ValueError("No completion choices returned")

        content = completion.choices[0].message.content
        raw_json = self._flatten_content(content)
        parsed = json.loads(raw_json)
        log.info(
            "[flow:response] session_id=%s payload=%s latency_ms=%.2f",
            session_id,
            parsed,
            (time.perf_counter() - t0) * 1000.0,
        )
        return AppointmentReasoning.model_validate(parsed)

    def _flatten_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part.get("text", ""))
                elif hasattr(part, "type") and getattr(part, "type") == "text":
                    parts.append(getattr(part, "text", ""))
            joined = "".join(parts).strip()
            if joined:
                return joined
        if isinstance(content, dict):
            return json.dumps(content)
        raise ValueError("Unexpected content format")

    def _build_booking_record(self, entities: dict[str, str]) -> dict[str, str] | None:
        date = entities.get("date")
        clinic_raw = entities.get("clinic")
        policy_id = entities.get("policy_id")
        doctor_name = entities.get("doctor_name")
        if not date or not clinic_raw or not policy_id or not doctor_name:
            return None

        clinic_id = self._resolve_clinic_id(clinic_raw)
        if clinic_id is None:
            return None

        return {
            "id": f"appt_{uuid4().hex[:8]}",
            "policyholder_id": policy_id,
            "clinic_id": clinic_id,
            "date": date,
            "reason": f"Appointment with Dr. {doctor_name}",
            "doctor_name": doctor_name,
            "status": "scheduled",
        }

    def _resolve_clinic_id(self, clinic_value: str) -> str | None:
        lowered = clinic_value.strip().lower()
        for clinic in self._clinics:
            if clinic.get("id", "").strip().lower() == lowered:
                return clinic["id"]
            if clinic.get("name", "").strip().lower() == lowered:
                return clinic["id"]
        return None

    def _append_appointment(self, session_id: str, booking_record: dict[str, str]) -> None:
        t_read = time.perf_counter()
        appointments = json.loads(self._appointments_path.read_text(encoding="utf-8"))
        read_latency_ms = (time.perf_counter() - t_read) * 1000.0
        appointments.append(booking_record)
        t_write = time.perf_counter()
        self._appointments_path.write_text(json.dumps(appointments, indent=2), encoding="utf-8")
        write_latency_ms = (time.perf_counter() - t_write) * 1000.0
        log.info(
            (
                "[flow:file_io] session_id=%s action=read_write path=%s "
                "read_latency_ms=%.2f write_latency_ms=%.2f after_count=%d booking_id=%s"
            ),
            session_id,
            self._appointments_path,
            read_latency_ms,
            write_latency_ms,
            len(appointments),
            booking_record.get("id"),
        )
