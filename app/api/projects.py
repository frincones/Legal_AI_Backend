"""Projects (Sprint 3.2) — agrupación Claude-style de threads + instrucciones."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import db
from ..auth import Principal, get_principal

router = APIRouter()


class ProjectIn(BaseModel):
    name: str
    instructions_md: str | None = None
    matter_id: str | None = None


@router.get("/api/projects")
async def list_projects(principal: Principal = Depends(get_principal)) -> list:
    org_id = principal.org_id or await db.resolve_org(principal.user_id)
    if not org_id:
        return []
    return await db.select("projects", f"org_id=eq.{org_id}&select=id,name,instructions_md,created_at&order=created_at.desc")


@router.post("/api/projects")
async def create_project(body: ProjectIn, principal: Principal = Depends(get_principal)) -> dict:
    org_id = principal.org_id or await db.resolve_org(principal.user_id)
    if not org_id:
        return {"error": "sin organización"}
    rows = await db.insert("projects", {
        "org_id": org_id, "name": body.name, "instructions_md": body.instructions_md,
        "matter_id": body.matter_id, "created_by": principal.user_id,
    }, returning=True)
    return rows[0] if rows else {"ok": True}
