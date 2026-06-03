"""Supabase Storage — subida de artifacts + signed URL (service_role)."""
from __future__ import annotations

import httpx

from ..config import settings

BUCKET = "artifacts"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _h(extra: dict | None = None) -> dict:
    h = {"Authorization": f"Bearer {settings.supabase_service_role_key}",
         "apikey": settings.supabase_service_role_key}
    if extra:
        h.update(extra)
    return h


async def ensure_bucket() -> None:
    async with httpx.AsyncClient(timeout=15) as c:
        await c.post(f"{settings.supabase_url}/storage/v1/bucket",
                     headers=_h({"Content-Type": "application/json"}),
                     json={"id": BUCKET, "name": BUCKET, "public": False})  # 400 si ya existe → ok


async def upload(path: str, data: bytes, content_type: str = DOCX_MIME) -> str:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            f"{settings.supabase_url}/storage/v1/object/{BUCKET}/{path}",
            headers=_h({"Content-Type": content_type, "x-upsert": "true"}),
            content=data,
        )
        r.raise_for_status()
    return path


async def download(path: str) -> bytes:
    """Descarga bytes de un objeto del bucket (service_role). Para export PDF on-demand."""
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            f"{settings.supabase_url}/storage/v1/object/{BUCKET}/{path}",
            headers=_h(),
        )
        r.raise_for_status()
        return r.content


async def signed_url(path: str, expires: int = 3600) -> str:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            f"{settings.supabase_url}/storage/v1/object/sign/{BUCKET}/{path}",
            headers=_h({"Content-Type": "application/json"}),
            json={"expiresIn": expires},
        )
        r.raise_for_status()
        signed = r.json()["signedURL"]
    return f"{settings.supabase_url}/storage/v1{signed}"
