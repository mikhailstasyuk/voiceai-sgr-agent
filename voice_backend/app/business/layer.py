from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from .flows.appointment import AppointmentFlow
from .flows.callback_support import CallbackSupportFlow
from .flows.plan_inquiry import PlanInquiryFlow
from .flows.policy_renewal import PolicyRenewalFlow
from .intent import IntentDetector
from .models import (
    AgentResponse,
    CallbackReasoning,
    IntentType,
    OnboardingReasoning,
    SessionContext,
    SessionState,
)
from .policy_id_capture import sanitize_candidate_selection, set_candidate_selection_schema_field
from .policy_store import PolicyStore
from .schema_utils import to_groq_strict_schema
from .session import SessionStore
from .sgr import INTENT_REROUTE_MARKER, call_structured_json, normalize_requested_flow

log = logging.getLogger("hypercheap.business.layer")


class BusinessLayer:
    _ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
    _LIGHT_NOISE_RE = re.compile(r"^[^a-zA-Z0-9]{0,3}[a-zA-Z]{1,8}[^a-zA-Z0-9]{0,3}$")
    _CALLBACK_RE = re.compile(r"\b(callback|call back|call me|call)\b", re.IGNORECASE)
    _CLIENT_STATUS_RE = re.compile(r"\b(client|customer)\b", re.IGNORECASE)
    _GEORGIA_COUNTRY_CODE = "995"
    _GEORGIA_LOCAL_DIGITS = 9
    _GEORGIA_MOBILE_LEAD = "5"

    def __init__(self, groq_client, model_name: str, session_store: SessionStore) -> None:
        self._groq_client = groq_client
        self._model_name = model_name
        self._session_store = session_store
        data_dir = Path(__file__).resolve().parent / "data"
        self._policy_store = PolicyStore(data_dir=data_dir)
        self._intent_detector = IntentDetector(groq_client=groq_client, model_name=model_name)
        self._appointment_flow = AppointmentFlow()
        self._policy_renewal_flow = PolicyRenewalFlow(policy_store=self._policy_store)
        self._plan_inquiry_flow = PlanInquiryFlow(policy_store=self._policy_store)
        self._callback_requests_path = data_dir / "callback_requests.json"
        self._callback_support_flow = CallbackSupportFlow(callback_requests_path=self._callback_requests_path)

    async def process(self, text: str, session_id: str) -> AgentResponse:
        turn_t0 = time.perf_counter()
        ctx = self._session_store.get_or_create(session_id)
        log.info(
            "[turn:start] session_id=%s state=%s intent_attempts=%d user_text=%r",
            session_id,
            ctx.state.value,
            ctx.intent_attempts,
            text,
        )
        self._append_history(ctx, "user", text)

        response = await self._process_state(text=text, ctx=ctx)
        response.text = self._humanize_dates(response.text)

        self._append_history(ctx, "assistant", response.text)
        self._session_store.update(ctx)
        log.info(
            (
                "[turn:end] session_id=%s state=%s should_end_session=%s "
                "assistant_text=%r pending_entities=%s latency_ms=%.2f"
            ),
            session_id,
            ctx.state.value,
            response.should_end_session,
            response.text,
            ctx.pending_entities,
            (time.perf_counter() - turn_t0) * 1000.0,
        )
        return response

    async def _process_state(self, text: str, ctx: SessionContext) -> AgentResponse:
        if ctx.onboarding_stage != "completed" and ctx.state in (SessionState.IDLE, SessionState.COMPLETED):
            return await self._handle_onboarding(text, ctx)

        if ctx.state in (SessionState.IDLE, SessionState.COMPLETED):
            client_status_response = await self._maybe_handle_client_status_correction(text, ctx)
            if client_status_response is not None:
                return client_status_response
            return await self._handle_intent_entry(text, ctx)

        if ctx.state == SessionState.AWAITING_CLARIFICATION:
            client_status_response = await self._maybe_handle_client_status_correction(text, ctx)
            if client_status_response is not None:
                return client_status_response
            return await self._handle_intent_entry(text, ctx)

        if ctx.state == SessionState.IN_FLOW:
            return await self._execute_flow(text, ctx)

        if ctx.state == SessionState.SCHEDULING_CALLBACK:
            return await self._handle_callback_capture(text, ctx)

        return AgentResponse(text="Could you please repeat that?")

    async def _handle_onboarding(self, text: str, ctx: SessionContext) -> AgentResponse:
        if ctx.onboarding_stage is None:
            ctx.onboarding_stage = "awaiting_client_status"
            return AgentResponse(text="Hello, thanks for calling. Are you our client?")

        if ctx.onboarding_stage == "awaiting_client_status":
            reasoning = await self._reason_onboarding(text=text, session_ctx=ctx)
            if reasoning.action == "confirm_client":
                ctx.is_known_client = True
                ctx.onboarding_stage = "completed"
                ctx.state = SessionState.IDLE
                return AgentResponse(
                    text=(
                        "Thanks for confirming. I can help with appointments, policy renewals, "
                        "plan inquiries, and callback support."
                    )
                )
            if reasoning.action == "not_client":
                ctx.is_known_client = False
                ctx.onboarding_stage = "awaiting_become_client"
                return AgentResponse(
                    text=(
                        "Would you like to become one? I can tell you about our plans and then "
                        "schedule a callback, or schedule a callback right away."
                    )
                )
            return AgentResponse(text="Please answer yes or no: are you our client?")

        if ctx.onboarding_stage == "awaiting_become_client":
            reasoning = await self._reason_onboarding(text=text, session_ctx=ctx)
            if reasoning.action == "callback_now":
                ctx.onboarding_stage = "completed"
                ctx.state = SessionState.SCHEDULING_CALLBACK
                ctx.callback_mode = "collect_phone"
                ctx.callback_date_iso = None
                return AgentResponse(text="Sure. What Georgian mobile number should we call?")
            if reasoning.action == "plans_then_callback":
                plans = self._policy_store.list_plans()
                plan_text = ", ".join(f"{p['name']} (${p['monthly_price_usd']}/month)" for p in plans)
                ctx.onboarding_stage = "completed"
                ctx.state = SessionState.SCHEDULING_CALLBACK
                ctx.callback_mode = "collect_phone"
                ctx.callback_date_iso = None
                return AgentResponse(
                    text=(
                        f"Our plans are: {plan_text}. If you want, I can arrange a callback now. "
                        "What Georgian mobile number should we call?"
                    )
                )
            if reasoning.action == "decline":
                ctx.onboarding_stage = "completed"
                ctx.state = SessionState.COMPLETED
                return AgentResponse(text="No problem. If you change your mind, I can help anytime.")

            return AgentResponse(
                text="Would you like plan information and a callback, or should I schedule a callback right away?"
            )

        ctx.onboarding_stage = "completed"
        return AgentResponse(text="How can I help you today?")

    async def _handle_intent_entry(self, text: str, ctx: SessionContext) -> AgentResponse:
        t0 = time.perf_counter()
        intent_result = await self._intent_detector.detect(text, ctx)
        log.info(
            "[intent] session_id=%s intent=%s confidence=%.3f extracted_entities=%s reasoning=%r latency_ms=%.2f",
            ctx.session_id,
            intent_result.intent.value,
            intent_result.confidence,
            intent_result.extracted_entities,
            intent_result.reasoning,
            (time.perf_counter() - t0) * 1000.0,
        )

        if intent_result.intent == IntentType.UNCLEAR:
            if ctx.state == SessionState.COMPLETED:
                if self._wants_callback(text):
                    ctx.state = SessionState.SCHEDULING_CALLBACK
                    ctx.callback_mode = "collect_phone"
                    ctx.callback_digits_buffer = ""
                    ctx.callback_date_iso = None
                    return AgentResponse(text="I can arrange a callback. What Georgian mobile number should we call?")
                return AgentResponse(
                    text=(
                        "Your previous request is already closed. "
                        "If you want, I can help with appointments, renewals, "
                        "plan questions, callback support, or a callback."
                    )
                )
            ctx.intent_attempts += 1
            if ctx.intent_attempts >= 3:
                if ctx.pending_entities:
                    ctx.last_booking_context = dict(ctx.pending_entities)
                ctx.pending_entities = {}
                ctx.active_flow = None
                ctx.state = SessionState.SCHEDULING_CALLBACK
                ctx.callback_mode = "collect_phone"
                ctx.callback_digits_buffer = ""
                ctx.callback_date_iso = None
                log.info(
                    "[decision] session_id=%s action=escalate_callback reason=unclear_intent intent_attempts=%d",
                    ctx.session_id,
                    ctx.intent_attempts,
                )
                return AgentResponse(
                    text=(
                        "I'm having trouble understanding. I can arrange a callback. "
                        "What Georgian mobile number should we call?"
                    )
                )
            ctx.state = SessionState.AWAITING_CLARIFICATION
            log.info(
                "[decision] session_id=%s action=request_clarification intent_attempts=%d",
                ctx.session_id,
                ctx.intent_attempts,
            )
            return AgentResponse(
                text=(
                    "I can help with appointments, policy renewals, or plan inquiries. "
                    "I can also help with callback status or rescheduling. "
                    "Please tell me which one you need."
                )
            )

        ctx.intent_attempts = 0
        ctx.callback_mode = None
        ctx.callback_resume_text = None
        ctx.callback_digits_buffer = ""
        ctx.callback_date_iso = None
        ctx.pending_entities.update(intent_result.extracted_entities)
        if intent_result.intent == IntentType.APPOINTMENT:
            if ctx.state == SessionState.COMPLETED and ctx.last_booking_context and not ctx.pending_entities:
                ctx.pending_entities.update(ctx.last_booking_context)
            ctx.active_flow = "appointment"
        elif intent_result.intent == IntentType.POLICY_RENEWAL:
            ctx.active_flow = "policy_renewal"
        elif intent_result.intent == IntentType.PLAN_INQUIRY:
            ctx.active_flow = "plan_inquiry"
        elif intent_result.intent == IntentType.CALLBACK_SUPPORT:
            ctx.active_flow = "callback_support"
        else:
            ctx.active_flow = None
        self._prune_entities_for_active_flow(ctx)
        ctx.state = SessionState.IN_FLOW
        log.info(
            "[decision] session_id=%s action=enter_flow flow=%s pending_entities=%s",
            ctx.session_id,
            ctx.active_flow,
            ctx.pending_entities,
        )
        return await self._execute_flow(text, ctx)

    async def _handle_callback_capture(self, text: str, ctx: SessionContext) -> AgentResponse:
        mode = ctx.callback_mode or "collect_phone"
        raw_digits_chunk = self._extract_phone_digits(text)
        raw_candidates = self._build_callback_phone_candidates(
            digits_chunk=raw_digits_chunk,
            current_buffer=ctx.callback_digits_buffer,
            source="raw",
        )
        reasoning = await self._reason_callback(
            text=text,
            session_ctx=ctx,
            candidate_ids=[candidate["id"] for candidate in raw_candidates],
            candidates_context=self._callback_candidates_context(raw_candidates),
        )
        if (
            mode in {None, "collect_phone", "confirm_phone", "collect_date", "confirm_date"}
            and reasoning.action == "confirm_switch_to_booking"
            and not self._looks_like_booking_switch_request(text)
        ):
            reasoning = CallbackReasoning(
                action="ask_phone" if mode in {None, "collect_phone", "confirm_phone"} else "ask_callback_date",
                message_to_user=(
                    "I can continue this callback setup. "
                    "If you want to switch to booking, please say that directly."
                ),
                extracted_phone=reasoning.extracted_phone,
                selected_phone_candidate_id=None,
                extracted_callback_date=reasoning.extracted_callback_date,
            )

        extracted_digits_chunk = self._extract_phone_digits(reasoning.extracted_phone or "")
        extracted_candidates = self._build_callback_phone_candidates(
            digits_chunk=extracted_digits_chunk,
            current_buffer=ctx.callback_digits_buffer,
            source="extracted",
        )
        all_candidates = raw_candidates + extracted_candidates
        selected_candidate_id = sanitize_candidate_selection(
            selected_candidate_id=reasoning.selected_phone_candidate_id,
            candidate_ids=[candidate["id"] for candidate in all_candidates],
        ) or ""
        selected_candidate = self._find_callback_candidate(all_candidates, selected_candidate_id)
        if selected_candidate is None:
            selected_candidate = self._pick_best_callback_candidate(all_candidates)
        before_buffer = ctx.callback_digits_buffer
        if selected_candidate is not None:
            ctx.callback_digits_buffer = selected_candidate["buffer"]
        normalized_phone = self._normalize_georgian_mobile_digits(ctx.callback_digits_buffer)

        candidate_date = self._normalize_callback_date_iso(reasoning.extracted_callback_date or text)
        if candidate_date is not None:
            ctx.callback_date_iso = candidate_date
        log.info(
            (
                "[callback:state] session_id=%s mode=%s action=%s raw_chunk=%r extracted_chunk=%r "
                "selected_candidate_id=%s before_buffer=%r after_buffer=%r normalized_phone=%r callback_date=%r"
            ),
            ctx.session_id,
            mode,
            reasoning.action,
            raw_digits_chunk,
            extracted_digits_chunk,
            selected_candidate.get("id") if selected_candidate else "",
            before_buffer,
            ctx.callback_digits_buffer,
            normalized_phone,
            ctx.callback_date_iso,
        )

        if reasoning.action == "switch_to_booking":
            resume_text = ctx.callback_resume_text or text
            ctx.callback_mode = None
            ctx.callback_resume_text = None
            ctx.callback_digits_buffer = ""
            ctx.callback_date_iso = None
            ctx.state = SessionState.IDLE
            ctx.intent_attempts = 0
            log.info("[decision] session_id=%s action=callback_exit_to_booking", ctx.session_id)
            return await self._handle_intent_entry(resume_text, ctx)

        if reasoning.action == "confirm_switch_to_booking":
            ctx.callback_mode = "confirm_exit"
            ctx.callback_resume_text = text
            return AgentResponse(text=reasoning.message_to_user)

        if reasoning.action == "cancel":
            ctx.callback_mode = None
            ctx.callback_resume_text = None
            ctx.callback_digits_buffer = ""
            ctx.callback_date_iso = None
            ctx.state = SessionState.COMPLETED
            return AgentResponse(text=reasoning.message_to_user)

        if mode == "collect_phone" and normalized_phone is not None:
            ctx.callback_mode = "confirm_phone"
            return AgentResponse(text=f"I captured {normalized_phone}. Is this the best number to call?")

        if mode == "confirm_phone" and reasoning.action == "ask_phone":
            ctx.callback_digits_buffer = ""
            ctx.callback_mode = "collect_phone"
            return AgentResponse(text="Okay, please provide your Georgian mobile number again.")

        if mode == "confirm_phone" and reasoning.action == "confirm_callback" and normalized_phone is not None:
            ctx.pending_entities["callback_phone"] = normalized_phone
            ctx.callback_mode = "collect_date"
            return AgentResponse(
                text="What date works best for your callback? Please say it in YYYY-MM-DD format."
            )

        if mode == "collect_date":
            if ctx.callback_date_iso is None:
                return AgentResponse(
                    text="Please share a callback date in YYYY-MM-DD format."
                )
            ctx.callback_mode = "confirm_date"
            return AgentResponse(text=f"I captured {ctx.callback_date_iso}. Is that callback date correct?")

        if mode == "confirm_date" and reasoning.action == "ask_callback_date":
            ctx.callback_date_iso = None
            ctx.callback_mode = "collect_date"
            return AgentResponse(text="Okay, please provide the callback date again in YYYY-MM-DD format.")

        if mode == "confirm_date" and reasoning.action == "confirm_callback":
            callback_phone = normalized_phone or ctx.pending_entities.get("callback_phone", "")
            callback_date = ctx.callback_date_iso
            if callback_phone and callback_date:
                if not self._callback_exists(ctx.session_id, callback_phone, callback_date):
                    self._append_callback_request(ctx.session_id, callback_phone, callback_date)
                ctx.state = SessionState.COMPLETED
                ctx.callback_mode = None
                ctx.callback_resume_text = None
                ctx.callback_digits_buffer = ""
                ctx.callback_date_iso = None
                ctx.active_flow = None
                ctx.pending_entities = {}
                log.info(
                    "[decision] session_id=%s action=callback_scheduled phone=%s callback_date=%s",
                    ctx.session_id,
                    callback_phone,
                    callback_date,
                )
                return AgentResponse(
                    text=f"Thanks. I scheduled your callback for {callback_date} at {callback_phone}."
                )

        ctx.callback_mode = {
            "confirm_phone": "confirm_phone",
            "collect_date": "collect_date",
            "confirm_date": "confirm_date",
            "confirm_exit": "collect_phone",
        }.get(mode, "collect_phone")
        if mode == "collect_phone":
            remaining = max(self._GEORGIA_LOCAL_DIGITS - len(ctx.callback_digits_buffer), 0)
            if remaining > 0:
                return AgentResponse(
                    text=(
                        "Please continue with your Georgian mobile number. "
                        f"I still need about {remaining} more digits after +995."
                    )
                )
        return AgentResponse(text=reasoning.message_to_user)

    async def _reason_callback(
        self,
        text: str,
        session_ctx: SessionContext,
        candidate_ids: list[str],
        candidates_context: list[dict[str, str]],
    ) -> CallbackReasoning:
        history = list(session_ctx.conversation_history[-4:])
        mode = session_ctx.callback_mode or "collect_phone"
        callback_context = {
            "callback_mode": mode,
            "resume_text": session_ctx.callback_resume_text,
            "phone_target": "Georgian mobile, +995 followed by 9 digits starting with 5",
            "captured_phone": self._normalize_georgian_mobile_digits(session_ctx.callback_digits_buffer),
            "captured_callback_date": session_ctx.callback_date_iso,
            "phone_candidates": candidates_context,
            "note": (
                "If user asks to switch back to assisted service handling, use confirm_switch_to_booking first. "
                "Use switch_to_booking only after explicit user confirmation. "
                "For callback date capture, output extracted_callback_date as ISO YYYY-MM-DD when date is provided."
            ),
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "You are the callback scheduling controller. "
                    "Return strict JSON only. Choose action from allowed actions for current callback_mode. "
                    "When callback_mode is collect_phone: do not ask to switch unless user explicitly asks for booking "
                    "or another service. Negative/unclear words alone are not switch intent. "
                    "For phone capture: prioritize Georgian mobile normalization (+995 plus 9 digits). "
                    "Use selected_phone_candidate_id when any candidate fits user intent. "
                    "If no candidate applies, set selected_phone_candidate_id to __NONE__. "
                    "When callback_mode is confirm_phone: if user confirms the captured number use confirm_callback; "
                    "if user rejects/corrects it use ask_phone. "
                    "When callback_mode is collect_date: use ask_callback_date until date is captured. "
                    "When callback_mode is confirm_date: use confirm_callback for explicit confirmation or "
                    "ask_callback_date when caller rejects/corrects the date."
                ),
            },
            {"role": "system", "content": f"Callback context: {json.dumps(callback_context)}"},
            *history,
        ]
        if not history or history[-1].get("role") != "user" or history[-1].get("content") != text:
            messages.append({"role": "user", "content": text})

        schema = self._callback_reasoning_schema(mode, candidate_ids)
        try:
            payload = await self._call_structured("callback_reasoning", schema, messages)
            reasoning = CallbackReasoning.model_validate(payload)
            return self._sanitize_callback_reasoning(reasoning=reasoning, mode=mode, text=text)
        except Exception:
            correction_messages = messages + [
                {
                    "role": "system",
                    "content": (
                        "Previous output was invalid. Return ONLY valid JSON that matches the schema exactly."
                    ),
                }
            ]
            try:
                payload = await self._call_structured("callback_reasoning", schema, correction_messages)
                reasoning = CallbackReasoning.model_validate(payload)
                return self._sanitize_callback_reasoning(reasoning=reasoning, mode=mode, text=text)
            except Exception:
                return CallbackReasoning(
                    action="ask_phone",
                    message_to_user=(
                        "Please share a Georgian mobile number. "
                        "You can say plus nine nine five, then nine digits."
                    ),
                    extracted_phone=None,
                    selected_phone_candidate_id=None,
                    extracted_callback_date=None,
                )

    def _callback_reasoning_schema(self, mode: str, candidate_ids: list[str]) -> dict[str, Any]:
        schema = to_groq_strict_schema(CallbackReasoning.model_json_schema())
        properties = schema.get("properties", {})
        action_field = properties.get("action")
        if not isinstance(action_field, dict):
            return schema
        allowed = self._allowed_callback_actions(mode)
        action_field.clear()
        action_field.update(
            {
                "type": "string",
                "enum": allowed,
                "description": f"Allowed callback actions for mode {mode}.",
            }
        )
        selected_field = properties.get("selected_phone_candidate_id")
        if isinstance(selected_field, dict):
            set_candidate_selection_schema_field(
                schema=schema,
                field_name="selected_phone_candidate_id",
                candidate_ids=candidate_ids,
                when_candidates_description="Choose one callback phone candidate id from context.",
                when_empty_description="No callback candidate available this turn; use __NONE__.",
            )
        return schema

    def _allowed_callback_actions(self, mode: str) -> list[str]:
        if mode == "confirm_exit":
            return ["switch_to_booking", "ask_phone", "cancel"]
        if mode == "collect_date":
            return ["ask_callback_date", "confirm_switch_to_booking", "cancel"]
        if mode == "confirm_date":
            return ["confirm_callback", "ask_callback_date", "confirm_switch_to_booking", "cancel"]
        if mode == "confirm_phone":
            return ["confirm_callback", "ask_phone", "confirm_switch_to_booking", "cancel"]
        return ["ask_phone", "confirm_switch_to_booking", "cancel"]

    def _sanitize_callback_reasoning(self, reasoning: CallbackReasoning, mode: str, text: str) -> CallbackReasoning:
        allowed = set(self._allowed_callback_actions(mode))
        if reasoning.action not in allowed:
            return CallbackReasoning(
                action="ask_phone" if mode in {"collect_phone", "confirm_phone", "confirm_exit"} else "ask_callback_date",
                message_to_user=(
                    "Please continue with your Georgian mobile number."
                    if mode in {"collect_phone", "confirm_phone", "confirm_exit"}
                    else "Please continue with the callback date in YYYY-MM-DD format."
                ),
                extracted_phone=reasoning.extracted_phone,
                selected_phone_candidate_id=None,
                extracted_callback_date=reasoning.extracted_callback_date,
            )
        if mode == "collect_phone" and reasoning.action == "confirm_switch_to_booking":
            if not self._looks_like_booking_switch_request(text):
                return CallbackReasoning(
                    action="ask_phone",
                    message_to_user=(
                        "Please continue with your Georgian callback phone number. "
                        "If you want booking instead, say that directly."
                    ),
                    extracted_phone=reasoning.extracted_phone,
                    selected_phone_candidate_id=None,
                    extracted_callback_date=reasoning.extracted_callback_date,
                )
        if mode in {"confirm_phone", "collect_date", "confirm_date"}:
            reasoning.selected_phone_candidate_id = None
        return reasoning

    def _looks_like_booking_switch_request(self, text: str) -> bool:
        lowered = text.lower()
        switch_terms = ("book", "appointment", "renew", "plan", "clinic", "instead", "switch")
        return any(term in lowered for term in switch_terms)

    async def _reason_onboarding(
        self,
        text: str,
        session_ctx: SessionContext,
        *,
        stage_override: str | None = None,
    ) -> OnboardingReasoning:
        history = list(session_ctx.conversation_history[-4:])
        stage = stage_override or session_ctx.onboarding_stage or "awaiting_client_status"
        messages = [
            {
                "role": "system",
                "content": (
                    "You are the onboarding controller for an insurance voice agent. "
                    "Return strict JSON only and choose the next action from the stage-specific options. "
                    "Infer user meaning from noisy ASR/STT text and nearby context. "
                    "Use confirm_client for affirmative answers like yes/yep/sure/that is right. "
                    "Use not_client for negative answers like no/not yet/not a client/uh no. "
                    "When stage is awaiting_become_client, use plans_then_callback if user wants to learn plans first, "
                    "callback_now if they want immediate callback, decline if they reject both, clarify otherwise."
                ),
            },
            {
                "role": "system",
                "content": (
                    "Onboarding stage context: "
                    f"{json.dumps({'stage': stage, 'is_known_client': session_ctx.is_known_client})}"
                ),
            },
            *history,
        ]
        if not history or history[-1].get("role") != "user" or history[-1].get("content") != text:
            messages.append({"role": "user", "content": text})

        schema = self._onboarding_reasoning_schema(stage)
        try:
            payload = await self._call_structured("onboarding_reasoning", schema, messages)
            reasoning = OnboardingReasoning.model_validate(payload)
            return self._sanitize_onboarding_reasoning(reasoning, stage)
        except Exception:
            correction_messages = messages + [
                {
                    "role": "system",
                    "content": "Previous output was invalid. Return ONLY valid JSON matching the schema.",
                }
            ]
            try:
                payload = await self._call_structured("onboarding_reasoning", schema, correction_messages)
                reasoning = OnboardingReasoning.model_validate(payload)
                return self._sanitize_onboarding_reasoning(reasoning, stage)
            except Exception:
                return self._onboarding_fallback(stage)

    def _onboarding_reasoning_schema(self, stage: str) -> dict[str, Any]:
        schema = to_groq_strict_schema(OnboardingReasoning.model_json_schema())
        properties = schema.get("properties", {})
        action_field = properties.get("action")
        if not isinstance(action_field, dict):
            return schema
        allowed = self._allowed_onboarding_actions(stage)
        action_field.clear()
        action_field.update(
            {
                "type": "string",
                "enum": allowed,
                "description": f"Allowed actions for stage {stage}.",
            }
        )
        return schema

    def _allowed_onboarding_actions(self, stage: str) -> list[str]:
        if stage == "awaiting_become_client":
            return ["plans_then_callback", "callback_now", "decline", "clarify"]
        return ["confirm_client", "not_client", "clarify"]

    def _sanitize_onboarding_reasoning(self, reasoning: OnboardingReasoning, stage: str) -> OnboardingReasoning:
        allowed = set(self._allowed_onboarding_actions(stage))
        if reasoning.action not in allowed:
            return self._onboarding_fallback(stage)
        return reasoning

    def _onboarding_fallback(self, stage: str) -> OnboardingReasoning:
        if stage == "awaiting_become_client":
            return OnboardingReasoning(
                action="clarify",
                message_to_user=(
                    "Would you like plan information and a callback, or should I schedule a callback right away?"
                ),
            )
        return OnboardingReasoning(
            action="clarify",
            message_to_user="Please answer yes or no: are you our client?",
        )

    async def _maybe_handle_client_status_correction(self, text: str, ctx: SessionContext) -> AgentResponse | None:
        if ctx.onboarding_stage != "completed":
            return None
        if ctx.is_known_client is not True:
            return None
        if not self._CLIENT_STATUS_RE.search(text):
            return None

        reasoning = await self._reason_onboarding(
            text=text,
            session_ctx=ctx,
            stage_override="awaiting_client_status",
        )
        if reasoning.action == "not_client":
            ctx.is_known_client = False
            ctx.onboarding_stage = "awaiting_become_client"
            ctx.state = SessionState.IDLE
            ctx.active_flow = None
            ctx.pending_entities = {}
            return AgentResponse(
                text=(
                    "Understood. Would you like to become one? I can tell you about our plans "
                    "and then schedule a callback, or schedule a callback right away."
                )
            )
        if reasoning.action == "confirm_client":
            return AgentResponse(
                text=(
                    "Thanks for confirming. I can help with appointments, policy renewals, "
                    "plan inquiries, and callback support."
                )
            )
        return None

    async def _call_structured(
        self,
        schema_name: str,
        schema: dict[str, Any],
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        return await call_structured_json(
            client=self._groq_client,
            model_name=self._model_name,
            schema_name=schema_name,
            schema=schema,
            messages=messages,
        )

    async def _execute_flow(self, text: str, ctx: SessionContext) -> AgentResponse:
        t0 = time.perf_counter()
        previous_flow = ctx.active_flow or "appointment"
        previous_entities = dict(ctx.pending_entities)
        if ctx.active_flow == "policy_renewal":
            result = await self._policy_renewal_flow.execute(
                text=text,
                session_ctx=ctx,
                groq_client=self._groq_client,
                model_name=self._model_name,
            )
        elif ctx.active_flow == "plan_inquiry":
            result = await self._plan_inquiry_flow.execute(
                text=text,
                session_ctx=ctx,
                groq_client=self._groq_client,
                model_name=self._model_name,
            )
        elif ctx.active_flow == "callback_support":
            result = await self._callback_support_flow.execute(
                text=text,
                session_ctx=ctx,
                groq_client=self._groq_client,
                model_name=self._model_name,
            )
        else:
            result = await self._appointment_flow.execute(
                text=text,
                session_ctx=ctx,
                groq_client=self._groq_client,
                model_name=self._model_name,
            )
        log.info(
            (
                "[flow] session_id=%s flow=%s completed=%s "
                "schedule_callback=%s progress_key=%s updated_entities=%s response_text=%r latency_ms=%.2f"
            ),
            ctx.session_id,
            ctx.active_flow or "appointment",
            result.completed,
            result.schedule_callback,
            result.progress_key,
            result.updated_entities,
            result.response_text,
            (time.perf_counter() - t0) * 1000.0,
        )
        if result.updated_entities:
            ctx.pending_entities.update(result.updated_entities)
        if ctx.pending_entities.get("handoff") == "non_client_options":
            ctx.pending_entities.pop("handoff", None)
            ctx.active_flow = None
            ctx.state = SessionState.IDLE
            ctx.is_known_client = False
            ctx.onboarding_stage = "awaiting_become_client"

        requested_flow = normalize_requested_flow(
            ctx.pending_entities.pop("requested_flow", None),
            current_flow=ctx.active_flow,
        )
        if requested_flow == INTENT_REROUTE_MARKER:
            reroute_depth = ctx.flow_counters.get("intent_reroute_depth", 0)
            if reroute_depth >= 1:
                return AgentResponse(
                    text=(
                        "Please tell me which service you need: appointment, policy renewal, "
                        "plan inquiry, or callback support."
                    )
                )
            ctx.flow_counters["intent_reroute_depth"] = reroute_depth + 1
            ctx.active_flow = None
            ctx.state = SessionState.IDLE
            try:
                return await self._handle_intent_entry(text, ctx)
            finally:
                ctx.flow_counters["intent_reroute_depth"] = reroute_depth

        if result.schedule_callback:
            ctx.state = SessionState.SCHEDULING_CALLBACK
            self._reset_flow_stall_counter(ctx, previous_flow)
            log.info("[decision] session_id=%s action=flow_schedule_callback", ctx.session_id)
            return AgentResponse(text=result.response_text)

        response_text = self._maybe_prepend_expiry_notice(result.response_text, ctx)
        if requested_flow in {"appointment", "policy_renewal", "plan_inquiry", "callback_support"}:
            ctx.active_flow = requested_flow
            self._prune_entities_for_active_flow(ctx)
            ctx.state = SessionState.IN_FLOW
            self._reset_flow_stall_counter(ctx, previous_flow)
            return AgentResponse(text=response_text)

        if result.completed:
            confirmed_id = ctx.pending_entities.get("confirmed_appointment_id")
            ctx.state = SessionState.COMPLETED
            ctx.active_flow = None
            ctx.pending_entities = {}
            if confirmed_id:
                ctx.last_confirmed_appointment_id = confirmed_id
            self._reset_flow_stall_counter(ctx, previous_flow)
            log.info("[decision] session_id=%s action=flow_completed", ctx.session_id)
            return AgentResponse(text=response_text)

        if self._is_flow_stalled(
            ctx=ctx,
            current_flow=previous_flow,
            previous_entities=previous_entities,
            progress_key=result.progress_key,
        ):
            return AgentResponse(text=self._stall_recovery_message(previous_flow, result.progress_key))

        return AgentResponse(text=response_text)

    def _is_flow_stalled(
        self,
        *,
        ctx: SessionContext,
        current_flow: str,
        previous_entities: dict[str, str],
        progress_key: str | None,
    ) -> bool:
        if ctx.active_flow != current_flow:
            self._reset_flow_stall_counter(ctx, current_flow)
            return False

        if not progress_key:
            self._reset_flow_stall_counter(ctx, current_flow)
            return False

        entity_progress = dict(ctx.pending_entities) != previous_entities
        counter_key = f"stall:{current_flow}:count"
        key_key = f"stall:{current_flow}:key"
        previous_key = str(ctx.flow_counters.get(key_key, ""))

        if entity_progress:
            ctx.flow_counters[counter_key] = 0
            ctx.flow_counters[key_key] = progress_key
            return False

        if previous_key != progress_key:
            ctx.flow_counters[key_key] = progress_key
            ctx.flow_counters[counter_key] = 1
            return False

        repeats = int(ctx.flow_counters.get(counter_key, 0)) + 1
        ctx.flow_counters[counter_key] = repeats
        return repeats >= 3

    def _reset_flow_stall_counter(self, ctx: SessionContext, flow_name: str) -> None:
        ctx.flow_counters[f"stall:{flow_name}:count"] = 0
        ctx.flow_counters[f"stall:{flow_name}:key"] = ""

    def _stall_recovery_message(self, flow_name: str, progress_key: str | None) -> str:
        if flow_name == "appointment":
            appointment_by_step = {
                "need_policy_id": "Let's reset this step. Please share your policy id in POL-1234 format.",
                "need_clinic": "Let's reset this step. Please choose a clinic, and I can list clinic options.",
                "need_doctor": "Let's reset this step. Please choose a doctor, and I can list doctor options.",
                "confirm_doctor": "Let's reset this step. Please confirm the selected doctor with yes or no.",
                "need_date": "Let's reset this step. Please share your preferred appointment date in YYYY-MM-DD format.",
                "confirm_booking": "Let's reset this step. Please confirm the booking summary with yes or no.",
                "clarify_appointment": (
                    "Let's reset this step. Please tell me which detail to continue with: clinic, doctor, date, "
                    "or booking confirmation."
                ),
                "clinic_no_open_dates": (
                    "That clinic has no open dates right now. Please choose a different clinic, "
                    "or ask me to arrange a callback."
                ),
                "doctor_no_open_dates": (
                    "That doctor has no open dates right now. Please choose another doctor or clinic."
                ),
            }
            if progress_key and progress_key in appointment_by_step:
                return appointment_by_step[progress_key]

        defaults = {
            "appointment": "Let's reset this step. Please share your policy id in POL-1234 format.",
            "policy_renewal": "Let's reset this step. Please share your policy id in POL-1234 format.",
            "plan_inquiry": "Let's reset this step. Do you want plan comparison or plan renewal?",
            "callback_support": "Let's reset this step. Do you want callback status or callback reschedule?",
        }
        return defaults.get(
            flow_name,
            "Let's reset this step. Please tell me which service you need: appointment, renewal, plan, or callback support.",
        )

    def _prune_entities_for_active_flow(self, ctx: SessionContext) -> None:
        if not ctx.active_flow:
            return
        allowed_by_flow: dict[str, set[str]] = {
            "appointment": {
                "policy_id",
                "clinic_id",
                "doctor_id",
                "date",
                "doctor_confirmed",
                "booking_confirmation_pending",
                "confirmed_appointment_id",
            },
            "policy_renewal": {"policy_id", "selected_plan_id"},
            "plan_inquiry": {"policy_id", "selected_plan_id"},
            "callback_support": set(),
        }
        allowed = allowed_by_flow.get(ctx.active_flow)
        if allowed is None:
            return
        for key in list(ctx.pending_entities.keys()):
            if key in {"requested_flow", "handoff"}:
                continue
            if key in {"policy_id_conflict_candidate", "renewal_confirmation_pending"}:
                continue
            if key not in allowed:
                ctx.pending_entities.pop(key, None)

    def _callback_exists(self, session_id: str, phone: str, callback_date: str) -> bool:
        requests = self._read_callback_requests()
        for request in requests:
            if (
                request.get("session_id") == session_id
                and request.get("phone") == phone
                and request.get("callback_date") == callback_date
            ):
                return True
        return False

    def _append_callback_request(self, session_id: str, phone: str, callback_date: str) -> None:
        requests = self._read_callback_requests()
        requests.append(
            {
                "id": f"cb_{uuid4().hex[:8]}",
                "session_id": session_id,
                "phone": phone,
                "callback_date": callback_date,
                "status": "queued",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._callback_requests_path.write_text(json.dumps(requests, indent=2), encoding="utf-8")

    def _read_callback_requests(self) -> list[dict[str, str]]:
        if not self._callback_requests_path.exists():
            return []
        raw = json.loads(self._callback_requests_path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return raw
        return []

    def _normalize_georgian_mobile_digits(self, digits: str) -> str | None:
        if len(digits) != self._GEORGIA_LOCAL_DIGITS:
            return None
        if not digits.startswith(self._GEORGIA_MOBILE_LEAD):
            return None
        return f"+{self._GEORGIA_COUNTRY_CODE}{digits}"

    def _normalize_callback_date_iso(self, value: str) -> str | None:
        match = self._ISO_DATE.search(value)
        if not match:
            return None
        candidate = match.group(0)
        try:
            parsed = date.fromisoformat(candidate)
        except ValueError:
            return None
        if parsed < date.today():
            return None
        return parsed.isoformat()

    def _extract_phone_digits(self, text: str) -> str:
        digit_words = {
            "zero": "0",
            "oh": "0",
            "o": "0",
            "one": "1",
            "two": "2",
            "three": "3",
            "four": "4",
            "five": "5",
            "six": "6",
            "seven": "7",
            "eight": "8",
            "nine": "9",
        }
        teen_words = {
            "ten": "10",
            "eleven": "11",
            "twelve": "12",
            "thirteen": "13",
            "fourteen": "14",
            "fifteen": "15",
            "sixteen": "16",
            "seventeen": "17",
            "eighteen": "18",
            "nineteen": "19",
        }
        tens_words = {
            "twenty": "2",
            "thirty": "3",
            "forty": "4",
            "fifty": "5",
            "sixty": "6",
            "seventy": "7",
            "eighty": "8",
            "ninety": "9",
        }
        tokens = re.findall(r"[a-zA-Z]+|\d+", text.lower())
        digits: list[str] = []
        idx = 0
        while idx < len(tokens):
            token = tokens[idx]
            if token.isdigit():
                digits.append(token)
                idx += 1
                continue
            mapped_digit = digit_words.get(token)
            if mapped_digit:
                digits.append(mapped_digit)
                idx += 1
                continue
            mapped_teen = teen_words.get(token)
            if mapped_teen:
                digits.append(mapped_teen)
                idx += 1
                continue
            mapped_tens = tens_words.get(token)
            if mapped_tens:
                next_token = tokens[idx + 1] if idx + 1 < len(tokens) else ""
                next_digit = digit_words.get(next_token)
                if next_digit is not None:
                    digits.append(f"{mapped_tens}{next_digit}")
                    idx += 2
                    continue
                digits.append(f"{mapped_tens}0")
                idx += 1
                continue
            idx += 1
        return "".join(digits)

    def _strip_georgia_country_prefixes(self, digits: str) -> str:
        stripped = digits
        while stripped.startswith(self._GEORGIA_COUNTRY_CODE):
            stripped = stripped[len(self._GEORGIA_COUNTRY_CODE) :]
        return stripped

    def _build_callback_phone_candidates(
        self,
        *,
        digits_chunk: str,
        current_buffer: str,
        source: str,
    ) -> list[dict[str, str]]:
        if not digits_chunk:
            return []

        stripped = self._strip_georgia_country_prefixes(digits_chunk)
        if digits_chunk.startswith(self._GEORGIA_COUNTRY_CODE):
            local_variants = [stripped] if stripped else []
        else:
            local_variants = [digits_chunk]

        candidates: list[dict[str, str]] = []
        candidate_counter = 0
        seen: set[tuple[str, str]] = set()

        for variant in local_variants:
            if not variant:
                continue
            candidate_counter += 1
            append_buffer = f"{current_buffer}{variant}"[-self._GEORGIA_LOCAL_DIGITS :]
            key = (append_buffer, "append")
            if key not in seen:
                candidates.append(
                    {
                        "id": f"{source}_append_{candidate_counter}",
                        "source": source,
                        "chunk": variant,
                        "buffer": append_buffer,
                        "normalized": self._normalize_georgian_mobile_digits(append_buffer) or "",
                    }
                )
                seen.add(key)

            if len(variant) >= self._GEORGIA_LOCAL_DIGITS:
                replace_counter = candidate_counter + 100
                replace_buffer = variant[-self._GEORGIA_LOCAL_DIGITS :]
                key = (replace_buffer, "replace_last")
                if key not in seen:
                    candidates.append(
                        {
                            "id": f"{source}_replace_last_{replace_counter}",
                            "source": source,
                            "chunk": variant,
                            "buffer": replace_buffer,
                            "normalized": self._normalize_georgian_mobile_digits(replace_buffer) or "",
                        }
                    )
                    seen.add(key)

                replace_first = variant[: self._GEORGIA_LOCAL_DIGITS]
                key = (replace_first, "replace_first")
                if key not in seen:
                    candidates.append(
                        {
                            "id": f"{source}_replace_first_{replace_counter}",
                            "source": source,
                            "chunk": variant,
                            "buffer": replace_first,
                            "normalized": self._normalize_georgian_mobile_digits(replace_first) or "",
                        }
                    )
                    seen.add(key)
        return candidates

    def _callback_candidates_context(self, candidates: list[dict[str, str]]) -> list[dict[str, str]]:
        return [
            {
                "id": candidate["id"],
                "source": candidate["source"],
                "chunk": candidate["chunk"],
                "buffer": candidate["buffer"],
                "normalized": candidate["normalized"] or "invalid",
            }
            for candidate in candidates
        ]

    def _find_callback_candidate(
        self,
        candidates: list[dict[str, str]],
        candidate_id: str,
    ) -> dict[str, str] | None:
        for candidate in candidates:
            if candidate["id"] == candidate_id:
                return candidate
        return None

    def _pick_best_callback_candidate(self, candidates: list[dict[str, str]]) -> dict[str, str] | None:
        if not candidates:
            return None
        return max(candidates, key=self._callback_candidate_score)

    def _callback_candidate_score(self, candidate: dict[str, str]) -> tuple[int, int, int, int, int]:
        normalized_score = 1 if candidate.get("normalized") else 0
        source_score = 1 if candidate.get("source") == "raw" else 0
        lead_score = 1 if candidate.get("buffer", "").startswith(self._GEORGIA_MOBILE_LEAD) else 0
        buffer_len = len(candidate.get("buffer", ""))
        chunk_len = len(candidate.get("chunk", ""))
        return (normalized_score, source_score, lead_score, buffer_len, chunk_len)

    def _wants_callback(self, text: str) -> bool:
        return bool(self._CALLBACK_RE.search(text))

    def _is_noise_or_frustration(self, text: str) -> bool:
        lowered = text.strip().lower()
        if lowered in {"jesus", "awful", "ugh", "wow"}:
            return True
        return bool(self._LIGHT_NOISE_RE.fullmatch(lowered))

    def _maybe_prepend_expiry_notice(self, text: str, ctx: SessionContext) -> str:
        policy_id = ctx.pending_entities.get("policy_id", "")
        normalized = self._policy_store.normalize_policy_id(policy_id)
        if normalized is None:
            return text
        holder = self._policy_store.find_policyholder(normalized)
        if holder is None:
            return text
        info = self._policy_store.renewal_expiry_info(holder, today=date.today())
        if info is None or not info.expires_soon:
            return text
        if ctx.expiry_notice_policy_id == normalized:
            return text
        ctx.expiry_notice_policy_id = normalized
        return f"Your policy {normalized} is due for renewal on {info.due_date.isoformat()}. {text}"

    def _humanize_dates(self, text: str) -> str:
        def _replace(match: re.Match[str]) -> str:
            try:
                parsed = date.fromisoformat(match.group(0))
            except ValueError:
                return match.group(0)
            return f"{parsed:%B} {parsed.day}, {parsed:%Y}"

        return self._ISO_DATE.sub(_replace, text)

    def _append_history(self, ctx: SessionContext, role: str, content: str) -> None:
        ctx.conversation_history.append({"role": role, "content": content})
        if len(ctx.conversation_history) > 10:
            ctx.conversation_history = ctx.conversation_history[-10:]
