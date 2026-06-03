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
