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
    document_ids: list[str] | None = None
    edit_artifact_id: str | None = None   # F3: editar un documento existente → nueva versión
    selection: str | None = None          # F3: texto seleccionado en el documento (highlight-to-edit)
    reuse_patron_id: str | None = None     # F4: reutilizar un patrón de la biblioteca → documento nuevo


@router.post("/api/chat/{session_id}")
async def chat(
    session_id: str,
    body: ChatRequest,
    principal: Principal = Depends(get_principal),
) -> StreamingResponse:
    return StreamingResponse(
        run_chat(session_id, principal, body.message, body.document_ids,
                 edit_artifact_id=body.edit_artifact_id, selection=body.selection,
                 reuse_patron_id=body.reuse_patron_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
