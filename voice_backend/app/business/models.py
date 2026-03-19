from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class IntentType(str, Enum):
    APPOINTMENT = "APPOINTMENT"
    POLICY_RENEWAL = "POLICY_RENEWAL"
    PLAN_INQUIRY = "PLAN_INQUIRY"
    CALLBACK_SUPPORT = "CALLBACK_SUPPORT"
    UNCLEAR = "UNCLEAR"


class IntentResult(BaseModel):
    intent: IntentType = Field(
        description=(
            "The detected user intent. Use APPOINTMENT if the user wants to book, schedule, "
            "or arrange a medical appointment. Use POLICY_RENEWAL for policy renewal requests. "
            "Use PLAN_INQUIRY for plan details/comparison requests. "
            "Use CALLBACK_SUPPORT for callback status/explanation/reschedule requests. Use UNCLEAR otherwise."
        )
    )
    confidence: float = Field(description="Confidence score between 0.0 and 1.0.")
    extracted_entities: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Entities extracted from speech, e.g. {'date': '2025-08-01', 'clinic': 'City Clinic'}."
        ),
    )
    reasoning: str = Field(description="One-sentence justification for the chosen intent.")


class SessionState(str, Enum):
    IDLE = "IDLE"
    AWAITING_CLARIFICATION = "AWAITING_CLARIFICATION"
    IN_FLOW = "IN_FLOW"
    SCHEDULING_CALLBACK = "SCHEDULING_CALLBACK"
    COMPLETED = "COMPLETED"


class SessionContext(BaseModel):
    session_id: str = Field(description="Unique identifier for this WebSocket session.")
    state: SessionState = Field(default=SessionState.IDLE, description="Current state of the conversation.")
    intent_attempts: int = Field(default=0, description="Number of consecutive turns where intent was UNCLEAR.")
    pending_entities: dict[str, str] = Field(
        default_factory=dict,
        description="Entities collected so far for the active flow, e.g. date and clinic.",
    )
    active_flow: str | None = Field(default=None, description="Name of the currently active flow, or None.")
    conversation_history: list[dict[str, str]] = Field(
        default_factory=list,
        description="List of {role, content} dicts, capped at last 10 turns, passed to every LLM call.",
    )
    callback_mode: str | None = Field(
        default=None,
        description="Callback sub-state, e.g. collect_phone or confirm_exit.",
    )
    callback_resume_text: str | None = Field(
        default=None,
        description="User text to resume booking flow after exiting callback collection.",
    )
    flow_counters: dict[str, int] = Field(
        default_factory=dict,
        description="Per-flow lightweight counters (e.g., repeated uncertainty).",
    )
    callback_digits_buffer: str = Field(
        default="",
        description="Accumulated callback phone digits captured across multiple turns.",
    )
    callback_date_iso: str | None = Field(
        default=None,
        description="Captured callback date in ISO format once the caller confirms it.",
    )
    last_booking_context: dict[str, str] = Field(
        default_factory=dict,
        description="Snapshot of booking entities when switching into callback flow.",
    )
    last_confirmed_appointment_id: str | None = Field(
        default=None,
        description="Latest persisted appointment id for this session, if any.",
    )
    onboarding_stage: str | None = Field(
        default=None,
        description="Onboarding state machine marker for first-turn client qualification.",
    )
    is_known_client: bool | None = Field(
        default=None,
        description="Tri-state client qualification status for current session.",
    )
    expiry_notice_policy_id: str | None = Field(
        default=None,
        description="Policy id for which expiry-soon notice was already emitted in this session.",
    )
    policy_gate_unavailable_count: int = Field(
        default=0,
        description="Consecutive turns where user indicates no available policy id in client-only flows.",
    )
    provider_limited_until_epoch: float = Field(
        default=0.0,
        description="Unix timestamp until which LLM provider calls should be avoided after rate limiting.",
    )
    provider_limited_reason: str | None = Field(
        default=None,
        description="Most recent provider-limited reason string for diagnostics.",
    )


class FlowResult(BaseModel):
    response_text: str = Field(description="Text to speak aloud to the user.")
    completed: bool = Field(description="True if the flow has reached a terminal state (booking confirmed or aborted).")
    schedule_callback: bool = Field(
        default=False,
        description="True if the session should be escalated to a callback.",
    )
    updated_entities: dict[str, str] = Field(
        default_factory=dict,
        description="Any new entities extracted during this flow turn.",
    )
    progress_key: str | None = Field(
        default=None,
        description="Canonical next-step marker for loop detection (e.g., need_policy_id).",
    )


