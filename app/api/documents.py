"""POST /api/documents — sube un adjunto, extrae texto y lo indexa (chunks).

Sprint 1.4 (ruta directa) + base para 2.3 (FTS sobre chunks).
"""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile

from .. import db
from ..auth import Principal, get_principal
from ..ingest.extract import chunk, extract_text
from ..tools import embeddings, storage

router = APIRouter()


@router.post("/api/documents")
async def upload_document(
    file: UploadFile = File(...),
    session_id: str | None = Form(default=None),
    matter_id: str | None = Form(default=None),
    principal: Principal = Depends(get_principal),
) -> dict:
    org_id = principal.org_id or await db.resolve_org(principal.user_id)
    if not org_id:
        return {"error": "sin organización"}

    data = await file.read()
    doc_id = str(uuid.uuid4())
    path = f"org/{org_id}/documents/{doc_id}/{file.filename}"
    try:
        await storage.ensure_bucket()
        await storage.upload(path, data, content_type=file.content_type or "application/octet-stream")
    except Exception as exc:  # noqa: BLE001
        return {"error": f"storage: {exc}"}

    text = extract_text(data, file.filename or "", file.content_type)
    chunks = chunk(text)

    await db.insert("documents", {
        "id": doc_id, "org_id": org_id, "matter_id": matter_id, "source": "upload",
        "title": file.filename, "mime_type": file.content_type, "storage_path": path,
        "is_untrusted": True, "ingest_status": "complete",
    })
    embedded = 0
    if chunks:
        rows = [{"org_id": org_id, "document_id": doc_id, "matter_id": matter_id,
                 "idx": i, "content": c, "token_count": len(c) // 4}
                for i, c in enumerate(chunks)]
        if embeddings.available():
            try:
                vecs = await asyncio.to_thread(embeddings.embed, chunks)
                for row, v in zip(rows, vecs):
                    row["embedding"] = embeddings.to_pgvector(v)
                embedded = len(vecs)
            except Exception:  # noqa: BLE001
                pass
        await db.insert("chunks", rows)
    return {"document_id": doc_id, "title": file.filename, "chars": len(text),
            "chunks": len(chunks), "embedded": embedded}
