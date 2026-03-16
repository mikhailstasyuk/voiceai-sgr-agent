# Architecture Overview

This repository has two primary runtime surfaces:
- `voice_backend/`: FastAPI + WebSocket orchestration for ASR -> LLM -> TTS turns
- `voice_frontend/`: React/Vite client for mic capture, streaming, playback, and UI state

## High-Level Runtime Path
1. Frontend captures PCM audio and streams via WebSocket.
2. Backend forwards audio to ASR and receives transcripts/VAD events.
3. Backend invokes LLM for structured text output.
4. Backend streams synthesized TTS audio back to frontend.
5. Frontend plays audio and updates status/transcript UI.

See also:
- [domains.md](/home/rhuu/mygit/hypercheap-voiceAI/docs/architecture/domains.md)
- [layers.md](/home/rhuu/mygit/hypercheap-voiceAI/docs/architecture/layers.md)
- [system-flows.md](/home/rhuu/mygit/hypercheap-voiceAI/docs/architecture/system-flows.md)

