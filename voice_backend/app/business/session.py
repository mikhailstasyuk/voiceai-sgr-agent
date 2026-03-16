from __future__ import annotations

import logging
import time

from .models import SessionContext

log = logging.getLogger("hypercheap.business.session_store")


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionContext] = {}

    def get_or_create(self, session_id: str) -> SessionContext:
        t0 = time.perf_counter()
        ctx = self._sessions.get(session_id)
        if ctx is None:
            ctx = SessionContext(session_id=session_id)
            self._sessions[session_id] = ctx
            log.info(
                "[store] action=create session_id=%s latency_ms=%.2f total_sessions=%d",
                session_id,
                (time.perf_counter() - t0) * 1000.0,
                len(self._sessions),
            )
            return ctx
        log.info(
            "[store] action=get session_id=%s latency_ms=%.2f total_sessions=%d",
            session_id,
            (time.perf_counter() - t0) * 1000.0,
            len(self._sessions),
        )
        return ctx

    def update(self, ctx: SessionContext) -> None:
        t0 = time.perf_counter()
        self._sessions[ctx.session_id] = ctx
        log.info(
            "[store] action=update session_id=%s latency_ms=%.2f total_sessions=%d",
            ctx.session_id,
            (time.perf_counter() - t0) * 1000.0,
            len(self._sessions),
        )

    def delete(self, session_id: str) -> None:
        t0 = time.perf_counter()
        self._sessions.pop(session_id, None)
        log.info(
            "[store] action=delete session_id=%s latency_ms=%.2f total_sessions=%d",
            session_id,
            (time.perf_counter() - t0) * 1000.0,
            len(self._sessions),
        )