class AgentResponse(BaseModel):
    text: str = Field(description="Final text to pass to TTS.")
    should_end_session: bool = Field(
        default=False,
        description="True if the WebSocket session should be closed after this response.",
    )


class AppointmentReasoning(BaseModel):
    action: Literal[
        "ask_date",
        "ask_clinic",
        "ask_policy_id",
        "ask_doctor_name",
        "confirm_doctor",
        "list_clinics",
        "list_doctors",
        "list_earliest_availability",
        "confirm_booking",
        "cancel",
        "clarify",
    ] = Field(
        description=(
            "Next action to take. ask_date: date is missing. ask_clinic: clinic is missing. "
            "ask_policy_id: policy id is missing. ask_doctor_name: doctor name is missing. "
            "confirm_doctor: confirm selected doctor before booking proceeds. "
            "list_clinics/list_doctors: user asked for options or is unsure. "
            "list_earliest_availability: user asked for earliest available doctor/date options. "
            "clarify: user input is noisy/unclear. "
            "confirm_booking: date, clinic, policy id, and doctor name are present and confirmed by user. "
            "cancel: user wants to stop."
        )
    )
    message_to_user: str = Field(description="The exact message to speak to the user for this action.")
    extracted_date: str | None = Field(
        default=None,
        description="ISO date string if a date was mentioned in this turn, else None.",
    )
    extracted_clinic: str | None = Field(
        default=None,
        description="Clinic name or id if a clinic was mentioned in this turn, else None.",
    )
    extracted_policy_id: str | None = Field(
        default=None,
        description="Policy id if mentioned in this turn, else None.",
    )
    selected_policy_candidate_id: str | None = Field(
        default=None,
        description=(
            "Selected policy candidate id from provided policy candidates for this turn. "
            "Use null when no candidate is available."
        ),
    )
    extracted_doctor_name: str | None = Field(
        default=None,
        description="Doctor name if mentioned in this turn, else None.",
    )
    selected_clinic_id: str | None = Field(
        default=None,
        description="Canonical clinic id selected from provided options, else None.",
    )
    selected_doctor_id: str | None = Field(
        default=None,
        description="Canonical doctor id selected from provided options, else None.",
    )
    selected_date: str | None = Field(
        default=None,
        description="Canonical appointment date selected from provided options, else None.",
    )
    doctor_confirmation: Literal["confirmed", "rejected", "unknown"] = Field(
        default="unknown",
        description="Doctor confirmation state inferred from the current turn.",
    )
    booking_confirmation: Literal["confirmed", "rejected", "unknown"] = Field(
        default="unknown",
        description=(
            "Final booking confirmation state for this turn. "
            "Use confirmed only for explicit acceptance of the final booking summary."
        ),
    )
    policy_gate_signal: Literal["valid", "missing_or_invalid", "unavailable_or_not_client", "unknown"] = Field(
        default="unknown",
        description=(
            "Policy gate signal for this turn. "
            "unavailable_or_not_client means user indicates they do not have a policy id or are not a client."
        ),
    )


class OnboardingReasoning(BaseModel):
    action: Literal[
        "confirm_client",
        "not_client",
        "plans_then_callback",
        "callback_now",
        "decline",
        "clarify",
    ] = Field(description="Structured onboarding decision for current onboarding stage.")
    message_to_user: str = Field(description="Short user-facing reply for this onboarding step.")


class CallbackReasoning(BaseModel):
    action: Literal[
        "ask_phone",
        "ask_callback_date",
        "confirm_switch_to_booking",
        "switch_to_booking",
        "confirm_callback",
        "cancel",
    ] = (
        Field(
            description=(
                "ask_phone: still collecting callback phone. "
                "ask_callback_date: callback phone is confirmed and callback date is still missing/invalid. "
                "confirm_switch_to_booking: user seems to want booking, ask explicit yes/no. "
                "switch_to_booking: user confirmed switch back to booking. "
                "confirm_callback: valid phone and callback date are confirmed; callback can be queued. "
                "cancel: user cancels callback flow."
            )
        )
    )
    message_to_user: str = Field(description="The exact message to speak to the user for this action.")
    extracted_phone: str | None = Field(
        default=None,
        description="Phone value captured from user utterance, if present.",
    )
    selected_phone_candidate_id: str | None = Field(
        default=None,
        description=(
            "Selected candidate id from callback phone candidate list for this turn. "
            "Use null when no numeric candidate is available."
        ),
    )
    extracted_callback_date: str | None = Field(
        default=None,
        description="ISO callback date candidate (YYYY-MM-DD) inferred from user utterance, else null.",
    )


