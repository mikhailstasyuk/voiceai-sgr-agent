from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class IntentType(str, Enum):
    APPOINTMENT = "APPOINTMENT"
    UNCLEAR = "UNCLEAR"


class IntentResult(BaseModel):
    intent: IntentType = Field(
        description=(
            "The detected user intent. Use APPOINTMENT if the user wants to book, schedule, "
            "or arrange a medical appointment. Use UNCLEAR otherwise."
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


class AgentResponse(BaseModel):
    text: str = Field(description="Final text to pass to TTS.")
    should_end_session: bool = Field(
        default=False,
        description="True if the WebSocket session should be closed after this response.",
    )


class AppointmentReasoning(BaseModel):
    action: Literal["ask_date", "ask_clinic", "ask_policy_id", "ask_doctor_name", "confirm_booking", "cancel"] = Field(
        description=(
            "Next action to take. ask_date: date is missing. ask_clinic: clinic is missing. "
            "ask_policy_id: policy id is missing. ask_doctor_name: doctor name is missing. "
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
    extracted_doctor_name: str | None = Field(
        default=None,
        description="Doctor name if mentioned in this turn, else None.",
    )
