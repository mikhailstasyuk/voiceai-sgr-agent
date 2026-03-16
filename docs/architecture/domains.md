# Domains

## Conversational Runtime Domain
Primary backend domain in `voice_backend/app/agent/`:
- ASR integration and VAD events
- LLM request/response handling
- TTS streaming
- session/turn orchestration and barge-in handling

## Delivery/API Domain
Backend entrypoints in `voice_backend/app/main.py`:
- WebSocket lifecycle management
- health endpoint
- event forwarding between runtime and client

## Client Interaction Domain
Frontend in `voice_frontend/src/`:
- mic capture/resampling/worklet playback
- WebSocket client transport
- UI state and transcript rendering

## Configuration Domain
Backend settings in `voice_backend/app/config.py` and `.env` conventions:
- provider keys, model IDs, sample rates, endpoint URLs
- runtime defaults and environment binding

