"""Document-by-code (Sprint F) — el modelo escribe python-docx, se corre en E2B.

Da formato AVANZADO (numeración, estilos, tablas) controlado por el modelo. El
código debe guardar en /tmp/out.docx.

IMPORTANTE: usamos el Sandbox SÍNCRONO de E2B dentro de `asyncio.to_thread`, no el
AsyncSandbox. El AsyncSandbox corre httpx en el event loop del request y, al cerrarse,
rompía el cliente httpx de Storage ("Attempted to send an sync request with an
AsyncClient instance"). Aislarlo en un hilo evita esa interferencia.
"""
from __future__ import annotations

import asyncio

from ..config import settings

RENDER_CODE_SCHEMA = {
    "name": "render_document_code",
    "description": (
        "Genera un DOCX con FORMATO AVANZADO escribiendo código Python con la librería "
        "python-docx. Tu código DEBE construir el documento y guardarlo EXACTAMENTE en "
        "/tmp/out.docx. Úsalo para documentos formales largos (poderes, contratos, demandas, "
        "minutas) donde importan la numeración, los estilos y la maquetación. Para entregables "
        "simples usa render_letter/render_memo."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "code": {"type": "string", "description": "Python con python-docx que guarda en /tmp/out.docx"},
        },
        "required": ["title", "code"],
    },
}

_PREP = (
    "import subprocess, sys\n"
    "try:\n    import docx  # noqa\n"
    "except Exception:\n    subprocess.run([sys.executable,'-m','pip','install','-q','python-docx'])\n"
)


def _build_sync(code: str, api_key: str) -> tuple[bytes | None, str | None]:
    """Corre en un hilo (sync E2B Sandbox), aislado del event loop principal."""
    try:
        from e2b_code_interpreter import Sandbox
    except Exception as exc:  # noqa: BLE001
        return None, f"SDK E2B no disponible: {exc}"
    sbx = None
    try:
        sbx = Sandbox(api_key=api_key)
        ex = sbx.run_code(_PREP + (code or ""))
        if getattr(ex, "error", None):
            return None, f"{ex.error.name}: {ex.error.value}"
        data = sbx.files.read("/tmp/out.docx", format="bytes")
        if isinstance(data, str):
            data = data.encode("latin-1", "ignore")
        data = bytes(data) if data else b""
        if not data:
            return None, "el código no produjo /tmp/out.docx"
        return data, None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)
    finally:
        if sbx is not None:
            try:
                sbx.kill()
            except Exception:  # noqa: BLE001
                pass


async def build(code: str) -> tuple[bytes | None, str | None]:
    if not settings.e2b_api_key:
        return None, "E2B no configurado"
    return await asyncio.to_thread(_build_sync, code or "", settings.e2b_api_key)
