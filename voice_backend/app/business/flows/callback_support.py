from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import CallbackSupportReasoning, FlowResult, SessionContext
from ..schema_utils import to_groq_strict_schema
from ..sgr import call_structured_json, resolve_handoff_flow


class CallbackSupportFlow:
    def __init__(self, callback_requests_path: Path) -> None:
        self._callback_requests_path = callback_requests_path

    async def execute(
        self,
        text: str,
        session_ctx: SessionContext,
        groq_client: Any,
        model_name: str,
    ) -> FlowResult:
        requests = self._read_callback_requests()
        latest = self._latest_for_session(requests, session_ctx.session_id)
        messages = [
            {
                "role": "system",
                "content": (
                    "You handle callback support requests. "
                    "Use confirm_status for callback status checks. "
                    "Use explain_last_transition for 'why canceled/closed' style questions. "
                    "Use offer_reschedule when user wants callback again. "
                    "Use collect_phone_if_needed when user is ready to reschedule but phone is missing. "
                    "If user requests another service, use handoff_intent. "
                    "Return strict JSON only."
                ),
            },
            {
                "role": "system",
                "content": f"Latest callback record for this session: {json.dumps(latest)}",
            },
            {"role": "user", "content": text},
        ]
        schema = to_groq_strict_schema(CallbackSupportReasoning.model_json_schema())
        reasoning = await self._reason(messages, schema, groq_client, model_name)

        if reasoning.action == "confirm_status":
            if latest is None:
                return FlowResult(
                    response_text="I do not see a queued callback for this session right now.",
                    completed=False,
                    updated_entities={},
                )
            phone = str(latest.get("phone", "")).strip()
            callback_date = str(latest.get("callback_date", "")).strip()
            status = str(latest.get("status", "queued")).strip().lower() or "queued"
            date_suffix = f" Callback date is {callback_date}." if callback_date else ""
            return FlowResult(
                response_text=f"Your latest callback status is {status}. Phone on file is {phone}.{date_suffix}",
                completed=False,
                updated_entities={},
            )

        if reasoning.action in {"offer_reschedule", "collect_phone_if_needed"}:
            return FlowResult(
                response_text=(
                    "Sure, I can schedule a callback now. "
                    "What Georgian mobile number should we call?"
                ),
                completed=False,
                schedule_callback=True,
                updated_entities={},
            )

        if reasoning.action == "explain_last_transition":
            if latest is None:
                return FlowResult(
                    response_text=(
                        "There is no active callback request on record right now. "
                        "If you want, I can schedule one now."
                    ),
                    completed=False,
                    updated_entities={},
                )
            status = str(latest.get("status", "queued")).strip().lower() or "queued"
            if status == "queued":
                return FlowResult(
                    response_text="Your callback request is still queued. If needed, I can reschedule it.",
                    completed=False,
                    updated_entities={},
                )
            return FlowResult(
                response_text=(
                    "The previous callback request is no longer active. "
                    "If you want, I can schedule a new callback now."
                ),
                completed=False,
                updated_entities={},
            )

        if reasoning.action == "handoff_intent":
            requested_flow = self._requested_flow_from_handoff(reasoning.handoff_intent, current_flow="callback_support")
            updated_entities: dict[str, str] = {}
            if requested_flow:
                updated_entities["requested_flow"] = requested_flow
            return FlowResult(
                response_text=(
                    reasoning.message_to_user
                    or "Sure. Tell me what you need next: appointment, renewal, plan, or callback."
                ),
                completed=False,
                updated_entities=updated_entities,
            )

        return FlowResult(
            response_text=reasoning.message_to_user or "I can check callback status or schedule a callback now.",
            completed=False,
            updated_entities={},
        )

    async def _reason(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        groq_client: Any,
        model_name: str,
    ) -> CallbackSupportReasoning:
        try:
            parsed = await self._call_structured(
                "callback_support_reasoning",
                schema,
                messages,
                groq_client,
                model_name,
            )
            return CallbackSupportReasoning.model_validate(parsed)
        except Exception:
            return CallbackSupportReasoning(
                action="clarify",
                message_to_user="I can check callback status or schedule a callback. Which one do you need?",
                handoff_intent=None,
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

    def _read_callback_requests(self) -> list[dict[str, str]]:
        if not self._callback_requests_path.exists():
            return []
        raw = json.loads(self._callback_requests_path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return raw
        return []

    def _latest_for_session(self, requests: list[dict[str, str]], session_id: str) -> dict[str, str] | None:
        for request in reversed(requests):
            if str(request.get("session_id", "")) == session_id:
                return request
        return None

    def _requested_flow_from_handoff(self, handoff_intent: str | None, *, current_flow: str) -> str | None:
        return resolve_handoff_flow(handoff_intent, current_flow=current_flow)
