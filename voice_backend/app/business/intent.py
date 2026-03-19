from __future__ import annotations

import logging
import time
from typing import Any

from .models import IntentResult, IntentType, SessionContext
from .schema_utils import to_groq_strict_schema
from .sgr import call_structured_json

log = logging.getLogger("hypercheap.business.intent")


class IntentDetector:
    _MAX_HISTORY_MESSAGES = 2

    def __init__(self, groq_client: Any, model_name: str) -> None:
        self._client = groq_client
        self._model_name = model_name

    async def detect(self, text: str, session_ctx: SessionContext) -> IntentResult:
        detect_t0 = time.perf_counter()
        schema = self._intent_result_schema()
        history = list(session_ctx.conversation_history[-self._MAX_HISTORY_MESSAGES :])
        if not history or history[-1].get("role") != "user" or history[-1].get("content") != text:
            history.append({"role": "user", "content": text})

        messages = [
            {
                "role": "system",
                "content": (
                    "Classify user intent into exactly one of: APPOINTMENT, POLICY_RENEWAL, "
                    "PLAN_INQUIRY, CALLBACK_SUPPORT, UNCLEAR. "
                    "Requests to change/switch/select plans map to PLAN_INQUIRY. "
                    "Use CALLBACK_SUPPORT for callback status questions, callback cancellation/explanation requests, "
                    "or callback rescheduling requests. "
                    "Return JSON strictly matching the schema."
                ),
            },
            *history,
        ]

        try:
            log.info(
                "[intent:request] session_id=%s model=%s attempt=1 messages=%s",
                session_ctx.session_id,
                self._model_name,
                messages,
            )
            payload = await self._call_structured("intent_result", schema, messages)
            log.info("[intent:response] session_id=%s attempt=1 payload=%s", session_ctx.session_id, payload)
            result = self._normalize_entities(IntentResult.model_validate(payload))
            log.info(
                "[intent:done] session_id=%s attempts=1 latency_ms=%.2f",
                session_ctx.session_id,
                (time.perf_counter() - detect_t0) * 1000.0,
            )
            return result
        except Exception:
            log.exception("[intent:error] session_id=%s attempt=1 validation_failed", session_ctx.session_id)
            correction_messages = messages + [
                {
                    "role": "system",
                    "content": (
                        "Your previous response failed validation. Return ONLY valid JSON that matches "
                        "the required schema exactly."
                    ),
                }
            ]
            try:
                log.info(
                    "[intent:request] session_id=%s model=%s attempt=2 messages=%s",
                    session_ctx.session_id,
                    self._model_name,
                    correction_messages,
                )
                payload = await self._call_structured("intent_result", schema, correction_messages)
                log.info("[intent:response] session_id=%s attempt=2 payload=%s", session_ctx.session_id, payload)
                result = self._normalize_entities(IntentResult.model_validate(payload))
                log.info(
                    "[intent:done] session_id=%s attempts=2 latency_ms=%.2f",
                    session_ctx.session_id,
                    (time.perf_counter() - detect_t0) * 1000.0,
                )
                return result
            except Exception:
                log.exception("[intent:error] session_id=%s attempt=2 validation_failed", session_ctx.session_id)
                log.info(
                    "[intent:done] session_id=%s attempts=2 latency_ms=%.2f fallback=parse_failure",
                    session_ctx.session_id,
                    (time.perf_counter() - detect_t0) * 1000.0,
                )
                return IntentResult(
                    intent=IntentType.UNCLEAR,
                    confidence=0.0,
                    extracted_entities={},
                    reasoning="parse failure",
                )

    def _intent_result_schema(self) -> dict[str, Any]:
        schema = to_groq_strict_schema(IntentResult.model_json_schema())
        extracted = schema.get("properties", {}).get("extracted_entities")
        if isinstance(extracted, dict):
            # Force a finite object shape because Groq strict mode rejects
            # free-form map schemas for response_format.
            extracted.clear()
            extracted.update(
                {
                    "type": "object",
                    "properties": {
                        "date": {
                            "type": "string",
                            "description": "ISO date when available, otherwise empty string.",
                        },
                        "clinic": {
                            "type": "string",
                            "description": "Clinic name or id when available, otherwise empty string.",
                        },
                        "policy_id": {
                            "type": "string",
                            "description": "Policy id when available, otherwise empty string.",
                        },
                        "doctor_name": {
                            "type": "string",
                            "description": "Doctor name when available, otherwise empty string.",
                        },
                    },
                    "required": ["date", "clinic", "policy_id", "doctor_name"],
                    "additionalProperties": False,
                }
            )
        return schema

    def _normalize_entities(self, result: IntentResult) -> IntentResult:
        result.extracted_entities = {
            key: value
            for key, value in result.extracted_entities.items()
            if isinstance(value, str) and value.strip()
        }
        return result

    async def _call_structured(
        self,
        schema_name: str,
        schema: dict[str, Any],
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        parsed = await call_structured_json(
            client=self._client,
            model_name=self._model_name,
            schema_name=schema_name,
            schema=schema,
            messages=messages,
        )
        log.info(
            "[intent:api] schema=%s latency_ms=%.2f response_keys=%s",
            schema_name,
            (time.perf_counter() - t0) * 1000.0,
            list(parsed.keys()),
        )
        return parsed