class PolicyRenewalReasoning(BaseModel):
    action: Literal[
        "ask_policy_id",
        "list_plans",
        "explain_current_plan",
        "confirm_renewal",
        "handoff_intent",
        "clarify",
        "cancel",
    ] = Field(description="Policy renewal step action.")
    message_to_user: str = Field(description="The exact message to speak to the user for this action.")
    extracted_policy_id: str | None = Field(default=None, description="Policy id extracted from user utterance.")
    selected_policy_candidate_id: str | None = Field(
        default=None,
        description=(
            "Selected policy candidate id from provided policy candidates for this turn. "
            "Use __NONE__ when no candidate is available."
        ),
    )
    selected_plan_id: str | None = Field(default=None, description="Selected canonical plan id.")
    renewal_confirmation: Literal["confirmed", "rejected", "unknown"] = Field(
        default="unknown",
        description="User confirmation status for executing renewal on the selected plan.",
    )
    handoff_intent: Literal["APPOINTMENT", "POLICY_RENEWAL", "PLAN_INQUIRY", "CALLBACK_SUPPORT"] | None = (
        Field(default=None, description="Optional service intent hint when action is handoff_intent.")
    )


class PlanInquiryReasoning(BaseModel):
    action: Literal[
        "list_plans",
        "ask_policy_id",
        "compare_with_current_plan",
        "offer_renewal",
        "handoff_intent",
        "clarify",
        "cancel",
    ] = Field(description="Plan inquiry action.")
    message_to_user: str = Field(description="The exact message to speak to the user for this action.")
    extracted_policy_id: str | None = Field(default=None, description="Policy id extracted from user utterance.")
    selected_policy_candidate_id: str | None = Field(
        default=None,
        description=(
            "Selected policy candidate id from provided policy candidates for this turn. "
            "Use __NONE__ when no candidate is available."
        ),
    )
    selected_plan_id: str | None = Field(default=None, description="Selected canonical plan id from known options.")
    handoff_intent: Literal["APPOINTMENT", "POLICY_RENEWAL", "PLAN_INQUIRY", "CALLBACK_SUPPORT"] | None = (
        Field(default=None, description="Optional service intent hint when action is handoff_intent.")
    )


class ClinicChangeReasoning(BaseModel):
    action: Literal["ask_policy_id", "list_clinics", "confirm_clinic_change", "handoff_intent", "clarify", "cancel"] = Field(
        description="Clinic change action."
    )
    message_to_user: str = Field(description="The exact message to speak to the user for this action.")
    extracted_policy_id: str | None = Field(default=None, description="Policy id extracted from user utterance.")
    selected_policy_candidate_id: str | None = Field(
        default=None,
        description=(
            "Selected policy candidate id from provided policy candidates for this turn. "
            "Use __NONE__ when no candidate is available."
        ),
    )
    extracted_clinic: str | None = Field(default=None, description="Clinic mention from user utterance.")
    selected_clinic_id: str | None = Field(default=None, description="Selected canonical clinic id.")
    handoff_intent: Literal["APPOINTMENT", "POLICY_RENEWAL", "PLAN_INQUIRY", "CALLBACK_SUPPORT"] | None = (
        Field(default=None, description="Optional service intent hint when action is handoff_intent.")
    )


class CallbackSupportReasoning(BaseModel):
    action: Literal[
        "confirm_status",
        "explain_last_transition",
        "offer_reschedule",
        "collect_phone_if_needed",
        "handoff_intent",
        "clarify",
    ] = Field(description="Callback support action for status, explanation, or reschedule requests.")
    message_to_user: str = Field(description="The exact message to speak to the user for this action.")
    handoff_intent: Literal["APPOINTMENT", "POLICY_RENEWAL", "PLAN_INQUIRY", "CALLBACK_SUPPORT"] | None = (
        Field(default=None, description="Optional service intent hint when action is handoff_intent.")
    )
