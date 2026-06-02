"""Cliente Supabase (PostgREST) con service_role.

service_role BYPASSA RLS → toda query se scope-a explícitamente por org_id.
El service key vive solo en el backend, nunca llega al browser.
"""
from __future__ import annotations

import httpx

from .config import settings


def _headers(extra: dict | None = None) -> dict:
    h = {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _rest() -> str:
    return f"{settings.supabase_url}/rest/v1"


async def insert(table: str, row: dict, returning: bool = False) -> list | None:
    pref = "return=representation" if returning else "return=minimal"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{_rest()}/{table}", headers=_headers({"Prefer": pref}), json=row)
        r.raise_for_status()
        return r.json() if returning and r.content else None


async def upsert(table: str, row: dict, on_conflict: str) -> None:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            f"{_rest()}/{table}?on_conflict={on_conflict}",
            headers=_headers({"Prefer": "return=minimal,resolution=ignore-duplicates"}),
            json=row,
        )
        r.raise_for_status()


async def patch(table: str, query: str, row: dict) -> None:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.patch(f"{_rest()}/{table}?{query}", headers=_headers(), json=row)
        r.raise_for_status()


async def select(table: str, query: str) -> list:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{_rest()}/{table}?{query}", headers=_headers())
        r.raise_for_status()
        return r.json()


async def rpc(fn: str, params: dict) -> list:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{_rest()}/rpc/{fn}", headers=_headers(), json=params)
        r.raise_for_status()
        return r.json() if r.content else []


async def resolve_org(user_id: str | None) -> str | None:
    """org_id de la primera membresía activa del usuario."""
    if not user_id:
        return None
    rows = await select("memberships", f"select=org_id&user_id=eq.{user_id}&status=eq.active&limit=1")
    return rows[0]["org_id"] if rows else None


async def next_seq(session_id: str) -> int:
    rows = await select("messages", f"select=seq&session_id=eq.{session_id}&order=seq.desc&limit=1")
    return (rows[0]["seq"] + 1) if rows else 0
