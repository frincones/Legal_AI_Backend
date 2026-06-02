"""Extracción de texto de adjuntos (Sprint 1.4) + chunking simple para FTS (2.3).

Ruta directa (sin OCR/docling todavía): PDF (pypdf), DOCX (python-docx), texto plano.
"""
from __future__ import annotations

import io


def extract_text(data: bytes, filename: str, mime: str | None = None) -> str:
    name = (filename or "").lower()
    try:
        if name.endswith(".pdf") or (mime or "").endswith("pdf"):
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            return "\n\n".join((p.extract_text() or "") for p in reader.pages)
        if name.endswith(".docx") or "word" in (mime or ""):
            from docx import Document

            doc = Document(io.BytesIO(data))
            return "\n".join(p.text for p in doc.paragraphs)
    except Exception as exc:  # noqa: BLE001
        return f"[no se pudo extraer texto: {exc}]"
    # txt / fallback
    try:
        return data.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""


def chunk(text: str, size: int = 1500, overlap: int = 150) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    out, i = [], 0
    while i < len(text):
        out.append(text[i : i + size])
        i += size - overlap
    return out[:400]  # tope defensivo
