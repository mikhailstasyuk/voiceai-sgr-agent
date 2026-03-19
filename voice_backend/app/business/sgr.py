from __future__ import annotations

import json
from typing import Any

HANDOFF_FLOW_MAP: dict[str, str] = {
    "APPOINTMENT": "appointment",
    "POLICY_RENEWAL": "policy_renewal",
    "PLAN_INQUIRY": "plan_inquiry",
    "CALLBACK_SUPPORT": "callback_support",
}

SUPPORTED_FLOWS = frozenset(HANDOFF_FLOW_MAP.values())
INTENT_REROUTE_MARKER = "__intent_reroute__"


def resolve_handoff_flow(handoff_intent: str | None, *, current_flow: str) -> str | None:
    mapped = HANDOFF_FLOW_MAP.get(str(handoff_intent or "").strip().upper())
    if mapped is None:
        return INTENT_REROUTE_MARKER
    if mapped == current_flow:
        return None
    return mapped


def normalize_requested_flow(requested_flow: str | None, *, current_flow: str | None) -> str | None:
    value = str(requested_flow or "").strip()
    if not value:
        return None
    if value == INTENT_REROUTE_MARKER:
        return INTENT_REROUTE_MARKER
    if value not in SUPPORTED_FLOWS:
        return None
    if current_flow and value == current_flow:
        return None
    return value


async def call_structured_json(
    *,
    client: Any,
    model_name: str,
    schema_name: str,
    schema: dict[str, Any],
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    completion = await client.chat.completions.create(
        model=model_name,
        messages=messages,
        stream=False,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": schema,
            },
        },
        temperature=0,
        top_p=1,
    )
    if not completion.choices:
        raise ValueError("No completion choices returned")
    raw_json = _flatten_content(completion.choices[0].message.content)
    parsed = json.loads(raw_json)
    if not isinstance(parsed, dict):
        raise TypeError("Structured response must be JSON object")
    return parsed


def _flatten_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
            elif hasattr(part, "type") and getattr(part, "type") == "text":
                parts.append(str(getattr(part, "text", "")))
        joined = "".join(parts).strip()
        if joined:
            return joined
    if isinstance(content, dict):
        return json.dumps(content)
    raise ValueError("Unexpected content format")
