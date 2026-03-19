# SGR Intent Schema Compatibility and Plan Inquiry Recovery

## Goal
Fix latest-session failures in intent classification schema compatibility and plan-inquiry conversational looping while preserving strict schema-guided reasoning control.

## Context
- Latest logs showed provider-side schema rejection for intent structured output (`$ref` + sibling keywords), causing turn failures.
- Plan inquiry repeatedly listed plans and failed to ask for policy id / progress to renewal when user requested a plan change.
- Self-targeted `handoff_intent` produced no-op reroute noise.

## Decisions
1. Normalize strict schemas so `$ref` nodes have no sibling keywords.
2. Make intent/onboarding structured-calling paths resilient to provider/runtime exceptions via SGR-safe fallbacks.
3. Extend plan inquiry reasoning contract with `ask_policy_id` action and `selected_plan_id`.
4. Preserve SGR action precedence and suppress same-flow handoff reroutes.

## Validation
- `PYENV_VERSION=zephyron PYTHONPATH=/home/rhuu/mygit/hypercheap-voiceAI/voice_backend pytest tests/business/test_business_layer.py -k "intent_detector_fallback_to_unclear_on_provider_exception or to_groq_strict_schema_strips_siblings_from_ref_nodes or plan_inquiry_flow_asks_policy_id_for_current_plan_question_without_policy or plan_inquiry_flow_offer_renewal_carries_selected_plan or plan_inquiry_flow_ignores_self_handoff_target"`
- `PYENV_VERSION=zephyron PYTHONPATH=/home/rhuu/mygit/hypercheap-voiceAI/voice_backend pytest tests/business/test_business_layer.py -k "plan_inquiry or intent_detector_schema_is_groq_strict_compatible or reroutes_from_active_flow_back_to_intent_entry"`
