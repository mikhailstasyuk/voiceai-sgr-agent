from __future__ import annotations

import json
import logging
import re
import time
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..models import AppointmentReasoning, FlowResult, SessionContext
from ..policy_id_capture import (
    PolicyIdCandidate,
    PolicyIdCapture,
    set_candidate_selection_schema_field,
    set_policy_candidate_schema_field,
)
from ..policy_store import PolicyStore
from ..schema_utils import to_groq_strict_schema
from ..sgr import call_structured_json

log = logging.getLogger("hypercheap.business.flow.appointment")


class AppointmentFlow:
    _MAX_HISTORY_MESSAGES = 4
    _RATE_LIMIT_RETRY = re.compile(r"try again in\s+(?:(?P<minutes>\d+)m)?(?P<seconds>\d+(?:\.\d+)?)s", re.IGNORECASE)
    _DEFAULT_PROVIDER_COOLDOWN_SECONDS = 120.0
    _PROVIDER_LIMITED_MESSAGE = "Sorry, we're having technical issues right now. Please call later."
    _POLICY_ID_RE = re.compile(r"^POL-\d{4}$")
    _POLICY_STOPWORDS = {"POLICY", "POLICYHOLDER", "HOLDER", "ID", "NUMBER", "NO"}
    _POLICY_NUMBER_WORDS = {
        "ZERO": "0",
        "ONE": "1",
        "TWO": "2",
        "THREE": "3",
        "FOUR": "4",
        "FIVE": "5",
        "SIX": "6",
        "SEVEN": "7",
        "EIGHT": "8",
        "NINE": "9",
    }

    def __init__(self, data_dir: Path | None = None, policy_store: PolicyStore | None = None) -> None:
        t0 = time.perf_counter()
        base_dir = data_dir or (Path(__file__).resolve().parent.parent / "data")
        self._data_dir = base_dir
        self._clinics_path = self._data_dir / "clinics.json"
        self._doctors_path = self._data_dir / "doctors.json"
        self._appointments_path = self._data_dir / "appointments.json"
        self._policy_store = policy_store or PolicyStore(data_dir=self._data_dir)
        self._policy_capture = PolicyIdCapture(recent_user_turn_window=3)
        self._clinics = json.loads(self._clinics_path.read_text(encoding="utf-8"))
        self._doctors = json.loads(self._doctors_path.read_text(encoding="utf-8"))
        log.info(
            "[flow:init] action=read_files clinics=%d doctors=%d latency_ms=%.2f",
            len(self._clinics),
            len(self._doctors),
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
        self._upgrade_legacy_entities(session_ctx.pending_entities)
        current_entities = dict(session_ctx.pending_entities)

        history = list(session_ctx.conversation_history[-self._MAX_HISTORY_MESSAGES :])
        clinic_context = self._build_clinic_context(current_entities)
        raw_policy_candidates: list[PolicyIdCandidate] = []
        if not self._has_verified_policy(current_entities) or self._turn_mentions_policy_signal(text):
            raw_policy_candidates = self._policy_capture.build_candidates(text=text, history=history, source="raw")
        reasoning_schema = self._appointment_reasoning_schema(
            clinic_context,
            [candidate.id for candidate in raw_policy_candidates],
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are the appointment booking flow controller. Choose the next action and provide "
                    "a user-facing message. Required entities are: date (ISO), clinic_id, policy_id, doctor_id. "
                    "Required collection order is: policy_id first, then clinic, then doctor, then doctor "
                    "confirmation, then date, then final booking confirmation. "
                    "Use ask_date/ask_clinic/ask_policy_id/ask_doctor_name when any required field is missing. "
                    "Use selected_clinic_id/selected_doctor_id/selected_date to choose canonical values from options. "
                    "Only select ids and dates that are present in the provided option lists. "
                    "For selected_clinic_id/selected_doctor_id/selected_date, always return one of provided ids "
                    "or __NONE__ when no canonical selection is made this turn. "
                    "If no selected date options are provided, keep selected_date as __NONE__ and place any date "
                    "mention into extracted_date only. "
                    "Use selected_date only when user explicitly picked a concrete appointment date from options. "
                    "When a doctor is selected, always use confirm_doctor and ask explicit yes/no confirmation "
                    "before continuing. "
                    "Use booking_confirmation=confirmed only when user explicitly confirms final booking summary. "
                    "If there is a pending booking confirmation and user says yes/no, set booking_confirmation. "
                    "Set policy_gate_signal=unavailable_or_not_client when user says they do not have a policy id "
                    "or they are not a client. "
                    "Set policy_gate_signal=missing_or_invalid when policy id is still needed or malformed. "
                    "When policy_candidates are provided, prefer selecting selected_policy_candidate_id from them "
                    "instead of rewriting policy_id text. "
                    "When no policy candidate applies, set selected_policy_candidate_id to __NONE__. "
                    "Use confirm_booking only when all required entities are present. "
                    "If validation fails, ask for the specific missing or invalid field instead of generic summaries. "
                    "Return JSON only."
                ),
            },
            {"role": "system", "content": f"Clinic context: {json.dumps(clinic_context)}"},
            {
                "role": "system",
                "content": (
                    "Policy candidates: "
                    f"{json.dumps(self._policy_capture.context_payload(raw_policy_candidates))}"
                ),
            },
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
        reasoning = await self._reason(
            messages,
            reasoning_schema,
            groq_client,
            model_name,
            session_ctx,
        )
        self._sanitize_reasoning_selection(reasoning, clinic_context)
        log.info(
            (
                "[flow:reasoning] session_id=%s action=%s message_to_user=%r selected_clinic_id=%r "
                "selected_doctor_id=%r selected_date=%r doctor_confirmation=%r "
                "booking_confirmation=%r policy_gate_signal=%r selected_policy_candidate_id=%r extracted_date=%r "
                "extracted_clinic=%r extracted_policy_id=%r extracted_doctor_name=%r"
            ),
            session_ctx.session_id,
            reasoning.action,
            reasoning.message_to_user,
            reasoning.selected_clinic_id,
            reasoning.selected_doctor_id,
            reasoning.selected_date,
            reasoning.doctor_confirmation,
            reasoning.booking_confirmation,
            reasoning.policy_gate_signal,
            reasoning.selected_policy_candidate_id,
            reasoning.extracted_date,
            reasoning.extracted_clinic,
            reasoning.extracted_policy_id,
            reasoning.extracted_doctor_name,
        )

        prior_booking_fingerprint = self._booking_fingerprint(session_ctx.pending_entities)
        clinic_changed_this_turn = False
        if reasoning.selected_clinic_id:
            if session_ctx.pending_entities.get("clinic_id") != reasoning.selected_clinic_id:
                clinic_changed_this_turn = True
                session_ctx.pending_entities.pop("doctor_id", None)
                session_ctx.pending_entities.pop("date", None)
                session_ctx.pending_entities.pop("doctor_confirmed", None)
            session_ctx.pending_entities["clinic_id"] = reasoning.selected_clinic_id
        if reasoning.selected_doctor_id and not clinic_changed_this_turn:
            if session_ctx.pending_entities.get("doctor_id") != reasoning.selected_doctor_id:
                session_ctx.pending_entities.pop("date", None)
                session_ctx.pending_entities["doctor_confirmed"] = "false"
            session_ctx.pending_entities["doctor_id"] = reasoning.selected_doctor_id
        if reasoning.selected_date and not clinic_changed_this_turn:
            session_ctx.pending_entities["date"] = reasoning.selected_date

        if reasoning.extracted_clinic and not reasoning.selected_clinic_id:
            clinic_id = self._resolve_clinic_id(reasoning.extracted_clinic)
            if clinic_id:
                if session_ctx.pending_entities.get("clinic_id") != clinic_id:
                    clinic_changed_this_turn = True
                    session_ctx.pending_entities.pop("doctor_id", None)
                    session_ctx.pending_entities.pop("date", None)
                    session_ctx.pending_entities.pop("doctor_confirmed", None)
                session_ctx.pending_entities["clinic_id"] = clinic_id
        if reasoning.extracted_date and not reasoning.selected_date:
            session_ctx.pending_entities["date"] = reasoning.extracted_date
        policy_candidates = list(raw_policy_candidates)
        if reasoning.extracted_policy_id:
            extracted_candidates = self._policy_capture.build_candidates(
                text=reasoning.extracted_policy_id,
                history=history,
                source="extracted",
            )
            policy_candidates = self._merge_policy_candidates(policy_candidates + extracted_candidates)
        selected_policy, selection_mode = self._policy_capture.resolve_candidate(
            candidates=policy_candidates,
            selected_candidate_id=reasoning.selected_policy_candidate_id,
        )
        if selected_policy is not None:
            session_ctx.pending_entities["policy_id"] = selected_policy.normalized
            log.info(
                (
                    "[flow:policy_capture] session_id=%s mode=%s candidate_id=%s source=%s "
                    "normalized=%s raw=%r"
                ),
                session_ctx.session_id,
                selection_mode,
                selected_policy.id,
                selected_policy.source,
                selected_policy.normalized,
                selected_policy.raw,
            )
        if reasoning.extracted_doctor_name and not reasoning.selected_doctor_id and not clinic_changed_this_turn:
            clinic_id = session_ctx.pending_entities.get("clinic_id")
            if clinic_id:
                doctor = self._resolve_doctor(clinic_id=clinic_id, doctor_value=reasoning.extracted_doctor_name)
                if doctor is not None:
                    session_ctx.pending_entities["doctor_id"] = str(doctor.get("id", ""))
                    session_ctx.pending_entities["doctor_confirmed"] = "false"

        if reasoning.doctor_confirmation == "confirmed" and session_ctx.pending_entities.get("doctor_id"):
            session_ctx.pending_entities["doctor_confirmed"] = "true"
        elif reasoning.doctor_confirmation == "rejected":
            session_ctx.pending_entities.pop("doctor_id", None)
            session_ctx.pending_entities.pop("date", None)
            session_ctx.pending_entities["doctor_confirmed"] = "false"

        self._reconcile_entities(session_ctx.pending_entities)
        if self._booking_fingerprint(session_ctx.pending_entities) != prior_booking_fingerprint:
            session_ctx.pending_entities.pop("booking_confirmation_pending", None)
        awaiting_booking_confirmation = session_ctx.pending_entities.get("booking_confirmation_pending") == "true"
        if reasoning.policy_gate_signal == "unavailable_or_not_client":
            session_ctx.policy_gate_unavailable_count += 1
        elif session_ctx.pending_entities.get("policy_id"):
            session_ctx.policy_gate_unavailable_count = 0

        policy_gate_response = self._policy_gate_response(session_ctx.pending_entities)
        if policy_gate_response is not None and reasoning.action not in {
            "cancel",
            "list_clinics",
            "list_doctors",
            "list_earliest_availability",
            "confirm_doctor",
        }:
            if (
                reasoning.policy_gate_signal == "unavailable_or_not_client"
                or session_ctx.policy_gate_unavailable_count >= 2
            ):
                session_ctx.policy_gate_unavailable_count = 0
                return FlowResult(
                    response_text=(
                        "No problem. If you are not a client yet, I can share plan information first "
                        "and then schedule a callback, or schedule a callback right away."
                    ),
                    completed=False,
                    updated_entities={"handoff": "non_client_options"},
                    progress_key="handoff_non_client_options",
                )
            return FlowResult(
                response_text=policy_gate_response,
                completed=False,
                updated_entities=dict(session_ctx.pending_entities),
                progress_key="need_policy_id",
            )

        clinic_id = session_ctx.pending_entities.get("clinic_id")
        if clinic_id:
            if not self._doctors_with_open_slots(clinic_id):
                session_ctx.pending_entities.pop("doctor_id", None)
                session_ctx.pending_entities.pop("date", None)
                session_ctx.pending_entities["doctor_confirmed"] = "false"
                return FlowResult(
                    response_text=self._clinic_no_open_dates_message(clinic_id),
                    completed=False,
                    updated_entities=dict(session_ctx.pending_entities),
                    progress_key="clinic_no_open_dates",
                )

            doctor_id = session_ctx.pending_entities.get("doctor_id")
            if doctor_id and not self._effective_slots_for_doctor(clinic_id, doctor_id):
                session_ctx.pending_entities.pop("doctor_id", None)
                session_ctx.pending_entities.pop("date", None)
                session_ctx.pending_entities["doctor_confirmed"] = "false"
                return FlowResult(
                    response_text=self._doctor_no_open_dates_message(clinic_id, doctor_id),
                    completed=False,
                    updated_entities=dict(session_ctx.pending_entities),
                    progress_key="doctor_no_open_dates",
                )

        if (
            session_ctx.pending_entities.get("doctor_id")
            and session_ctx.pending_entities.get("doctor_confirmed") != "true"
            and reasoning.action in {"ask_date", "ask_policy_id", "confirm_booking"}
        ):
            return FlowResult(
                response_text=self._doctor_confirmation_message(session_ctx.pending_entities),
                completed=False,
                updated_entities=dict(session_ctx.pending_entities),
                progress_key="confirm_doctor",
            )

        if awaiting_booking_confirmation and reasoning.booking_confirmation == "rejected":
            session_ctx.pending_entities.pop("booking_confirmation_pending", None)
            return FlowResult(
                response_text=(
                    reasoning.message_to_user
                    or (
                        "Okay, I will not book that yet. Which detail should I change: "
                        "clinic, doctor, date, or policy id?"
                    )
                ),
                completed=False,
                updated_entities=dict(session_ctx.pending_entities),
                progress_key="confirm_booking",
            )

        if (
            awaiting_booking_confirmation
            and reasoning.booking_confirmation == "unknown"
            and reasoning.action != "confirm_booking"
        ):
            if self._is_provider_limited_message(reasoning.message_to_user):
                session_ctx.pending_entities.pop("booking_confirmation_pending", None)
                return FlowResult(
                    response_text=reasoning.message_to_user,
                    completed=False,
                    updated_entities=dict(session_ctx.pending_entities),
                    progress_key="provider_limited",
                )
            return FlowResult(
                response_text=self._booking_confirmation_message(session_ctx.pending_entities),
                completed=False,
                updated_entities=dict(session_ctx.pending_entities),
                progress_key="confirm_booking",
            )
        if (
            awaiting_booking_confirmation
            and reasoning.booking_confirmation == "confirmed"
            and reasoning.action != "confirm_booking"
        ):
            reasoning.action = "confirm_booking"

        if reasoning.action == "list_clinics":
            return FlowResult(
                response_text=f"Available clinics are: {self._clinic_options_message()}",
                completed=False,
                updated_entities=dict(session_ctx.pending_entities),
                progress_key="need_clinic",
            )

        if reasoning.action == "list_doctors":
            clinic_id = session_ctx.pending_entities.get("clinic_id")
            if clinic_id:
                return FlowResult(
                    response_text=f"Available doctors are: {self._doctor_options_message(clinic_id)}",
                    completed=False,
                    updated_entities=dict(session_ctx.pending_entities),
                    progress_key="need_doctor",
                )
            return FlowResult(
                response_text="Please choose a clinic first. I can list available clinics if needed.",
                completed=False,
                updated_entities=dict(session_ctx.pending_entities),
                progress_key="need_clinic",
            )

        if reasoning.action == "list_earliest_availability":
            return FlowResult(
                response_text=self._earliest_availability_message(session_ctx.pending_entities),
                completed=False,
                updated_entities=dict(session_ctx.pending_entities),
                progress_key=self._clarify_progress_key(session_ctx.pending_entities),
            )

        if reasoning.action == "clarify":
            clarify_progress_key = self._clarify_progress_key(session_ctx.pending_entities)
            if clarify_progress_key == "need_clinic" and reasoning.message_to_user.startswith("I had trouble processing"):
                return FlowResult(
                    response_text=f"Available clinics are: {self._clinic_options_message()}",
                    completed=False,
                    updated_entities=dict(session_ctx.pending_entities),
                    progress_key=clarify_progress_key,
                )
            return FlowResult(
                response_text=reasoning.message_to_user or "Could you clarify your preferred clinic, doctor, or date?",
                completed=False,
                updated_entities=dict(session_ctx.pending_entities),
                progress_key=clarify_progress_key,
            )

        if reasoning.action == "confirm_doctor":
            confirmation_prompt = reasoning.message_to_user or self._doctor_confirmation_message(
                session_ctx.pending_entities
            )
            return FlowResult(
                response_text=confirmation_prompt,
                completed=False,
                updated_entities=dict(session_ctx.pending_entities),
                progress_key="confirm_doctor",
            )

        if reasoning.action == "confirm_booking":
            booking, error_code = self._build_booking_record(session_ctx.pending_entities, session_ctx.session_id)
            if booking is None:
                return FlowResult(
                    response_text=self._booking_validation_message(error_code, session_ctx.pending_entities),
                    completed=False,
                    updated_entities=dict(session_ctx.pending_entities),
                    progress_key=self._progress_key_for_booking_error(error_code),
                )

            duplicate_or_conflict = self._find_duplicate_or_conflict(booking)
            if duplicate_or_conflict == "duplicate":
                return FlowResult(
                    response_text=(
                        "This appointment is already scheduled. If you want, I can help you pick a different date."
                    ),
                    completed=False,
                    updated_entities=dict(session_ctx.pending_entities),
                    progress_key="need_date",
                )
            if duplicate_or_conflict == "slot_taken":
                session_ctx.pending_entities.pop("date", None)
                clinic_id = session_ctx.pending_entities.get("clinic_id")
                doctor_id = session_ctx.pending_entities.get("doctor_id")
                if clinic_id and doctor_id and not self._effective_slots_for_doctor(clinic_id, doctor_id):
                    session_ctx.pending_entities.pop("doctor_id", None)
                    session_ctx.pending_entities["doctor_confirmed"] = "false"
                    return FlowResult(
                        response_text=self._doctor_fully_booked_message(clinic_id),
                        completed=False,
                        updated_entities=dict(session_ctx.pending_entities),
                        progress_key="need_doctor",
                    )
                return FlowResult(
                    response_text=(
                        "That doctor and date are no longer available. "
                        "Please choose another date from available slots."
                    ),
                    completed=False,
                    updated_entities=dict(session_ctx.pending_entities),
                    progress_key="need_date",
                )

            if not awaiting_booking_confirmation:
                session_ctx.pending_entities["booking_confirmation_pending"] = "true"
                return FlowResult(
                    response_text=self._booking_confirmation_message(session_ctx.pending_entities),
                    completed=False,
                    updated_entities=dict(session_ctx.pending_entities),
                    progress_key="confirm_booking",
                )

            if reasoning.booking_confirmation != "confirmed":
                return FlowResult(
                    response_text=(
                        "Please confirm the booking with yes or no so I can proceed. "
                        + self._booking_confirmation_message(session_ctx.pending_entities)
                    ),
                    completed=False,
                    updated_entities=dict(session_ctx.pending_entities),
                    progress_key="confirm_booking",
                )

            self._append_appointment(session_ctx.session_id, booking)
            session_ctx.pending_entities.pop("booking_confirmation_pending", None)
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
                updated_entities={
                    **dict(session_ctx.pending_entities),
                    "confirmed_appointment_id": booking["id"],
                },
            )

        if reasoning.action == "cancel":
            log.info("[flow:decision] session_id=%s action=cancel", session_ctx.session_id)
            canceled = self._cancel_latest_scheduled_appointment(
                session_ctx.session_id, session_ctx.last_confirmed_appointment_id
            )
            if not canceled:
                return FlowResult(
                    response_text="There is no scheduled appointment to cancel right now. I can help you book one.",
                    completed=False,
                    updated_entities=dict(session_ctx.pending_entities),
                    progress_key="cancel_noop",
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
            progress_key=self._progress_key_for_action(reasoning.action),
        )

    async def _reason(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        groq_client: Any,
        model_name: str,
        session_ctx: SessionContext,
    ) -> AppointmentReasoning:
        t0 = time.perf_counter()
        if self._provider_limited_active(session_ctx):
            log.warning(
                "[flow:provider_limited] session_id=%s action=short_circuit until=%.2f",
                session_ctx.session_id,
                session_ctx.provider_limited_until_epoch,
            )
            return self._provider_limited_reasoning()
        try:
            parsed = await self._call_structured("appointment_reasoning", schema, messages, groq_client, model_name)
            session_ctx.provider_limited_until_epoch = 0.0
            session_ctx.provider_limited_reason = None
            log.info(
                "[flow:response] session_id=%s payload=%s latency_ms=%.2f",
                session_ctx.session_id,
                parsed,
                (time.perf_counter() - t0) * 1000.0,
            )
            return AppointmentReasoning.model_validate(parsed)
        except Exception as exc:
            if self._is_rate_limit_error(exc):
                self._mark_provider_limited(session_ctx, exc)
                return self._provider_limited_reasoning()
            log.exception("[flow:error] session_id=%s attempt=1 validation_failed", session_ctx.session_id)
            correction_messages = messages + [
                {
                    "role": "system",
                    "content": (
                        "Previous output was invalid. Return ONLY valid JSON that matches the schema exactly. "
                        "If uncertain, set selected_policy_candidate_id to __NONE__ and use extracted fields with plain text."
                    ),
                }
            ]
            try:
                parsed = await self._call_structured(
                    "appointment_reasoning", schema, correction_messages, groq_client, model_name
                )
                session_ctx.provider_limited_until_epoch = 0.0
                session_ctx.provider_limited_reason = None
                log.info(
                    "[flow:response] session_id=%s payload=%s latency_ms=%.2f",
                    session_ctx.session_id,
                    parsed,
                    (time.perf_counter() - t0) * 1000.0,
                )
                return AppointmentReasoning.model_validate(parsed)
            except Exception as exc:
                if self._is_rate_limit_error(exc):
                    self._mark_provider_limited(session_ctx, exc)
                    return self._provider_limited_reasoning()
                log.exception("[flow:error] session_id=%s attempt=2 validation_failed", session_ctx.session_id)
                return AppointmentReasoning(
                    action="clarify",
                    message_to_user=(
                        "I had trouble processing that. Please restate your clinic, doctor, date, "
                        "or policy id."
                    ),
                    extracted_date=None,
                    extracted_clinic=None,
                    extracted_policy_id=None,
                    selected_policy_candidate_id=None,
                    extracted_doctor_name=None,
                    selected_clinic_id=None,
                    selected_doctor_id=None,
                    selected_date=None,
                    doctor_confirmation="unknown",
                    booking_confirmation="unknown",
                    policy_gate_signal="unknown",
                )

    def _provider_limited_active(self, session_ctx: SessionContext) -> bool:
        return session_ctx.provider_limited_until_epoch > time.time()

    def _provider_limited_reasoning(self) -> AppointmentReasoning:
        return AppointmentReasoning(
            action="clarify",
            message_to_user=self._PROVIDER_LIMITED_MESSAGE,
            extracted_date=None,
            extracted_clinic=None,
            extracted_policy_id=None,
            selected_policy_candidate_id=None,
            extracted_doctor_name=None,
            selected_clinic_id=None,
            selected_doctor_id=None,
            selected_date=None,
            doctor_confirmation="unknown",
            booking_confirmation="unknown",
            policy_gate_signal="unknown",
        )

    def _is_provider_limited_message(self, message: str) -> bool:
        return message.strip() == self._PROVIDER_LIMITED_MESSAGE

    def _is_rate_limit_error(self, exc: Exception) -> bool:
        code = str(getattr(exc, "code", "")).lower()
        if "rate_limit" in code:
            return True
        name = exc.__class__.__name__.lower()
        if "ratelimit" in name:
            return True
        message = str(exc).lower()
        return "rate limit" in message or "429" in message or "tokens per day" in message

    def _mark_provider_limited(self, session_ctx: SessionContext, exc: Exception) -> None:
        wait_seconds = self._retry_after_seconds(str(exc))
        session_ctx.provider_limited_until_epoch = max(
            session_ctx.provider_limited_until_epoch,
            time.time() + wait_seconds,
        )
        session_ctx.provider_limited_reason = str(exc)
        log.warning(
            "[flow:provider_limited] session_id=%s wait_seconds=%.2f reason=%r",
            session_ctx.session_id,
            wait_seconds,
            session_ctx.provider_limited_reason,
        )

    def _retry_after_seconds(self, error_text: str) -> float:
        match = self._RATE_LIMIT_RETRY.search(error_text)
        if match is None:
            return self._DEFAULT_PROVIDER_COOLDOWN_SECONDS
        minutes_raw = match.group("minutes")
        seconds_raw = match.group("seconds")
        minutes = float(minutes_raw) * 60.0 if minutes_raw else 0.0
        seconds = float(seconds_raw) if seconds_raw else 0.0
        total = minutes + seconds
        if total <= 0:
            return self._DEFAULT_PROVIDER_COOLDOWN_SECONDS
        return total

    def _sanitize_reasoning_selection(
        self,
        reasoning: AppointmentReasoning,
        clinic_context: dict[str, Any],
    ) -> None:
        allowed_clinic_ids = {str(clinic.get("id", "")) for clinic in self._clinics}
        if reasoning.selected_clinic_id and reasoning.selected_clinic_id not in allowed_clinic_ids:
            reasoning.selected_clinic_id = None

        allowed_doctor_ids: set[str] = set()
        allowed_dates: set[str] = set()
        if clinic_context.get("mode") == "selected_clinic_doctors":
            selected = clinic_context.get("selected_clinic", {})
            doctors = selected.get("doctors", [])
            for doctor in doctors:
                doctor_id = str(doctor.get("id", "")).strip()
                if not doctor_id:
                    continue
                allowed_doctor_ids.add(doctor_id)
                if reasoning.selected_doctor_id and reasoning.selected_doctor_id == doctor_id:
                    allowed_dates = {str(slot) for slot in doctor.get("available_slots", []) if str(slot).strip()}

        if reasoning.selected_doctor_id and reasoning.selected_doctor_id not in allowed_doctor_ids:
            reasoning.selected_doctor_id = None
            reasoning.selected_date = None

        if reasoning.selected_date and reasoning.selected_date not in allowed_dates:
            reasoning.selected_date = None

    def _has_verified_policy(self, entities: dict[str, str]) -> bool:
        normalized = self._normalize_policy_id(entities.get("policy_id", ""))
        if normalized is None:
            return False
        return self._policy_store.find_policyholder(normalized) is not None

    def _turn_mentions_policy_signal(self, text: str) -> bool:
        if re.search(r"\bpol(?:icy)?\b", text, flags=re.IGNORECASE):
            return True
        candidates = self._policy_capture.build_candidates(text=text, history=[], source="raw")
        return bool(candidates)

    def _policy_gate_response(self, entities: dict[str, str]) -> str | None:
        policy_raw = entities.get("policy_id", "")
        if policy_raw:
            normalized = self._normalize_policy_id(policy_raw)
            if normalized is not None:
                holder = self._policy_store.find_policyholder(normalized)
                if holder is None:
                    entities.pop("policy_id", None)
                    log.info("[flow:policy_gate] action=unknown_policy_id policy_id=%s", normalized)
                    return "I could not find that policy id. Please provide a valid POL-1234 id first."
                entities["policy_id"] = normalized
                log.info("[flow:policy_gate] action=verified policy_id=%s", normalized)
                return None
            entities.pop("policy_id", None)
            log.info("[flow:policy_gate] action=invalid_format raw=%r", policy_raw)
            return "Policy id must use format POL-1234. Please provide a valid policy id first."
        log.info("[flow:policy_gate] action=missing_policy_id")
        return "Before we continue, please share your policy id in the format POL-1234."

    def _appointment_reasoning_schema(
        self,
        clinic_context: dict[str, Any],
        policy_candidate_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        schema = to_groq_strict_schema(AppointmentReasoning.model_json_schema())
        properties = schema.get("properties", {})
        clinic_ids = [str(clinic.get("id", "")) for clinic in self._clinics if str(clinic.get("id", ""))]
        doctor_ids: list[str] = []
        date_options: list[str] = []
        if clinic_context.get("mode") == "selected_clinic_doctors":
            selected = clinic_context.get("selected_clinic", {})
            doctors = selected.get("doctors", [])
            doctor_ids = [str(doctor.get("id", "")) for doctor in doctors if str(doctor.get("id", ""))]
            selected_doctor_id = str(selected.get("selected_doctor_id", "")).strip()
            if selected_doctor_id:
                for doctor in doctors:
                    if str(doctor.get("id", "")) == selected_doctor_id:
                        date_options = [str(slot) for slot in doctor.get("available_slots", []) if str(slot).strip()]
                        break

        self._set_selection_schema_field(
            schema=schema,
            field_name="selected_clinic_id",
            options=clinic_ids,
            when_options_description="Select one clinic id from the provided options.",
            when_empty_description="No clinic should be selected this turn; use __NONE__.",
        )
        self._set_selection_schema_field(
            schema=schema,
            field_name="selected_doctor_id",
            options=doctor_ids,
            when_options_description="Select one doctor id from the provided options.",
            when_empty_description="No doctor should be selected this turn; use __NONE__.",
        )
        self._set_selection_schema_field(
            schema=schema,
            field_name="selected_date",
            options=date_options,
            when_options_description="Select one appointment date from the provided options.",
            when_empty_description="No appointment date should be selected this turn; use __NONE__.",
        )
        set_policy_candidate_schema_field(schema, policy_candidate_ids or [])
        return schema

    def _merge_policy_candidates(self, candidates: list[PolicyIdCandidate]) -> list[PolicyIdCandidate]:
        deduped: list[PolicyIdCandidate] = []
        seen: set[str] = set()
        for candidate in candidates:
            if candidate.normalized in seen:
                continue
            seen.add(candidate.normalized)
            deduped.append(candidate)
        return deduped

    def _set_selection_schema_field(
        self,
        *,
        schema: dict[str, Any],
        field_name: str,
        options: list[str],
        when_options_description: str,
        when_empty_description: str,
    ) -> None:
        set_candidate_selection_schema_field(
            schema=schema,
            field_name=field_name,
            candidate_ids=options,
            when_candidates_description=when_options_description,
            when_empty_description=when_empty_description,
        )

    async def _call_structured(
        self,
        schema_name: str,
        schema: dict[str, Any],
        messages: list[dict[str, str]],
        groq_client: Any,
        model_name: str,
    ) -> dict[str, Any]:
        return await call_structured_json(
            client=groq_client,
            model_name=model_name,
            schema_name=schema_name,
            schema=schema,
            messages=messages,
        )

    def _build_booking_record(
        self, entities: dict[str, str], session_id: str
    ) -> tuple[dict[str, str] | None, str | None]:
        clinic_id = entities.get("clinic_id")
        if not clinic_id:
            return None, "missing_clinic"
        clinic = self._get_clinic_by_id(clinic_id)
        if clinic is None:
            return None, "invalid_clinic"

        doctor_id = entities.get("doctor_id")
        if not doctor_id:
            return None, "missing_doctor"
        doctor = self._resolve_doctor_by_id(clinic_id=clinic_id, doctor_id=doctor_id)
        if doctor is None:
            return None, "invalid_doctor"
        if entities.get("doctor_confirmed") != "true":
            return None, "unconfirmed_doctor"

        date_value = entities.get("date")
        if not date_value:
            return None, "missing_date"
        if not self._is_valid_iso_date(date_value):
            return None, "invalid_date"
        if date_value not in [str(slot) for slot in doctor.get("available_slots", [])]:
            return None, "unavailable_slot"

        policy_id_raw = entities.get("policy_id")
        if not policy_id_raw:
            return None, "missing_policy_id"
        policy_id = self._normalize_policy_id(policy_id_raw)
        if policy_id is None:
            return None, "invalid_policy_id"

        return (
            {
                "id": f"appt_{uuid4().hex[:8]}",
                "session_id": session_id,
                "policyholder_id": policy_id,
                "clinic_id": clinic_id,
                "doctor_id": str(doctor.get("id", "")),
                "date": date_value,
                "reason": f"Appointment with Dr. {doctor.get('name', doctor_id)}",
                "doctor_name": str(doctor.get("name", doctor_id)),
                "status": "scheduled",
            },
            None,
        )

    def _booking_validation_message(self, error_code: str | None, entities: dict[str, str]) -> str:
        if error_code in {"missing_clinic", "invalid_clinic"}:
            return "Please choose a clinic first. I can list available clinics if needed."
        if error_code in {"missing_doctor", "invalid_doctor"}:
            clinic_id = entities.get("clinic_id")
            if clinic_id:
                doctors = self._list_doctor_names(clinic_id)
                if doctors:
                    return f"Please choose one of the available doctors: {', '.join(doctors)}."
            return "Please share the doctor's name for your selected clinic."
        if error_code == "unconfirmed_doctor":
            return self._doctor_confirmation_message(entities)
        if error_code == "missing_date":
            return "Please share your preferred appointment date in YYYY-MM-DD format."
        if error_code == "invalid_date":
            return "Please provide a valid date in YYYY-MM-DD format."
        if error_code == "unavailable_slot":
            clinic_id = entities.get("clinic_id")
            doctor_id = entities.get("doctor_id")
            if clinic_id and doctor_id:
                slots = self._effective_slots_for_doctor(clinic_id, doctor_id)
                if slots:
                    return f"That slot is unavailable. Available dates for this doctor are: {', '.join(slots)}."
                return self._doctor_fully_booked_message(clinic_id)
            return "That doctor/date slot is unavailable. Please pick another date."
        if error_code == "missing_policy_id":
            return "Please share your policy id in the format POL-1234."
        if error_code == "invalid_policy_id":
            return "Policy id must use format POL-1234. Please provide a valid policy id."
        return "I still need the required booking details to continue."

    def _normalize_policy_id(self, policy_id: str) -> str | None:
        normalized = policy_id.strip().upper()
        if not self._POLICY_ID_RE.fullmatch(normalized):
            expanded = normalized
            for word, digit in self._POLICY_NUMBER_WORDS.items():
                expanded = re.sub(rf"\b{word}\b", digit, expanded)
            expanded = re.sub(r"\b(DASH|HYPHEN)\b", "-", expanded)
            tokens = re.findall(r"[A-Z]+|\d+|-", expanded)
            filtered = [token for token in tokens if token not in self._POLICY_STOPWORDS]
            collapsed = "".join(token for token in filtered if token != "-")
            match = re.search(r"POL(\d{4})", collapsed)
            if not match:
                return None
            return f"POL-{match.group(1)}"
        return normalized

    def _has_recent_policy_prefix_context(self, history: list[dict[str, str]]) -> bool:
        for item in reversed(history):
            if item.get("role") != "user":
                continue
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            if self._looks_like_policy_prefix(content):
                return True
        return False

    def _looks_like_policy_prefix(self, text: str) -> bool:
        upper = text.upper()
        letters_only = re.sub(r"[^A-Z]", "", upper)
        if "POLICY" in letters_only:
            return True
        if "POL" in letters_only:
            return True
        return False

    def _assemble_policy_id_from_digits(self, value: str) -> str | None:
        digits = re.sub(r"\D", "", value)
        if len(digits) != 4:
            return None
        return f"POL-{digits}"

    def _reconcile_entities(self, entities: dict[str, str]) -> None:
        clinic_id = entities.get("clinic_id")
        if clinic_id is None:
            entities.pop("doctor_id", None)
            entities.pop("doctor_confirmed", None)
            entities.pop("date", None)
            return

        clinic = self._get_clinic_by_id(clinic_id)
        if clinic is None:
            entities.pop("clinic_id", None)
            entities.pop("doctor_id", None)
            entities.pop("doctor_confirmed", None)
            entities.pop("date", None)
            return

        doctor_id = entities.get("doctor_id", "")
        doctor = self._resolve_doctor_by_id(clinic_id, doctor_id) if doctor_id else None
        if doctor is None:
            if doctor_id:
                entities.pop("doctor_id", None)
                entities.pop("doctor_confirmed", None)
                entities.pop("date", None)
            return

        if entities.get("doctor_confirmed") not in {"true", "false"}:
            entities["doctor_confirmed"] = "false"

        date_value = entities.get("date")
        if date_value and date_value not in [str(slot) for slot in doctor.get("available_slots", [])]:
            entities.pop("date", None)

    def _resolve_clinic_id(self, clinic_value: str) -> str | None:
        lowered = clinic_value.strip().lower()
        for clinic in self._clinics:
            if clinic.get("id", "").strip().lower() == lowered:
                return str(clinic["id"])
            if clinic.get("name", "").strip().lower() == lowered:
                return str(clinic["id"])
        return None

    def _resolve_doctor(self, clinic_id: str, doctor_value: str) -> dict[str, Any] | None:
        lowered = doctor_value.strip().lower()
        for doctor in self._doctors:
            if doctor.get("clinic_id") != clinic_id:
                continue
            if str(doctor.get("id", "")).strip().lower() == lowered:
                return doctor
            if str(doctor.get("name", "")).strip().lower() == lowered:
                return doctor
        return None

    def _resolve_doctor_by_id(self, clinic_id: str, doctor_id: str) -> dict[str, Any] | None:
        lowered = doctor_id.strip().lower()
        for doctor in self._doctors:
            if doctor.get("clinic_id") != clinic_id:
                continue
            if str(doctor.get("id", "")).strip().lower() == lowered:
                return doctor
        return None

    def _find_duplicate_or_conflict(self, booking_record: dict[str, str]) -> str | None:
        appointments = json.loads(self._appointments_path.read_text(encoding="utf-8"))
        normalized_policy = booking_record["policyholder_id"].strip().upper()
        doctor_id = booking_record.get("doctor_id", "").strip().lower()
        doctor_name = booking_record.get("doctor_name", "").strip().lower()

        for appointment in appointments:
            if appointment.get("status") != "scheduled":
                continue

            appt_doctor_id = str(appointment.get("doctor_id", "")).strip().lower()
            appt_doctor_name = str(appointment.get("doctor_name", "")).strip().lower()
            same_doctor = bool(doctor_id and appt_doctor_id and doctor_id == appt_doctor_id)
            if not same_doctor:
                same_doctor = bool(doctor_name and appt_doctor_name and doctor_name == appt_doctor_name)

            if (
                str(appointment.get("clinic_id", "")).strip().lower() == booking_record["clinic_id"].strip().lower()
                and str(appointment.get("date", "")).strip() == booking_record["date"]
                and same_doctor
            ):
                existing_policy = str(appointment.get("policyholder_id", "")).strip().upper()
                if existing_policy == normalized_policy:
                    return "duplicate"
                return "slot_taken"

        return None

    def _clinic_options_message(self) -> str:
        names = [str(clinic.get("name", "")).strip() for clinic in self._clinics]
        names = [name for name in names if name]
        return ", ".join(names) if names else "I don't have clinic options loaded right now."

    def _doctor_options_message(self, clinic_id: str) -> str:
        options: list[str] = []
        for doctor in self._effective_doctors_for_clinic(clinic_id):
            name = str(doctor.get("name", "")).strip()
            if not name:
                continue
            slots = [str(slot) for slot in doctor.get("available_slots", []) if str(slot).strip()]
            if slots:
                options.append(f"{name} (next {slots[0]})")
            else:
                options.append(f"{name} (no open dates)")
        if options:
            return ", ".join(options)
        return "I don't have doctor options for that clinic right now."

    def _list_doctor_names(self, clinic_id: str) -> list[str]:
        names = []
        for doctor in self._effective_doctors_for_clinic(clinic_id):
            name = str(doctor.get("name", "")).strip()
            if name:
                names.append(name)
        return names

    def _build_clinic_context(self, entities: dict[str, str]) -> dict[str, Any]:
        clinic_id = entities.get("clinic_id")
        if clinic_id:
            clinic = self._get_clinic_by_id(clinic_id)
            if clinic:
                doctors = self._effective_doctors_for_clinic(clinic_id)
                context: dict[str, Any] = {
                    "mode": "selected_clinic_doctors",
                    "selected_clinic": {
                        "id": clinic.get("id"),
                        "name": clinic.get("name"),
                        "doctors": doctors,
                    },
                }
                if entities.get("doctor_id"):
                    context["selected_clinic"]["selected_doctor_id"] = entities.get("doctor_id")
                return context

        return {
            "mode": "clinic_catalog",
            "clinics": [
                {
                    "id": clinic.get("id"),
                    "name": clinic.get("name"),
                }
                for clinic in self._clinics
            ],
            "note": "Ask the user to choose a clinic before selecting a doctor and slot.",
        }

    def _effective_doctors_for_clinic(self, clinic_id: str) -> list[dict[str, Any]]:
        doctors: list[dict[str, Any]] = []
        for doctor in self._doctors:
            if doctor.get("clinic_id") != clinic_id:
                continue
            doctor_id = str(doctor.get("id", "")).strip()
            if not doctor_id:
                continue
            open_slots = self._effective_slots_for_doctor(clinic_id, doctor_id)
            doctors.append(
                {
                    "id": doctor_id,
                    "name": doctor.get("name"),
                    "available_slots": open_slots,
                    "has_open_dates": bool(open_slots),
                    "next_open_date": open_slots[0] if open_slots else None,
                }
            )
        return doctors

    def _doctors_with_open_slots(self, clinic_id: str) -> list[dict[str, Any]]:
        return [doctor for doctor in self._effective_doctors_for_clinic(clinic_id) if doctor.get("has_open_dates")]

    def _clinic_no_open_dates_message(self, clinic_id: str) -> str:
        clinic = self._get_clinic_by_id(clinic_id)
        clinic_name = str(clinic.get("name", clinic_id)).strip() if clinic else clinic_id
        alternatives = self._clinics_with_open_dates_summary(exclude_clinic_id=clinic_id)
        if alternatives:
            return (
                f"{clinic_name} has no open appointment dates right now. "
                f"Clinics with openings are: {alternatives}. Which clinic do you prefer?"
            )
        return (
            f"{clinic_name} has no open appointment dates right now. "
            "I can schedule a callback if you want."
        )

    def _doctor_no_open_dates_message(self, clinic_id: str, doctor_id: str) -> str:
        doctor = self._resolve_doctor_by_id(clinic_id, doctor_id)
        doctor_name = str(doctor.get("name", doctor_id)).strip() if doctor else doctor_id
        available_doctors = [
            str(candidate.get("name", "")).strip()
            for candidate in self._doctors_with_open_slots(clinic_id)
            if str(candidate.get("name", "")).strip()
        ]
        if available_doctors:
            return (
                f"{doctor_name} has no open dates right now. "
                f"Please choose one of these doctors with open dates: {', '.join(available_doctors)}."
            )
        return self._clinic_no_open_dates_message(clinic_id)

    def _clinics_with_open_dates_summary(self, exclude_clinic_id: str | None = None) -> str:
        entries: list[str] = []
        for clinic in self._clinics:
            cid = str(clinic.get("id", "")).strip()
            if not cid:
                continue
            if exclude_clinic_id and cid == exclude_clinic_id:
                continue
            earliest = self._earliest_open_date_for_clinic(cid)
            if earliest is None:
                continue
            name = str(clinic.get("name", cid)).strip() or cid
            entries.append(f"{name} (earliest {earliest})")
        return ", ".join(entries)

    def _earliest_open_date_for_clinic(self, clinic_id: str) -> str | None:
        earliest: str | None = None
        for doctor in self._doctors:
            if doctor.get("clinic_id") != clinic_id:
                continue
            doctor_id = str(doctor.get("id", "")).strip()
            if not doctor_id:
                continue
            for slot in self._effective_slots_for_doctor(clinic_id, doctor_id):
                if earliest is None or slot < earliest:
                    earliest = slot
        return earliest

    def _progress_key_for_action(self, action: str) -> str:
        mapping = {
            "ask_policy_id": "need_policy_id",
            "ask_clinic": "need_clinic",
            "ask_doctor_name": "need_doctor",
            "list_clinics": "need_clinic",
            "list_doctors": "need_doctor",
            "list_earliest_availability": "need_doctor",
            "confirm_doctor": "confirm_doctor",
            "ask_date": "need_date",
            "confirm_booking": "confirm_booking",
            "clarify": "clarify_appointment",
            "cancel": "cancel",
        }
        return mapping.get(action, "clarify_appointment")

    def _clarify_progress_key(self, entities: dict[str, str]) -> str:
        if not self._has_verified_policy(entities):
            return "need_policy_id"
        if not entities.get("clinic_id"):
            return "need_clinic"
        if not entities.get("doctor_id"):
            return "need_doctor"
        if entities.get("doctor_confirmed") != "true":
            return "confirm_doctor"
        if not entities.get("date"):
            return "need_date"
        return "confirm_booking"

    def _earliest_availability_message(self, entities: dict[str, str]) -> str:
        top = self._earliest_open_options(limit=3)
        if not top:
            return "I do not see any open appointment dates right now. I can schedule a callback if you want."

        first = top[0]
        summary = (
            "The earliest open option is "
            f"{first['doctor_name']} at {first['clinic_name']} on {first['date']}."
        )
        ranked = "; ".join(
            f"{entry['doctor_name']} at {entry['clinic_name']} ({entry['date']})" for entry in top
        )
        current_clinic = entities.get("clinic_id")
        if current_clinic and str(first["clinic_id"]) != str(current_clinic):
            return (
                f"{summary} Other earliest options are: {ranked}. "
                "Would you like to switch to that clinic, or stay with your current clinic?"
            )
        return f"{summary} Other earliest options are: {ranked}. Which doctor would you like to book?"

    def _earliest_open_options(self, *, limit: int = 3) -> list[dict[str, str]]:
        options: list[dict[str, str]] = []
        for clinic in self._clinics:
            clinic_id = str(clinic.get("id", "")).strip()
            clinic_name = str(clinic.get("name", clinic_id)).strip() or clinic_id
            if not clinic_id:
                continue
            for doctor in self._effective_doctors_for_clinic(clinic_id):
                doctor_id = str(doctor.get("id", "")).strip()
                doctor_name = str(doctor.get("name", doctor_id)).strip() or doctor_id
                if not doctor_id:
                    continue
                slots = [str(slot).strip() for slot in doctor.get("available_slots", []) if str(slot).strip()]
                if not slots:
                    continue
                options.append(
                    {
                        "clinic_id": clinic_id,
                        "clinic_name": clinic_name,
                        "doctor_id": doctor_id,
                        "doctor_name": doctor_name,
                        "date": slots[0],
                    }
                )
        options.sort(key=lambda item: (item["date"], item["clinic_name"], item["doctor_name"]))
        return options[:limit]

    def _progress_key_for_booking_error(self, error_code: str | None) -> str:
        mapping = {
            "missing_clinic": "need_clinic",
            "invalid_clinic": "need_clinic",
            "missing_doctor": "need_doctor",
            "invalid_doctor": "need_doctor",
            "unconfirmed_doctor": "confirm_doctor",
            "missing_date": "need_date",
            "invalid_date": "need_date",
            "unavailable_slot": "need_date",
            "missing_policy_id": "need_policy_id",
            "invalid_policy_id": "need_policy_id",
        }
        return mapping.get(error_code, "clarify_appointment")

    def _effective_slots_for_doctor(self, clinic_id: str, doctor_id: str) -> list[str]:
        doctor = self._resolve_doctor_by_id(clinic_id, doctor_id)
        if doctor is None:
            return []
        all_slots = [str(slot) for slot in doctor.get("available_slots", []) if str(slot).strip()]
        booked = self._scheduled_slots_for_doctor(clinic_id, doctor_id)
        return [slot for slot in all_slots if slot not in booked]

    def _scheduled_slots_for_doctor(self, clinic_id: str, doctor_id: str) -> set[str]:
        appointments = json.loads(self._appointments_path.read_text(encoding="utf-8"))
        booked: set[str] = set()
        clinic_lower = clinic_id.strip().lower()
        doctor_lower = doctor_id.strip().lower()
        for appointment in appointments:
            if str(appointment.get("status", "")).strip().lower() != "scheduled":
                continue
            if str(appointment.get("clinic_id", "")).strip().lower() != clinic_lower:
                continue
            if str(appointment.get("doctor_id", "")).strip().lower() != doctor_lower:
                continue
            slot = str(appointment.get("date", "")).strip()
            if slot:
                booked.add(slot)
        return booked

    def _doctor_confirmation_message(self, entities: dict[str, str]) -> str:
        clinic_id = entities.get("clinic_id")
        doctor_id = entities.get("doctor_id")
        if not clinic_id or not doctor_id:
            return "Please choose a doctor first."
        doctor = self._resolve_doctor_by_id(clinic_id, doctor_id)
        if doctor is None:
            return "Please choose a doctor from the available options."
        doctor_name = str(doctor.get("name", "this doctor")).strip() or "this doctor"
        return f"I understood {doctor_name}. Is that correct?"

    def _doctor_fully_booked_message(self, clinic_id: str) -> str:
        alternatives = [
            str(doctor.get("name", "")).strip()
            for doctor in self._doctors_with_open_slots(clinic_id)
            if str(doctor.get("name", "")).strip()
        ]
        if alternatives:
            return (
                "That doctor has no open dates right now. "
                f"Please choose one of these doctors with open dates: {', '.join(alternatives)}."
            )
        return "That doctor has no open dates right now. Please choose another clinic."

    def _booking_confirmation_message(self, entities: dict[str, str]) -> str:
        clinic_id = entities.get("clinic_id")
        doctor_id = entities.get("doctor_id")
        date_value = entities.get("date")
        policy_id = entities.get("policy_id")
        if not clinic_id or not doctor_id or not date_value or not policy_id:
            return "I still need all booking details before final confirmation."

        clinic = self._get_clinic_by_id(clinic_id)
        doctor = self._resolve_doctor_by_id(clinic_id, doctor_id)
        clinic_name = str(clinic.get("name", clinic_id)).strip() if clinic else clinic_id
        doctor_name = str(doctor.get("name", doctor_id)).strip() if doctor else doctor_id
        return (
            f"Please confirm: book {date_value} at {clinic_name} with {doctor_name} under policy {policy_id}. "
            "Reply yes to confirm or no to change details."
        )

    def _booking_fingerprint(self, entities: dict[str, str]) -> tuple[str, str, str, str]:
        return (
            entities.get("policy_id", ""),
            entities.get("clinic_id", ""),
            entities.get("doctor_id", ""),
            entities.get("date", ""),
        )

    def _upgrade_legacy_entities(self, entities: dict[str, str]) -> None:
        clinic_id = entities.get("clinic_id")
        if not clinic_id and entities.get("clinic"):
            resolved = self._resolve_clinic_id(entities["clinic"])
            if resolved:
                entities["clinic_id"] = resolved

        clinic_id = entities.get("clinic_id")
        if clinic_id and not entities.get("doctor_id") and entities.get("doctor_name"):
            doctor = self._resolve_doctor(clinic_id, entities["doctor_name"])
            if doctor is not None:
                entities["doctor_id"] = str(doctor.get("id", ""))

    def _get_clinic_by_id(self, clinic_id: str) -> dict[str, Any] | None:
        for clinic in self._clinics:
            if clinic.get("id") == clinic_id:
                return clinic
        return None

    def _is_valid_iso_date(self, value: str) -> bool:
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
        return True

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

    def _cancel_latest_scheduled_appointment(self, session_id: str, confirmed_id: str | None) -> bool:
        appointments = json.loads(self._appointments_path.read_text(encoding="utf-8"))

        cancel_index = -1
        if confirmed_id:
            for index in range(len(appointments) - 1, -1, -1):
                appointment = appointments[index]
                if appointment.get("id") == confirmed_id and appointment.get("status") == "scheduled":
                    cancel_index = index
                    break

        if cancel_index < 0:
            for index in range(len(appointments) - 1, -1, -1):
                appointment = appointments[index]
                if appointment.get("status") != "scheduled":
                    continue
                if str(appointment.get("session_id", "")) == session_id:
                    cancel_index = index
                    break

        if cancel_index < 0:
            return False

        appointments[cancel_index]["status"] = "cancelled"
        self._appointments_path.write_text(json.dumps(appointments, indent=2), encoding="utf-8")
        return True
