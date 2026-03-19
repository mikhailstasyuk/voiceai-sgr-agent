from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..models import FlowResult, PlanInquiryReasoning, SessionContext
from ..policy_id_capture import PolicyIdCapture, set_policy_candidate_schema_field
from ..policy_store import PolicyStore
from ..schema_utils import to_groq_strict_schema
from ..slot_policy import reconcile_policy_id
from ..sgr import call_structured_json, resolve_handoff_flow

log = logging.getLogger("hypercheap.business.flow.plan_inquiry")


class PlanInquiryFlow:
    _AFFIRMATIVE_RE = re.compile(r"\b(yes|yeah|yep|correct|right|switch|use that)\b", re.IGNORECASE)
    _NEGATIVE_RE = re.compile(r"\b(no|nope|keep|stay|current|don't switch|do not switch)\b", re.IGNORECASE)

    def __init__(self, policy_store: PolicyStore) -> None:
        self._policy_store = policy_store
        self._policy_capture = PolicyIdCapture(recent_user_turn_window=3)

    async def execute(
        self,
        text: str,
        session_ctx: SessionContext,
        groq_client: Any,
        model_name: str,
    ) -> FlowResult:
        entities = session_ctx.pending_entities
        pending_conflict = entities.get("policy_id_conflict_candidate", "")
        if pending_conflict:
            if self._AFFIRMATIVE_RE.search(text):
                entities["policy_id"] = pending_conflict
                entities.pop("policy_id_conflict_candidate", None)
            elif self._NEGATIVE_RE.search(text):
                entities.pop("policy_id_conflict_candidate", None)
            else:
                current_policy = entities.get("policy_id", "")
                return FlowResult(
                    response_text=(
                        f"I heard policy {pending_conflict} instead of {current_policy}. "
                        "Say yes to switch policy id, or no to keep the current one."
                    ),
                    completed=False,
                    updated_entities=dict(entities),
                    progress_key="confirm_policy_conflict",
                )

        plans = self._policy_store.list_plans()
        history = list(session_ctx.conversation_history[-4:])
        raw_policy_candidates = []
        if not self._has_verified_policy(entities) or self._turn_mentions_policy_signal(text):
            raw_policy_candidates = self._policy_capture.build_candidates(text=text, history=history, source="raw")
        schema = self._reasoning_schema(plans, [candidate.id for candidate in raw_policy_candidates])
        messages = [
            {
                "role": "system",
                "content": (
                    "You handle plan inquiries. "
                    "Use list_plans for general plan listings. "
                    "Use ask_policy_id when policy id is needed to answer current-plan questions or proceed with renewal. "
                    "Use compare_with_current_plan when caller asks what plan they currently have and policy id is available. "
                    "Use offer_renewal when caller wants to choose/switch/change to a specific plan. "
                    "If user asks to switch to a different service, use handoff_intent. "
                    "Do not use handoff_intent to stay in plan inquiry. "
                    "When policy_candidates are provided, choose selected_policy_candidate_id from them when possible. "
                    "When no policy candidate applies, set selected_policy_candidate_id to __NONE__. "
                    "Use selected_plan_id only from provided plan ids when caller names a target plan. "
                    "Return strict JSON only."
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
                "selected_policy_candidate_id=%r selected_plan_id=%r"
            ),
            session_ctx.session_id,
            reasoning.action,
            reasoning.handoff_intent,
            reasoning.selected_policy_candidate_id,
            reasoning.selected_plan_id,
        )

        lines = self._plan_lines(plans)
        policy_id = entities.get("policy_id")
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
        resolution = reconcile_policy_id(
            current_policy_id=policy_id or "",
            selected_policy_id=selected_policy.normalized if selected_policy else None,
            policy_store=self._policy_store,
        )
        if resolution.changed and resolution.value:
            entities["policy_id"] = resolution.value
            policy_id = resolution.value
            entities.pop("policy_id_conflict_candidate", None)
        elif resolution.conflict and selected_policy is not None:
            entities["policy_id_conflict_candidate"] = selected_policy.normalized
            return FlowResult(
                response_text=(
                    f"I heard policy {selected_policy.normalized} instead of {policy_id}. "
                    "Say yes to switch policy id, or no to keep the current one."
                ),
                completed=False,
                updated_entities=dict(entities),
                progress_key="confirm_policy_conflict",
            )

        if reasoning.action == "offer_renewal" and reasoning.selected_plan_id:
            entities["selected_plan_id"] = reasoning.selected_plan_id

        if reasoning.action == "cancel":
            return FlowResult(
                response_text=reasoning.message_to_user,
                completed=True,
                updated_entities=dict(entities),
                progress_key="done",
            )

        if reasoning.action == "handoff_intent":
            requested_flow = self._requested_flow_from_handoff(reasoning.handoff_intent, current_flow="plan_inquiry")
            if requested_flow:
                entities["requested_flow"] = requested_flow
            return FlowResult(
                response_text=(
                    reasoning.message_to_user
                    or "Sure. Tell me what you need help with: appointment, renewal, plan, or callback."
                ),
                completed=False,
                updated_entities=dict(entities),
                progress_key="handoff",
            )

        if reasoning.action == "clarify":
            return FlowResult(
                response_text=reasoning.message_to_user or self._list_plans_message(lines),
                completed=False,
                updated_entities=dict(entities),
                progress_key="clarify_plan_inquiry",
            )

        if reasoning.action == "ask_policy_id":
            return FlowResult(
                response_text="Please share your policy id in the format POL-1234 so I can check your current plan.",
                completed=False,
                updated_entities=dict(entities),
                progress_key="need_policy_id",
            )

        holder = self._policy_store.find_policyholder(policy_id) if policy_id else None
        if reasoning.action == "compare_with_current_plan":
            if holder is None:
                return FlowResult(
                    response_text="Please share your policy id in the format POL-1234 so I can check your current plan.",
                    completed=False,
                    updated_entities=dict(entities),
                    progress_key="need_policy_id",
                )
            current = self._policy_store.get_plan(str(holder.get("current_plan_id", "")))
            if current:
                return FlowResult(
                    response_text=(
                        f"Your current plan is {current['name']} at ${current['monthly_price_usd']} per month. "
                        f"All plans: {', '.join(lines)}."
                    ),
                    completed=False,
                    updated_entities=dict(entities),
                    progress_key="compare_current_plan",
                )
            return FlowResult(
                response_text="I could not find your current plan details. " + self._list_plans_message(lines),
                completed=False,
                updated_entities=dict(entities),
                progress_key="compare_current_plan",
            )

        if reasoning.action == "offer_renewal":
            entities["requested_flow"] = "policy_renewal"
            return FlowResult(
                response_text=(
                    "I can renew your policy and switch plans if needed. "
                    "Please confirm your policy id in POL-1234 format."
                ),
                completed=False,
                updated_entities=dict(entities),
                progress_key="handoff_to_renewal",
            )

        return FlowResult(
            response_text=self._list_plans_message(lines),
            completed=False,
            updated_entities=dict(entities),
            progress_key="list_plans",
        )

    async def _reason(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        groq_client: Any,
        model_name: str,
    ) -> PlanInquiryReasoning:
        try:
            parsed = await self._call_structured("plan_inquiry_reasoning", schema, messages, groq_client, model_name)
            return PlanInquiryReasoning.model_validate(parsed)
        except Exception:
            return PlanInquiryReasoning(
                action="list_plans",
                message_to_user="I can list available plans.",
                extracted_policy_id=None,
                selected_policy_candidate_id=None,
                selected_plan_id=None,
                handoff_intent=None,
            )

    def _reasoning_schema(self, plans: list[dict[str, Any]], policy_candidate_ids: list[str]) -> dict[str, Any]:
        schema = to_groq_strict_schema(PlanInquiryReasoning.model_json_schema())
        set_policy_candidate_schema_field(schema, policy_candidate_ids)
        selected_plan = schema.get("properties", {}).get("selected_plan_id")
        if isinstance(selected_plan, dict):
            selected_plan.clear()
            plan_ids = [str(plan.get("id", "")).strip() for plan in plans if str(plan.get("id", "")).strip()]
            if plan_ids:
                selected_plan.update({"anyOf": [{"type": "string", "enum": plan_ids}, {"type": "null"}]})
            else:
                selected_plan.update({"anyOf": [{"type": "null"}]})
        return schema

    def _plan_lines(self, plans: list[dict[str, Any]]) -> list[str]:
        return [f"{p['name']} (${p['monthly_price_usd']}/month)" for p in plans]

    def _list_plans_message(self, lines: list[str]) -> str:
        return "Available plans are: " + ", ".join(lines) + "."

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

    def _requested_flow_from_handoff(self, handoff_intent: str | None, *, current_flow: str) -> str | None:
        return resolve_handoff_flow(handoff_intent, current_flow=current_flow)
