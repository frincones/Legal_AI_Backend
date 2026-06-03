"""Perfil de la firma + auditoría (F5). Datos reales para Settings y el panel de auditoría.

GET  /api/profile        → cabecera de firma + perfil + uso real (tokens/costo, conteos).
PATCH /api/profile       → preferencias de UI (tema/tono/idioma) en orgs.settings.ui (aditivo).
GET  /api/verificaciones → historial de verificaciones del org (auditoría defendible).
Todo es aditivo y defensivo; no toca el agente ni la generación.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import db
from ..auth import Principal, get_principal

router = APIRouter()

# Precios aprox (USD por 1M tokens) — alineado con admin.PRICES.
_PRICES = {
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-8": (15.0, 75.0),
}


async def _count(table: str, org_id: str) -> int:
    try:
        rows = await db.select(table, f"org_id=eq.{org_id}&select=id")
        return len(rows)
    except Exception:  # noqa: BLE001
        return 0


@router.get("/api/profile")
async def get_profile(principal: Principal = Depends(get_principal)) -> dict:
    org_id = principal.org_id or await db.resolve_org(principal.user_id)
    if not org_id:
        return {}
    firm = {"name": "Mi firma", "plan": "trial", "members": 1}
    profile: dict = {}
    ui = {"theme": "Claro", "tone": "Formal jurídico", "lang": "Español"}
    try:
        orgs = await db.select("orgs", f"id=eq.{org_id}&select=name,plan,settings&limit=1")
        if orgs:
            firm["name"] = orgs[0].get("name") or firm["name"]
            firm["plan"] = orgs[0].get("plan") or firm["plan"]
            ui.update((orgs[0].get("settings") or {}).get("ui") or {})
    except Exception:  # noqa: BLE001
        pass
    try:
        mem = await db.select("memberships", f"org_id=eq.{org_id}&status=eq.active&select=user_id")
        firm["members"] = len(mem) or 1
    except Exception:  # noqa: BLE001
        pass
    try:
        cp = await db.select("company_profiles",
                             f"org_id=eq.{org_id}&select=entity_name,primary_jurisdiction&limit=1")
        if cp:
            profile = {"entity_name": cp[0].get("entity_name"),
                       "primary_jurisdiction": cp[0].get("primary_jurisdiction")}
    except Exception:  # noqa: BLE001
        pass

    # Uso real: tokens/costo desde token_ledger + conteos.
    cost = tin = tout = 0
    try:
        ledger = await db.select(
            "token_ledger",
            f"org_id=eq.{org_id}&select=model,input_tokens,output_tokens&limit=10000")
        for r in ledger:
            inp = r.get("input_tokens", 0) or 0
            outp = r.get("output_tokens", 0) or 0
            tin += inp
            tout += outp
            pin, pout = _PRICES.get(r.get("model"), (3.0, 15.0))
            cost += inp / 1e6 * pin + outp / 1e6 * pout
    except Exception:  # noqa: BLE001
        pass
    usage = {
        "documentos": await _count("artifacts", org_id),
        "verificaciones": await _count("verificaciones", org_id),
        "input_tokens": tin, "output_tokens": tout,
        "estimated_cost_usd": round(cost, 2),
    }
    return {"firm": firm, "profile": profile, "ui": ui, "usage": usage}


class ProfilePatch(BaseModel):
    theme: str | None = None
    tone: str | None = None
    lang: str | None = None


@router.patch("/api/profile")
async def patch_profile(body: ProfilePatch, principal: Principal = Depends(get_principal)) -> dict:
    """Guarda preferencias de UI en orgs.settings.ui. No afecta lógica del agente."""
    org_id = principal.org_id or await db.resolve_org(principal.user_id)
    if not org_id:
        return {"ok": False}
    try:
        orgs = await db.select("orgs", f"id=eq.{org_id}&select=settings&limit=1")
        settings = (orgs[0].get("settings") if orgs else {}) or {}
        ui = settings.get("ui") or {}
        for k, v in {"theme": body.theme, "tone": body.tone, "lang": body.lang}.items():
            if v is not None:
                ui[k] = v
        settings["ui"] = ui
        await db.patch("orgs", f"id=eq.{org_id}", {"settings": settings})
        return {"ok": True, "ui": ui}
    except Exception:  # noqa: BLE001
        return {"ok": False}


@router.get("/api/verificaciones")
async def list_verificaciones(principal: Principal = Depends(get_principal), limit: int = 50) -> list:
    """Historial de verificaciones del org (auditoría). Solo lectura."""
    org_id = principal.org_id or await db.resolve_org(principal.user_id)
    if not org_id:
        return []
    limit = max(1, min(limit, 200))
    try:
        return await db.select(
            "verificaciones",
            f"org_id=eq.{org_id}&select=id,consulta,tipo_fuente,estado,tier,confianza,fuentes,"
            f"latency_ms,created_at&order=created_at.desc&limit={limit}")
    except Exception:  # noqa: BLE001
        return []
