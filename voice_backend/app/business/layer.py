from __future__ import annotations

import logging
import re
import time
from datetime import date

from .flows.appointment import AppointmentFlow
from .intent import IntentDetector
from .models import AgentResponse, IntentType, SessionContext, SessionState
from .session import SessionStore

log = logging.getLogger("hypercheap.business.layer")


class BusinessLayer:
    _ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

    def __init__(self, groq_client, model_name: str, session_store: SessionStore) -> None:
        self._groq_client = groq_client
        self._model_name = model_name
        self._session_store = session_store
        self._intent_detector = IntentDetector(groq_client=groq_client, model_name=model_name)
        self._appointment_flow = AppointmentFlow()

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
        if ctx.state in (SessionState.IDLE, SessionState.COMPLETED):
            return await self._handle_intent_entry(text, ctx)

        if ctx.state == SessionState.AWAITING_CLARIFICATION:
            return await self._handle_intent_entry(text, ctx)

        if ctx.state == SessionState.IN_FLOW:
            return await self._execute_flow(text, ctx)

        if ctx.state == SessionState.SCHEDULING_CALLBACK:
            return AgentResponse(text="I will arrange a callback from our team shortly.")

        return AgentResponse(text="Could you please repeat that?")

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
            ctx.intent_attempts += 1
            if ctx.intent_attempts >= 3:
                ctx.state = SessionState.SCHEDULING_CALLBACK
                log.info(
                    "[decision] session_id=%s action=escalate_callback reason=unclear_intent intent_attempts=%d",
                    ctx.session_id,
                    ctx.intent_attempts,
                )
                return AgentResponse(text="I'm having trouble understanding. Let me arrange a callback for you.")
            ctx.state = SessionState.AWAITING_CLARIFICATION
            log.info(
                "[decision] session_id=%s action=request_clarification intent_attempts=%d",
                ctx.session_id,
                ctx.intent_attempts,
            )
            return AgentResponse(
                text=(
                    "I can help book an appointment. Please share your preferred date, clinic, policy id, "
                    "and doctor's name."
                )
            )

        ctx.intent_attempts = 0
        ctx.pending_entities.update(intent_result.extracted_entities)
        ctx.active_flow = "appointment"
        ctx.state = SessionState.IN_FLOW
        log.info(
            "[decision] session_id=%s action=enter_appointment_flow pending_entities=%s",
            ctx.session_id,
            ctx.pending_entities,
        )
        return await self._execute_flow(text, ctx)

    async def _execute_flow(self, text: str, ctx: SessionContext) -> AgentResponse:
        t0 = time.perf_counter()
        result = await self._appointment_flow.execute(
            text=text,
            session_ctx=ctx,
            groq_client=self._groq_client,
            model_name=self._model_name,
        )
        log.info(
            (
                "[flow] session_id=%s flow=appointment completed=%s "
                "schedule_callback=%s updated_entities=%s response_text=%r latency_ms=%.2f"
            ),
            ctx.session_id,
            result.completed,
            result.schedule_callback,
            result.updated_entities,
            result.response_text,
            (time.perf_counter() - t0) * 1000.0,
        )
        if result.schedule_callback:
            ctx.state = SessionState.SCHEDULING_CALLBACK
            log.info("[decision] session_id=%s action=flow_schedule_callback", ctx.session_id)
            return AgentResponse(text=result.response_text)
        if result.completed:
            ctx.state = SessionState.COMPLETED
            ctx.active_flow = None
            ctx.pending_entities = {}
            log.info("[decision] session_id=%s action=flow_completed", ctx.session_id)
        if result.updated_entities:
            ctx.pending_entities.update(result.updated_entities)
        return AgentResponse(text=result.response_text)

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
