"""POST /api/chat/{session_id} — stream SSE con el contrato del bridge.

Fase 0: emite eventos normalizados de demostración (text_delta → done) para
validar la plomería SSE + el contrato que assistant-ui renderiza.
Fase 1: este generador se reemplaza por el loop real `ClaudeSDKClient`.
"""
from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import bridge
from ..auth import Principal, get_principal

router = APIRouter()


class ChatRequest(BaseModel):
    message: str


async def _demo_stream(session_id: str, principal: Principal, message: str) -> AsyncGenerator[str, None]:
    # init: session id (como SystemMessage init del SDK)
    yield bridge.sse(bridge.AGENT_STEP, {"task_id": session_id, "agent": "orchestrator", "status": "started"})

    reply = (
        f"Hola{(' ' + principal.email) if principal.email else ''}. "
        "Bridge SSE operativo (Fase 0). En Fase 1 esto lo emite el loop ReAct del Claude Agent SDK. "
        f"Tu mensaje fue: {message!r}"
    )
    for token in reply.split(" "):
        yield bridge.sse(bridge.TEXT_DELTA, {"text": token + " ", "message_id": session_id})
        await asyncio.sleep(0.02)

    yield bridge.sse(bridge.USAGE, {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cost_usd": 0})
    yield bridge.sse(bridge.DONE, {"session_id": session_id, "result": "ok"})


@router.post("/api/chat/{session_id}")
async def chat(
    session_id: str,
    body: ChatRequest,
    principal: Principal = Depends(get_principal),
) -> StreamingResponse:
    return StreamingResponse(
        _demo_stream(session_id, principal, body.message),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
