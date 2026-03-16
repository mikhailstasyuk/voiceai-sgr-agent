# System Flows

## Voice Turn Flow
1. Client opens `/ws/agent`.
2. Client sends `start`; backend initializes ASR/LLM/TTS session.
3. Client streams PCM bytes.
4. ASR emits transcript/VAD events.
5. Backend forwards transcript events to client and requests LLM output.
6. Backend streams TTS audio chunks to client.
7. Client plays audio while updating UI status/transcript.
8. `stop` or disconnect closes session and resources.

## Barge-In Flow
1. ASR signals speech begin or utterance begin.
2. Backend invokes session interrupt.
3. Current assistant audio/token generation is interrupted.
4. New user utterance continues as the active turn.

## Failure Handling Flow
1. Runtime/provider exception occurs.
2. Backend logs and emits status/error event.
3. Session cleanup executes in `finally` path.
4. Client transitions to `error` or `idle` state depending on closure reason.

