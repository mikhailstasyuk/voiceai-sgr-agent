from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

from ..models import ClinicChangeReasoning, FlowResult, SessionContext
from ..policy_id_capture import PolicyIdCapture, set_policy_candidate_schema_field
from ..policy_store import PolicyStore
from ..schema_utils import to_groq_strict_schema
from ..sgr import call_structured_json, resolve_handoff_flow

log = logging.getLogger("hypercheap.business.flow.clinic_change")


class ClinicChangeFlow:
    def __init__(self, policy_store: PolicyStore, data_dir: Path | None = None) -> None:
        self._policy_store = policy_store
        self._policy_capture = PolicyIdCapture(recent_user_turn_window=3)
        base_dir = data_dir or (Path(__file__).resolve().parent.parent / "data")
        self._clinics = json.loads((base_dir / "clinics.json").read_text(encoding="utf-8"))

    async def execute(
        self,
        text: str,
        session_ctx: SessionContext,
        groq_client: Any,
        model_name: str,
    ) -> FlowResult:
        entities = session_ctx.pending_entities
        clinic_ids = [str(item.get("id", "")).strip() for item in self._clinics if str(item.get("id", "")).strip()]
        history = list(session_ctx.conversation_history[-4:])
        raw_policy_candidates = []
        if not self._has_verified_policy(entities) or self._turn_mentions_policy_signal(text):
            raw_policy_candidates = self._policy_capture.build_candidates(text=text, history=history, source="raw")
        schema = self._reasoning_schema(clinic_ids, [candidate.id for candidate in raw_policy_candidates])
        messages = [
            {
                "role": "system",
                "content": (
                    "You handle clinic changes. Ask for policy id first. "
                    "Use selected_clinic_id from allowed options. "
                    "If user asks to switch service or asks for other help, use handoff_intent. "
                    "When policy_candidates are provided, choose selected_policy_candidate_id from them when possible. "
                    "When no policy candidate applies, set selected_policy_candidate_id to __NONE__. "
                    "Use confirm_clinic_change only when policy and clinic selection are present."
                ),
            },
            {
                "role": "system",
                "content": (
                    "Policy candidates: "
                    f"{json.dumps(self._policy_capture.context_payload(raw_policy_candidates))}"
                ),
            },
            {"role": "system", "content": f"Known entities: {json.dumps(entities)}"},
            {"role": "user", "content": text},
        ]
        reasoning = await self._reason(messages, schema, groq_client, model_name)
        log.info(
            (
                "[flow:reasoning] session_id=%s action=%s handoff_intent=%r "
                "selected_clinic_id=%r selected_policy_candidate_id=%r"
            ),
            session_ctx.session_id,
            reasoning.action,
            reasoning.handoff_intent,
            reasoning.selected_clinic_id,
            reasoning.selected_policy_candidate_id,
        )

        policy_id = entities.get("policy_id", "")
        policy_candidates = list(raw_policy_candidates)
        if reasoning.extracted_policy_id:
            extracted_candidates = self._policy_capture.build_candidates(
                text=reasoning.extracted_policy_id,
                history=history,
                source="extracted",
            )
            policy_candidates = self._policy_capture.merge_candidates(policy_candidates + extracted_candidates)
        selected_policy, _ = self._policy_capture.resolve_candidate(
            candidates=policy_candidates,
            selected_candidate_id=reasoning.selected_policy_candidate_id,
        )
        if selected_policy is not None and not policy_id:
            entities["policy_id"] = selected_policy.normalized
            policy_id = selected_policy.normalized

        if reasoning.selected_clinic_id:
            entities["selected_clinic_id"] = reasoning.selected_clinic_id

        if reasoning.action == "handoff_intent":
            requested_flow = self._requested_flow_from_handoff(reasoning.handoff_intent, current_flow="clinic_change")
            if requested_flow:
                entities["requested_flow"] = requested_flow
            return FlowResult(
                response_text=(
                    reasoning.message_to_user
                    or "Sure. Tell me which service you need: appointment, renewal, plan, clinic change, or callback."
                ),
                completed=False,
                updated_entities=dict(entities),
            )

        if reasoning.action == "cancel":
            return FlowResult(response_text=reasoning.message_to_user, completed=True, updated_entities=dict(entities))

        if reasoning.action == "clarify":
            return FlowResult(response_text=reasoning.message_to_user, completed=False, updated_entities=dict(entities))

        if not policy_id:
            return FlowResult(
                response_text="Please share your policy id in POL-1234 format before changing clinics.",
                completed=False,
                updated_entities=dict(entities),
            )

        holder = self._policy_store.find_policyholder(policy_id)
        if holder is None:
            entities.pop("policy_id", None)
            return FlowResult(
                response_text="I could not find that policy id. Please provide a valid one.",
                completed=False,
                updated_entities=dict(entities),
            )

        can_change, eligible_on = self._policy_store.can_change_clinic(holder, today=date.today())
        if not can_change:
            if eligible_on is None:
                return FlowResult(
                    response_text="I cannot verify clinic-change eligibility right now.",
                    completed=False,
                    updated_entities=dict(entities),
                )
            return FlowResult(
                response_text=(
                    "Clinic can be changed once per year. "
                    f"Your next eligible date is {eligible_on.isoformat()}."
                ),
                completed=False,
                updated_entities=dict(entities),
            )

        if reasoning.action == "ask_policy_id":
            return FlowResult(
                response_text="Please share your policy id in POL-1234 format before changing clinics.",
                completed=False,
                updated_entities=dict(entities),
            )

        selected_clinic_id = entities.get("selected_clinic_id")
        if reasoning.action == "list_clinics":
            clinic_names = [
                str(item.get("name", "")).strip()
                for item in self._clinics
                if str(item.get("name", "")).strip()
            ]
            return FlowResult(
                response_text="Available clinics are: " + ", ".join(clinic_names) + ". Which clinic do you want?",
                completed=False,
                updated_entities=dict(entities),
            )

        if reasoning.action == "confirm_clinic_change":
            if not selected_clinic_id:
                clinic_names = [
                    str(item.get("name", "")).strip()
                    for item in self._clinics
                    if str(item.get("name", "")).strip()
                ]
                return FlowResult(
                    response_text="Available clinics are: " + ", ".join(clinic_names) + ". Which clinic do you want?",
                    completed=False,
                    updated_entities=dict(entities),
                )
            updated = self._policy_store.update_clinic(
                policy_id=policy_id,
                clinic_id=selected_clinic_id,
                today=date.today(),
            )
            if updated is None:
                return FlowResult(
                    response_text="I could not apply that clinic change. Please pick an available clinic.",
                    completed=False,
                    updated_entities=dict(entities),
                )
            entities.pop("selected_clinic_id", None)
            return FlowResult(
                response_text="Your clinic assignment has been updated.",
                completed=True,
                updated_entities=dict(entities),
            )

        if not selected_clinic_id:
            clinic_names = [
                str(item.get("name", "")).strip()
                for item in self._clinics
                if str(item.get("name", "")).strip()
            ]
            return FlowResult(
                response_text="Available clinics are: " + ", ".join(clinic_names) + ". Which clinic do you want?",
                completed=False,
                updated_entities=dict(entities),
            )

        return FlowResult(response_text=reasoning.message_to_user, completed=False, updated_entities=dict(entities))

    async def _reason(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        groq_client: Any,
        model_name: str,
    ) -> ClinicChangeReasoning:
        try:
            parsed = await self._call_structured("clinic_change_reasoning", schema, messages, groq_client, model_name)
            return ClinicChangeReasoning.model_validate(parsed)
        except Exception:
            return ClinicChangeReasoning(
                action="ask_policy_id",
                message_to_user="Please share your policy id in POL-1234 format.",
                extracted_policy_id=None,
                selected_policy_candidate_id=None,
                selected_clinic_id=None,
                extracted_clinic=None,
                handoff_intent=None,
            )

    def _requested_flow_from_handoff(self, handoff_intent: str | None, *, current_flow: str) -> str | None:
        return resolve_handoff_flow(handoff_intent, current_flow=current_flow)

    def _reasoning_schema(self, clinic_ids: list[str], policy_candidate_ids: list[str]) -> dict[str, Any]:
        schema = to_groq_strict_schema(ClinicChangeReasoning.model_json_schema())
        selected = schema.get("properties", {}).get("selected_clinic_id")
        if isinstance(selected, dict):
            selected.clear()
            if clinic_ids:
                selected.update({"anyOf": [{"type": "string", "enum": clinic_ids}, {"type": "null"}]})
            else:
                selected.update({"anyOf": [{"type": "null"}]})
        set_policy_candidate_schema_field(schema, policy_candidate_ids)
        return schema

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

    def _has_verified_policy(self, entities: dict[str, str]) -> bool:
        policy_id = entities.get("policy_id", "")
        normalized = self._policy_store.normalize_policy_id(policy_id)
        if normalized is None:
            return False
        return self._policy_store.find_policyholder(normalized) is not None

    def _turn_mentions_policy_signal(self, text: str) -> bool:
        if re.search(r"\bpol(?:icy)?\b", text, flags=re.IGNORECASE):
            return True
        candidates = self._policy_capture.build_candidates(text=text, history=[], source="raw")
        return bool(candidates)
