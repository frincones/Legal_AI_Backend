"""Integraciones del usuario (F6.2) — conectar/gestionar herramientas Composio. Aislado por usuario.

GET    /api/integrations            → toolkits disponibles + estado de conexión del usuario
POST   /api/integrations/connect    → inicia OAuth (devuelve redirect_url de Composio)
POST   /api/integrations/sync       → reconcilia las conexiones de Composio tras el OAuth
POST   /api/integrations/toggle     → habilita/deshabilita un toolkit para el agente
DELETE /api/integrations/{toolkit}  → desconecta

Todo scope-ado por principal.user_id (server-side). Aditivo y defensivo.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import db
from ..auth import Principal, get_principal
from ..tools import composio

router = APIRouter()


class ConnectIn(BaseModel):
    toolkit: str
    callback_url: str | None = None


class ToggleIn(BaseModel):
    toolkit: str
    enabled: bool


async def _user_rows(uid: str) -> dict:
    try:
        rows = await db.select(
            "user_integrations",
            f"user_id=eq.{uid}&select=toolkit,connected_account_id,status,enabled,account_label")
    except Exception:  # noqa: BLE001
        rows = []
    by_tk: dict = {}
    for r in rows:
        # prioriza una conexión activa por toolkit
        cur = by_tk.get(r["toolkit"])
        if cur is None or (r.get("status") == "active" and cur.get("status") != "active"):
            by_tk[r["toolkit"]] = r
    return by_tk


@router.get("/api/integrations")
async def list_integrations(principal: Principal = Depends(get_principal)) -> dict:
    by_tk = await _user_rows(principal.user_id) if principal.user_id else {}
    available = []
    for slug, meta in composio.TOOLKITS.items():
        r = by_tk.get(slug)
        available.append({
            "toolkit": slug, "label": meta["label"], "icon": meta["icon"], "provider": meta["provider"],
            "connected": bool(r and r.get("status") == "active"),
            "enabled": bool(r.get("enabled")) if r else False,
            "account_label": r.get("account_label") if r else None,
        })
    return {"available": available, "composio_ready": composio.available()}


@router.post("/api/integrations/connect")
async def connect(body: ConnectIn, principal: Principal = Depends(get_principal)) -> dict:
    if not composio.available():
        return {"ok": False, "error": "Composio no configurado"}
    uid = principal.user_id
    cb = body.callback_url or "https://example.com/connected"
    res = await composio.initiate(uid, body.toolkit, cb)
    if not res or not res.get("redirect_url"):
        return {"ok": False, "error": "no se pudo iniciar la conexión"}
    # La conexión real se registra en /sync tras el OAuth (con su connected_account_id y status
    # 'active'). No insertamos placeholder aquí para evitar filas duplicadas/huérfanas.
    return {"ok": True, "redirect_url": res["redirect_url"], "toolkit": body.toolkit}


@router.post("/api/integrations/sync")
async def sync(principal: Principal = Depends(get_principal)) -> dict:
    """Tras el OAuth, trae las conexiones reales de Composio y actualiza la tabla."""
    uid = principal.user_id
    if not uid:
        return await list_integrations(principal)
    conns = await composio.list_connections(uid)
    try:
        existing = await db.select("user_integrations",
                                   f"user_id=eq.{uid}&select=toolkit,connected_account_id")
        seen = {(r["toolkit"], r.get("connected_account_id")) for r in existing}
    except Exception:  # noqa: BLE001
        seen = set()
    for c in conns:
        tk, ca, status = c.get("toolkit"), c.get("connected_account_id"), c.get("status")
        if not tk or tk not in composio.TOOLKITS:
            continue
        try:
            if (tk, ca) in seen:
                await db.patch("user_integrations",
                               f"user_id=eq.{uid}&toolkit=eq.{tk}&connected_account_id=eq.{ca}",
                               {"status": status})
            else:
                await db.insert("user_integrations", {
                    "user_id": uid, "org_id": principal.org_id, "toolkit": tk,
                    "connected_account_id": ca, "status": (status or "").lower(), "enabled": True})
        except Exception:  # noqa: BLE001
            pass
    # Limpia placeholders huérfanos (sin connected_account_id) ya reemplazados por la conexión real.
    try:
        await db.delete_rows("user_integrations", f"user_id=eq.{uid}&connected_account_id=is.null")
    except Exception:  # noqa: BLE001
        pass
    return await list_integrations(principal)


@router.post("/api/integrations/toggle")
async def toggle(body: ToggleIn, principal: Principal = Depends(get_principal)) -> dict:
    uid = principal.user_id
    try:
        await db.patch("user_integrations", f"user_id=eq.{uid}&toolkit=eq.{body.toolkit}",
                       {"enabled": body.enabled})
    except Exception:  # noqa: BLE001
        return {"ok": False}
    return {"ok": True}


@router.delete("/api/integrations/{toolkit}")
async def disconnect(toolkit: str, principal: Principal = Depends(get_principal)) -> dict:
    uid = principal.user_id
    try:
        rows = await db.select("user_integrations",
                               f"user_id=eq.{uid}&toolkit=eq.{toolkit}&select=connected_account_id")
        for r in rows:
            if r.get("connected_account_id"):
                await composio.delete_connection(r["connected_account_id"])
        await db.patch("user_integrations", f"user_id=eq.{uid}&toolkit=eq.{toolkit}",
                       {"status": "deleted", "enabled": False})
    except Exception:  # noqa: BLE001
        return {"ok": False}
    return {"ok": True}
