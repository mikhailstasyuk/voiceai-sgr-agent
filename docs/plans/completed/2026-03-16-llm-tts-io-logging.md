# LLM/TTS I/O Logging and TTS Segmentation Stability

## Goal
Capture full LLM and TTS API in/out traces per websocket session and reduce word-splitting artifacts in streamed TTS segments.

## Completion Summary (2026-03-16)
- Added TTS API request/response/chunk logs with session correlation.
- Routed TTS logs through session-specific log files by including `session_id` in log lines.
- Improved session segment flushing logic to avoid breaking words at sentence boundaries and budget cuts.
- Added unit tests for segmentation helper behavior.

## Validation Performed
- `./.venv/bin/python -m ruff check voice_backend/app/agent/session.py voice_backend/app/agent/inworld_tts.py voice_backend/app/main.py voice_backend/tests/agent/test_session_segmentation.py`
- `../.venv/bin/python -m pytest -q tests/business/test_business_layer.py tests/agent/test_session_segmentation.py` (10 passed)

## Follow-Up
- Add integration test asserting per-turn `llm_token` concatenation equals text sent to TTS synthesize.
