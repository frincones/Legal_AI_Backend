"""Biblioteca (F4) — documentos generados del org + patrones reutilizables. Aditivo, solo lectura.

Incluye export PDF on-demand (F5): GET /api/artifacts/{id}/pdf convierte el DOCX guardado a PDF
en un sandbox E2B aislado (sin tocar el flujo de generación).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from .. import db
from ..auth import Principal, get_principal
from ..tools import codepdf, storage

router = APIRouter()


@router.get("/api/artifacts")
async def list_artifacts(principal: Principal = Depends(get_principal), limit: int = 50) -> list:
    """Documentos generados por la organización (para la Biblioteca). Incluye la versión actual."""
    org_id = principal.org_id or await db.resolve_org(principal.user_id)
    if not org_id:
        return []
    limit = max(1, min(limit, 100))
    try:
        rows = await db.select(
            "artifacts",
            f"org_id=eq.{org_id}&select=id,title,kind,created_at,current_version_id,"
            f"artifact_versions(version,storage_path)&order=created_at.desc&limit={limit}",
        )
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in rows:
        vers = r.get("artifact_versions") or []
        latest = max((v.get("version") or 1) for v in vers) if vers else 1
        out.append({
            "id": r["id"], "title": r.get("title") or "Documento", "kind": r.get("kind") or "document",
            "created_at": r.get("created_at"), "version": latest,
        })
    return out


@router.get("/api/patrones")
async def list_patrones(principal: Principal = Depends(get_principal), limit: int = 50) -> list:
    """Plantillas reutilizables de la firma (flywheel), ordenadas por uso. 'usado N veces'."""
    org_id = principal.org_id or await db.resolve_org(principal.user_id)
    if not org_id:
        return []
    limit = max(1, min(limit, 100))
    try:
        rows = await db.select(
            "documentos_patron",
            f"org_id=eq.{org_id}&select=id,title,kind,used_count,created_at,updated_at"
            f"&order=used_count.desc,updated_at.desc&limit={limit}",
        )
    except Exception:  # noqa: BLE001
        return []
    return rows


@router.get("/api/artifacts/{artifact_id}/pdf")
async def artifact_pdf(artifact_id: str, principal: Principal = Depends(get_principal),
                       version: int | None = None) -> Response:
    """Convierte a PDF la versión indicada (o la última) de un artifact del org. On-demand, aislado."""
    org_id = principal.org_id or await db.resolve_org(principal.user_id)
    if not org_id:
        raise HTTPException(status_code=403, detail="sin organización")
    # Localiza el storage_path de la versión pedida (scope-ado por org).
    q = (f"artifact_id=eq.{artifact_id}&org_id=eq.{org_id}"
         f"&select=version,storage_path&order=version.desc&limit=1")
    if version:
        q = (f"artifact_id=eq.{artifact_id}&org_id=eq.{org_id}&version=eq.{version}"
             f"&select=version,storage_path&limit=1")
    try:
        rows = await db.select("artifact_versions", q)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"error consultando versión: {exc}")
    if not rows or not rows[0].get("storage_path"):
        raise HTTPException(status_code=404, detail="documento no encontrado")
    path = rows[0]["storage_path"]
    try:
        docx = await storage.download(path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=f"no se pudo leer el DOCX: {exc}")
    pdf, err = await codepdf.docx_to_pdf(docx)
    if err or not pdf:
        raise HTTPException(status_code=503, detail=f"conversión a PDF no disponible: {err}")
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{artifact_id}.pdf"'})
