# Layers

## Backend Layers
1. **Delivery Layer**
   - FastAPI routes/WebSocket handlers (`app/main.py`)
2. **Orchestration Layer**
   - Session coordination, callbacks, turn control (`app/agent/session.py`)
3. **Business Layer**
   - Intent detection, state machine, appointment flow, session store (`app/business/`)
4. **Provider Adapter Layer**
   - External service clients (`fennec_ws.py`, `llm_client.py`, `inworld_tts.py`)
5. **Schema/Protocol Layer**
   - Event models and payload contracts (`app/agent/protocol.py`)
6. **Configuration Layer**
   - Runtime settings and environment parsing (`app/config.py`)

## Frontend Layers
1. **UI Layer**
   - React components and visual state (`src/App.tsx`, `src/styles.css`)
2. **Transport + Audio Runtime Layer**
   - WS transport and audio pipeline (`src/lib/ws.ts`, `src/audio/`, `src/worklets/`)
