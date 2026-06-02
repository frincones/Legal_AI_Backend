"""Embeddings semánticos (Sprint 3.3) — modelo local fastembed (sin key externa).

Default `BAAI/bge-small-en-v1.5` (384 dims, ligero). Para producción en español,
`EMBEDDING_MODEL` puede apuntar a un modelo multilingüe soportado por fastembed.
fastembed es sync (ONNX/CPU) → los callers lo invocan con `asyncio.to_thread`.
"""
from __future__ import annotations

from ..config import settings

_model = None
_available: bool | None = None


def available() -> bool:
    global _available
    if _available is None:
        try:
            import fastembed  # noqa: F401
            _available = True
        except Exception:  # noqa: BLE001
            _available = False
    return _available


def _m():
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(model_name=settings.embedding_model)
    return _model


def embed(texts: list[str]) -> list[list[float]]:
    return [list(map(float, v)) for v in _m().embed(texts)]


def to_pgvector(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
