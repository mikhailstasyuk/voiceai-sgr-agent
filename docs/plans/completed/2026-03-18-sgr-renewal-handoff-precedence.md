# SGR Renewal and Non-Appointment Handoff Precedence

## Goal
Fix SGR action override loops in non-appointment flows, starting with policy renewal current-plan questions and handoff/cancel precedence.

## Context
- Latest log `ws_20260318T104841Z_137241870217344.log` showed renewal loop repetition when caller asked which plan they were currently on.
- Root cause: missing-entity fallback branches could override structured SGR actions (including `handoff_intent` and `cancel`).

## Decisions
1. Extend policy renewal reasoning with `explain_current_plan` action.
2. In policy renewal and clinic change flows, apply `handoff_intent`/`cancel`/`clarify` before policy/eligibility/list fallback gating.
3. Use structured `handoff_intent` hints to set `requested_flow` directly when valid, with `__intent_reroute__` fallback.
4. Keep deterministic logic only for boundary validation and persistence safety.

## Validation
- `PYENV_VERSION=zephyron PYTHONPATH=/home/rhuu/mygit/hypercheap-voiceAI/voice_backend pytest tests/business/test_business_layer.py -k "policy_renewal_flow_handoff_not_overridden_by_missing_plan or policy_renewal_flow_explains_current_plan_inline or clinic_change_flow_handoff_not_blocked_by_policy_gate or business_layer_handoff_hint_sets_requested_flow_directly"`
- `PYENV_VERSION=zephyron PYTHONPATH=/home/rhuu/mygit/hypercheap-voiceAI/voice_backend pytest tests/business/test_business_layer.py -k "policy_renewal or clinic_change or reroutes_from_active_flow_back_to_intent_entry"`

## Outcome
- Renewal flow can answer current-plan questions inline and continue renewal selection.
- Non-appointment SGR handoff actions are no longer overridden by missing-entity fallback branches.
- Flow routing can consume structured handoff hints directly without forced reclassification.
