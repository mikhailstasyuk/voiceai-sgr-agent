# Product Requirements (Current Baseline)

## Functional Requirements
- The backend exposes a health endpoint and a voice agent WebSocket endpoint.
- The client can establish a realtime session and stream microphone audio.
- The system emits user transcript updates to the client.
- The system streams assistant audio output back to the client.
- Assistant reply generation must pass through a business layer between ASR-final text and TTS.
- The business layer currently supports one intent: appointment booking.
- Intent detection and appointment flow decisions must use strict schema-guided model outputs validated by Pydantic v2 models.
- Appointment confirmation requires date, clinic, policy id, and doctor name.
- User-facing responses should present dates in human-readable format while internal persistence remains ISO format.
- After three consecutive unclear intent detections, the assistant must escalate to callback scheduling messaging.
- The session supports barge-in behavior when user speech resumes.
- The client supports start/stop controls and clear status transitions.

## Non-Functional Requirements
- Prioritize low-latency turn response.
- Keep runtime/provider behavior configurable through environment settings.
- Maintain compatibility with local development setup documented in root `README.md`.

## Documentation Requirements
- Any user-visible behavior change must update this file or `user-workflows.md`.
- Any architecture boundary change must update `docs/architecture/`.
