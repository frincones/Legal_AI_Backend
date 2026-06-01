"""POST /api/chat/{session_id} — stream SSE con el contrato del bridge.

Sprint 1.1: loop real con Claude (Anthropic) + persistencia (ver agent/runner.py).
El ReAct loop con plugins/tools/guardrails del Agent SDK llega en sprints posteriores.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..agent.runner import run_chat
from ..auth import Principal, get_principal

router = APIRouter()


class ChatRequest(BaseModel):
    message: str


@router.post("/api/chat/{session_id}")
async def chat(
    session_id: str,
    body: ChatRequest,
    principal: Principal = Depends(get_principal),
) -> StreamingResponse:
    return StreamingResponse(
        run_chat(session_id, principal, body.message),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
