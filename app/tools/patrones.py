"""Biblioteca de patrones reutilizables (F4 · flywheel).

Cada documento generado por código (docx-js) válido se guarda como un *patrón* reutilizable.
Futuras solicitudes parten de un docx-js ya verificado y solo modifican lo necesario →
menos tokens de salida, estructura probada, costo marginal decreciente.

Todo es ADITIVO y defensivo: si la tabla/RPC aún no existe o falla, hace no-op silencioso y
NUNCA afecta la generación del documento. Embeddings = fastembed local (384 dims, $0).
"""
from __future__ import annotations

import asyncio

from .. import db
from . import embeddings


async def _embed(text: str) -> str | None:
    if not text or not embeddings.available():
        return None
    try:
        vec = (await asyncio.to_thread(embeddings.embed, [text[:1000]]))[0]
        return embeddings.to_pgvector(vec)
    except Exception:  # noqa: BLE001
        return None


async def save(ctx: dict, artifact_id: str, title: str, kind: str, docx_js: str,
               params: dict | None = None) -> None:
    """Guarda un docx-js validado como patrón reutilizable. Defensivo (no-op si falla)."""
    org_id = ctx.get("org_id")
    if not org_id or not docx_js:
        return
    try:
        emb = await _embed(f"{title} {kind} {(params or {}).get('materia','')}")
        row = {
            "org_id": org_id,
            "artifact_id": artifact_id,
            "title": title or "documento",
            "kind": kind or "document",
            "docx_js": docx_js,
            "params": params or {},
            "created_by": ctx.get("user_id"),
        }
        if emb:
            row["embedding"] = emb
        await db.insert("documentos_patron", row)
    except Exception:  # noqa: BLE001
        pass


async def load(org_id: str, patron_id: str) -> dict | None:
    """Carga un patrón (para reutilización). Devuelve {title, kind, docx_js} o None."""
    if not org_id or not patron_id:
        return None
    try:
        rows = await db.select(
            "documentos_patron",
            f"id=eq.{patron_id}&org_id=eq.{org_id}&select=id,title,kind,docx_js&limit=1",
        )
        return rows[0] if rows else None
    except Exception:  # noqa: BLE001
        return None


async def bump_use(patron_id: str) -> None:
    """Incrementa used_count al reutilizar. Defensivo."""
    if not patron_id:
        return
    try:
        await db.rpc("bump_patron_use", {"p_id": patron_id})
    except Exception:  # noqa: BLE001
        pass


async def suggest(org_id: str, query: str, k: int = 1) -> list[dict]:
    """Top-k patrones semánticamente similares (opcional, para auto-sugerencia del agente)."""
    if not org_id or not query or not embeddings.available():
        return []
    try:
        vec = (await asyncio.to_thread(embeddings.embed, [query[:1000]]))[0]
        return await db.rpc("match_patrones", {
            "query_embedding": embeddings.to_pgvector(vec), "p_org": org_id, "k": k})
    except Exception:  # noqa: BLE001
        return []
