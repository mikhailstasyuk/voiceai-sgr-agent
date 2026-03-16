import contextlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI

from .agent.fennec_ws import DEFAULT_VAD, FennecWSClient
from .agent.inworld_tts import InworldTTS
from .agent.protocol import (
    AsrFinalEvent,
    AudioStartEvent,
    DoneEvent,
    LlmTokenEvent,
    SegmentDoneEvent,
    StatusEvent,
    TurnDoneEvent,
)
from .agent.session import AgentSession
from .business import BusinessLayer, SessionStore
from .config import settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("hypercheap.app")
session_logs_dir = Path(__file__).resolve().parent.parent / "logs" / "ws_sessions"
session_logs_dir.mkdir(parents=True, exist_ok=True)


class SessionIdLogFilter(logging.Filter):
    def __init__(self, session_id: str) -> None:
        super().__init__()
        self._token = f"session_id={session_id}"

    def filter(self, record: logging.LogRecord) -> bool:
        # Route only records that explicitly belong to this websocket session.
        return self._token in record.getMessage()


def build_session_file_handler(session_id: str) -> tuple[logging.Handler, Path]:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = session_logs_dir / f"ws_{ts}_{session_id}.log"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    handler.addFilter(SessionIdLogFilter(session_id))
    return handler, log_path

app = FastAPI(title="Hypercheap Voice Agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

static_files_path = os.path.join(os.path.dirname(__file__), "static")
session_store = SessionStore()
groq_client = AsyncOpenAI(api_key=settings.groq_api_key, base_url=settings.groq_base_url)
business_layer = BusinessLayer(
    groq_client=groq_client,
    model_name=settings.groq_model,
    session_store=session_store,
)


@app.get("/health")
async def health():
    return {"ok": True}


@app.websocket("/ws/agent")
async def ws_agent(ws: WebSocket):
    await ws.accept()
    session_id = str(id(ws))
    session_handler, session_log_path = build_session_file_handler(session_id)
    root_logger = logging.getLogger()
    root_logger.addHandler(session_handler)
    await ws.send_text(StatusEvent(message="connected").model_dump_json())
    log.info("[ws:session] session_id=%s event=connected session_log=%s", session_id, session_log_path)

    # Construct components
    fennec = FennecWSClient(
        api_key=settings.fennec_api_key,
        sample_rate=settings.fennec_sample_rate,
        channels=settings.fennec_channels,
        vad=DEFAULT_VAD,  # IMPORTANT: request VAD events + cadence
    )
    tts = InworldTTS(
        api_key_basic_b64=settings.inworld_api_key,
        model_id=settings.inworld_model_id,
        voice_id=settings.inworld_voice_id,
        sample_rate_hz=settings.inworld_sample_rate,
    )
    agent = AgentSession(fennec, None, tts, session_id=session_id)
    session_started = False

    async def on_asr_final(text: str):
        t0 = time.perf_counter()
        log.info("[ws:turn] session_id=%s event=asr_final text=%r", session_id, text)
        await ws.send_text(AsrFinalEvent(text=text).model_dump_json())
        try:
            response = await business_layer.process(text, session_id)
            log.info(
                "[ws:turn] session_id=%s event=business_response text=%r latency_ms=%.2f",
                session_id,
                response.text,
                (time.perf_counter() - t0) * 1000.0,
            )
            return response.text
        except Exception:
            log.exception("business layer failed for session_id=%s", session_id)
            return "I ran into an issue processing that. Please repeat your appointment request."

    async def on_llm_token(tok: str):
        await ws.send_text(LlmTokenEvent(text=tok).model_dump_json())

    async def on_tts_chunk(b: bytes):
        await ws.send_bytes(b)

    async def on_segment_done():
        await ws.send_text(SegmentDoneEvent().model_dump_json())

    async def on_audio_start():
        await ws.send_text(AudioStartEvent().model_dump_json())

    async def on_turn_done():
        await ws.send_text(TurnDoneEvent().model_dump_json())

    async def on_vad(evt: dict):
        # Forward raw VAD/utterance events to the client (UI meters, speaking state, etc.)
        await ws.send_text(json.dumps(evt))
        # Barge-in: when user starts speaking, interrupt AI output immediately
        try:
            is_speech = evt.get("type") == "vad" and evt.get("state") == "speech"
            utter_begin = evt.get("type") == "utterance" and evt.get("phase") == "begin"
            if is_speech or utter_begin:
                await agent.barge_in()
        except Exception:
            log.exception("barge-in interrupt failed")

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.receive":
                if "bytes" in msg and msg["bytes"] is not None:
                    if session_started:
                        await agent.feed_pcm(msg["bytes"])
                elif "text" in msg and msg["text"]:
                    try:
                        payload = json.loads(msg["text"])
                        if payload.get("type") == "start":
                            if session_started:
                                continue
                            await ws.send_text(StatusEvent(message="initializing").model_dump_json())
                            await agent.start(
                                on_asr_final=on_asr_final,
                                on_token=on_llm_token,
                                on_audio_chunk=on_tts_chunk,
                                on_segment_done=on_segment_done,
                                on_audio_start=on_audio_start,
                                on_turn_done=on_turn_done,
                                on_vad=on_vad,  # wire VAD events through session -> fennec
                            )
                            session_started = True
                            await ws.send_text(StatusEvent(message="ready").model_dump_json())

                        elif payload.get("type") == "stop":
                            if session_started:
                                await agent.stop()
                            await ws.send_text(DoneEvent().model_dump_json())
                            break
                    except Exception as e:
                        log.exception("Error processing client message")
                        await ws.send_text(StatusEvent(message=f"error: {e}").model_dump_json())
                        if payload.get("type") == "start" and not session_started:
                            break
            elif msg["type"] == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    finally:
        with contextlib.suppress(Exception):
            await agent.close()
        with contextlib.suppress(Exception):
            session_store.delete(session_id)
        with contextlib.suppress(Exception):
            log.info("[ws:session] session_id=%s event=disconnected", session_id)
        with contextlib.suppress(Exception):
            root_logger.removeHandler(session_handler)
        with contextlib.suppress(Exception):
            session_handler.close()
        with contextlib.suppress(Exception):
            await ws.close()


app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="static")
