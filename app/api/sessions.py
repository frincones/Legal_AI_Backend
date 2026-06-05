"""Sessions — lista de conversaciones recientes para el sidebar (F1.3). Aditivo, solo lectura."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import db
from ..auth import Principal, get_principal

router = APIRouter()


@router.get("/api/sessions")
async def list_sessions(principal: Principal = Depends(get_principal), limit: int = 20) -> list:
    """Conversaciones recientes del usuario en su organización (para 'Recientes' del sidebar)."""
    org_id = principal.org_id or await db.resolve_org(principal.user_id)
    if not org_id:
        return []
    limit = max(1, min(limit, 50))
    try:
        rows = await db.select(
            "chat_sessions",
            f"org_id=eq.{org_id}&select=id,title,updated_at,model_tier&order=updated_at.desc&limit={limit}",
        )
    except Exception:  # noqa: BLE001
        return []
    return rows


@router.get("/api/sessions/{session_id}")
async def get_session(session_id: str, principal: Principal = Depends(get_principal)) -> dict:
    """Mensajes de una conversación (para abrirla desde 'Recientes'). Scope-ado por org."""
    org_id = principal.org_id or await db.resolve_org(principal.user_id)
    if not org_id:
        return {"messages": []}
    try:
        # Verifica que la sesión sea del org del usuario (aislamiento).
        sess = await db.select("chat_sessions", f"id=eq.{session_id}&org_id=eq.{org_id}&select=id,title&limit=1")
        if not sess:
            return {"messages": []}
        rows = await db.select(
            "messages",
            f"session_id=eq.{session_id}&order=seq.asc&select=role,seq,message_parts(idx,type,text)")
    except Exception:  # noqa: BLE001
        return {"messages": []}
    out = []
    for r in rows:
        if r.get("role") not in ("user", "assistant"):
            continue
        parts = sorted(r.get("message_parts") or [], key=lambda p: p.get("idx", 0))
        text = "".join((p.get("text") or "") for p in parts if p.get("type") == "text")
        if text.strip():
            out.append({"role": r["role"], "text": text})
    return {"id": session_id, "title": sess[0].get("title"), "messages": out}
