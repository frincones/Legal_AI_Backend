"""Document-by-code (Sprint F) — el modelo escribe python-docx, se corre en E2B.

Da formato AVANZADO (numeración, estilos, tablas) controlado por el modelo, a
diferencia de las plantillas fijas. El código debe guardar en /tmp/out.docx.
"""
from __future__ import annotations

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


async def build(code: str) -> tuple[bytes | None, str | None]:
    if not settings.e2b_api_key:
        return None, "E2B no configurado"
    try:
        from e2b_code_interpreter import AsyncSandbox
    except Exception as exc:  # noqa: BLE001
        return None, f"SDK E2B no disponible: {exc}"
    sbx = None
    try:
        sbx = await AsyncSandbox.create(api_key=settings.e2b_api_key)
        ex = await sbx.run_code(_PREP + (code or ""))
        if getattr(ex, "error", None):
            return None, f"{ex.error.name}: {ex.error.value}"
        data = await sbx.files.read("/tmp/out.docx", format="bytes")
        if isinstance(data, str):
            data = data.encode("latin-1", "ignore")
        if not data:
            return None, "el código no produjo /tmp/out.docx"
        return data, None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)
    finally:
        if sbx is not None:
            try:
                await sbx.kill()
            except Exception:  # noqa: BLE001
                pass
