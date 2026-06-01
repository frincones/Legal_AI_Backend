"""Healthcheck + readiness (verifica conectividad a Supabase)."""
from __future__ import annotations

import httpx
from fastapi import APIRouter

from ..config import settings

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "legal-ai-backend", "version": "0.0.1"}


@router.get("/ready")
async def ready() -> dict:
    supabase_ok = False
    if settings.supabase_url and settings.supabase_service_role_key:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(
                    f"{settings.supabase_url}/rest/v1/plugins?select=key&limit=1",
                    headers={
                        "apikey": settings.supabase_service_role_key,
                        "Authorization": f"Bearer {settings.supabase_service_role_key}",
                    },
                )
                supabase_ok = r.status_code == 200
        except Exception:  # noqa: BLE001
            supabase_ok = False
    return {"status": "ok" if supabase_ok else "degraded", "supabase": supabase_ok}
