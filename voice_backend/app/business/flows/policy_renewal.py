from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any

from ..models import FlowResult, PolicyRenewalReasoning, SessionContext
from ..policy_id_capture import PolicyIdCapture, set_policy_candidate_schema_field
from ..policy_store import PolicyStore
from ..schema_utils import to_groq_strict_schema
from ..slot_policy import reconcile_policy_id
from ..sgr import call_structured_json, resolve_handoff_flow

log = logging.getLogger("hypercheap.business.flow.policy_renewal")


class PolicyRenewalFlow:
    _AFFIRMATIVE_RE = re.compile(r"\b(yes|yeah|yep|confirm|correct)\b", re.IGNORECASE)
    _NEGATIVE_RE = re.compile(r"\b(no|nope|cancel|don't|do not|change)\b", re.IGNORECASE)

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

        policy_id = entities.get("policy_id", "")
        plan_ids = self._policy_store.list_plan_ids()
        history = list(session_ctx.conversation_history[-4:])
        raw_policy_candidates = []
        if not self._has_verified_policy(entities) or self._turn_mentions_policy_signal(text):
            raw_policy_candidates = self._policy_capture.build_candidates(text=text, history=history, source="raw")
        schema = self._reasoning_schema(plan_ids, [candidate.id for candidate in raw_policy_candidates])

        messages = [
            {
                "role": "system",
                "content": (
                    "You handle policy renewals. Ask for policy id first if missing. "
                    "Use selected_plan_id only from provided options. "
                    "If user asks what plan they are on (or says they do not know their current plan), "
                    "use explain_current_plan. "
                    "If user asks to switch service or asks for other help, use handoff_intent. "
                    "When policy_candidates are provided, choose selected_policy_candidate_id from them when possible. "
                    "When no policy candidate applies, set selected_policy_candidate_id to __NONE__. "
                    "Set renewal_confirmation to confirmed/rejected/unknown based on the current user turn. "
                    "Use confirm_renewal only when policy_id and selected plan are present. "
                    "Return strict JSON."
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
                "selected_plan_id=%r selected_policy_candidate_id=%r"
            ),
            session_ctx.session_id,
            reasoning.action,
            reasoning.handoff_intent,
            reasoning.selected_plan_id,
            reasoning.selected_policy_candidate_id,
        )

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

        if reasoning.action in {"list_plans", "confirm_renewal"} and reasoning.selected_plan_id:
            entities["selected_plan_id"] = reasoning.selected_plan_id

        if reasoning.action == "handoff_intent":
            requested_flow = self._requested_flow_from_handoff(reasoning.handoff_intent, current_flow="policy_renewal")
            if requested_flow:
                entities["requested_flow"] = requested_flow
            return FlowResult(
                response_text=(
                    reasoning.message_to_user
                    or "Sure. Tell me which service you need next: appointment, renewal, plan, or callback."
                ),
                completed=False,
                updated_entities=dict(entities),
                progress_key="handoff",
            )

        if reasoning.action == "cancel":
            entities.pop("renewal_confirmation_pending", None)
            return FlowResult(
                response_text=reasoning.message_to_user,
                completed=True,
                updated_entities=dict(entities),
                progress_key="done",
            )

        if not policy_id:
            return FlowResult(
                response_text="Please share your policy id in the format POL-1234 so I can process renewal.",
                completed=False,
                updated_entities=dict(entities),
                progress_key="need_policy_id",
            )

        holder = self._policy_store.find_policyholder(policy_id)
        if holder is None:
            entities.pop("policy_id", None)
            return FlowResult(
                response_text="I could not find that policy id. Please provide a valid POL-1234 id.",
                completed=False,
                updated_entities=dict(entities),
                progress_key="need_valid_policy_id",
            )

        if reasoning.action == "ask_policy_id":
            return FlowResult(
                response_text="Please share your policy id in the format POL-1234 so I can process renewal.",
                completed=False,
                updated_entities=dict(entities),
                progress_key="need_policy_id",
            )

        if reasoning.action == "clarify":
            return FlowResult(
                response_text=reasoning.message_to_user,
                completed=False,
                updated_entities=dict(entities),
                progress_key="clarify_renewal",
            )

        if reasoning.action == "explain_current_plan":
            return FlowResult(
                response_text=self._current_plan_then_renewal_prompt(holder),
                completed=False,
                updated_entities=dict(entities),
                progress_key="need_plan_selection",
            )

        selected_plan_id = entities.get("selected_plan_id")
        if reasoning.action == "list_plans":
            return FlowResult(
                response_text=self._renewal_plan_prompt(),
                completed=False,
                updated_entities=dict(entities),
                progress_key="need_plan_selection",
            )

        if reasoning.action == "confirm_renewal":
            if not selected_plan_id:
                return FlowResult(
                    response_text=self._renewal_plan_prompt(),
                    completed=False,
                    updated_entities=dict(entities),
                    progress_key="need_plan_selection",
                )
            textual_confirmation = self._infer_confirmation_from_text(text)
            effective_confirmation = reasoning.renewal_confirmation
            if effective_confirmation == "unknown":
                effective_confirmation = textual_confirmation

            summary = self._renewal_confirmation_prompt(policy_id=policy_id, selected_plan_id=selected_plan_id)
            pending_confirmation = entities.get("renewal_confirmation_pending") == "true"

            if effective_confirmation == "rejected":
                entities.pop("renewal_confirmation_pending", None)
                entities.pop("selected_plan_id", None)
                return FlowResult(
                    response_text="Okay, I will not renew yet. Which plan should I use instead?",
                    completed=False,
                    updated_entities=dict(entities),
                    progress_key="need_plan_selection",
                )

            if not pending_confirmation and effective_confirmation != "confirmed":
                entities["renewal_confirmation_pending"] = "true"
                return FlowResult(
                    response_text=summary,
                    completed=False,
                    updated_entities=dict(entities),
                    progress_key="confirm_renewal",
                )

            if pending_confirmation and effective_confirmation != "confirmed":
                return FlowResult(
                    response_text=summary,
                    completed=False,
                    updated_entities=dict(entities),
                    progress_key="confirm_renewal",
                )

            updated = self._policy_store.renew_policy(
                policy_id=policy_id,
                new_plan_id=selected_plan_id,
                today=date.today(),
            )
            if updated is None:
                return FlowResult(
                    response_text="I could not complete renewal with that plan. Please pick a listed plan.",
                    completed=False,
                    updated_entities=dict(entities),
                    progress_key="need_plan_selection",
                )
            plan = self._policy_store.get_plan(selected_plan_id)
            entities.pop("selected_plan_id", None)
            entities.pop("renewal_confirmation_pending", None)
            return FlowResult(
                response_text=(
                    f"Your policy {policy_id} is renewed. "
                    f"Your active plan is {plan['name']} at ${plan['monthly_price_usd']} per month."
                ),
                completed=True,
                updated_entities=dict(entities),
                progress_key="done",
            )

        if not selected_plan_id:
            return FlowResult(
                response_text=self._renewal_plan_prompt(),
                completed=False,
                updated_entities=dict(entities),
                progress_key="need_plan_selection",
            )

        return FlowResult(
            response_text=reasoning.message_to_user,
            completed=False,
            updated_entities=dict(entities),
            progress_key="clarify_renewal",
        )

    async def _reason(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        groq_client: Any,
        model_name: str,
    ) -> PolicyRenewalReasoning:
        try:
            parsed = await self._call_structured("policy_renewal_reasoning", schema, messages, groq_client, model_name)
            return PolicyRenewalReasoning.model_validate(parsed)
        except Exception:
            correction_messages = messages + [
                {
                    "role": "system",
                    "content": "Previous output was invalid. Return only JSON matching schema exactly.",
                }
            ]
            try:
                parsed = await self._call_structured(
                    "policy_renewal_reasoning", schema, correction_messages, groq_client, model_name
                )
                return PolicyRenewalReasoning.model_validate(parsed)
            except Exception:
                return PolicyRenewalReasoning(
                    action="ask_policy_id",
                    message_to_user="Please share your policy id in the format POL-1234.",
                    extracted_policy_id=None,
                    selected_policy_candidate_id=None,
                    selected_plan_id=None,
                    renewal_confirmation="unknown",
                    handoff_intent=None,
                )

    def _renewal_plan_prompt(self) -> str:
        lines = [f"{p['name']} (${p['monthly_price_usd']}/month)" for p in self._policy_store.list_plans()]
        return "Available plans are: " + ", ".join(lines) + ". Which plan should I renew you with?"

    def _renewal_confirmation_prompt(self, *, policy_id: str, selected_plan_id: str) -> str:
        plan = self._policy_store.get_plan(selected_plan_id)
        if plan is None:
            return "Please confirm renewal with yes or no."
        return (
            f"Please confirm: renew policy {policy_id} with {plan['name']} at "
            f"${plan['monthly_price_usd']} per month. Reply yes or no."
        )

    def _infer_confirmation_from_text(self, text: str) -> str:
        if self._AFFIRMATIVE_RE.search(text):
            return "confirmed"
        if self._NEGATIVE_RE.search(text):
            return "rejected"
        return "unknown"

    def _current_plan_then_renewal_prompt(self, holder: dict[str, Any]) -> str:
        current_plan_id = str(holder.get("current_plan_id", "")).strip()
        current = self._policy_store.get_plan(current_plan_id)
        if current is None:
            return "I could not find your current plan. " + self._renewal_plan_prompt()
        return (
            f"Your current plan is {current['name']} at ${current['monthly_price_usd']} per month. "
            + self._renewal_plan_prompt()
        )

    def _requested_flow_from_handoff(self, handoff_intent: str | None, *, current_flow: str) -> str | None:
        return resolve_handoff_flow(handoff_intent, current_flow=current_flow)

    def _reasoning_schema(self, plan_ids: list[str], policy_candidate_ids: list[str]) -> dict[str, Any]:
        schema = to_groq_strict_schema(PolicyRenewalReasoning.model_json_schema())
        selected_plan = schema.get("properties", {}).get("selected_plan_id")
        if isinstance(selected_plan, dict):
            selected_plan.clear()
            if plan_ids:
                selected_plan.update({"anyOf": [{"type": "string", "enum": plan_ids}, {"type": "null"}]})
            else:
                selected_plan.update({"anyOf": [{"type": "null"}]})
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
