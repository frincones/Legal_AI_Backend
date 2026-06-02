"""Document-by-code (Sprint F) — el modelo escribe python-docx, se corre en E2B.

Da formato AVANZADO (numeración, estilos, tablas) controlado por el modelo. El
código debe guardar en /tmp/out.docx.

AISLAMIENTO: el AsyncSandbox de E2B funciona, pero si corre en el event loop del
request, al cerrarse rompe el cliente httpx de Storage ("Attempted to send an sync
request with an AsyncClient instance"). Lo corremos en un HILO con su PROPIO event
loop (`asyncio.run` dentro de `asyncio.to_thread`), totalmente aislado.
"""
from __future__ import annotations

import asyncio

from ..config import settings

RENDER_CODE_SCHEMA = {
    "name": "render_document_code",
    "description": (
        "Genera un DOCX con FORMATO AVANZADO escribiendo código Python con la librería "
        "python-docx. Tu código debe construir el documento en una variable llamada `doc` "
        "(`from docx import Document; doc = Document(); ...`). NO necesitas guardarlo: el sistema "
        "lo guarda automáticamente. Úsalo para documentos formales (poderes, contratos, demandas, "
        "minutas) donde importan numeración, estilos y maquetación. Para entregables simples usa "
        "render_letter/render_memo."
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
# Red de seguridad: escanea TODAS las variables y guarda cualquier Document de python-docx
# en la ruta exacta — sin depender del nombre de la variable ni de que el modelo lo guarde.
_POST = (
    "\ntry:\n"
    "    import docx.document as _dxd\n"
    "    _saved = False\n"
    "    for _v in list(globals().values()):\n"
    "        if isinstance(_v, _dxd.Document):\n"
    "            _v.save('/tmp/out.docx'); _saved = True; break\n"
    "    print('AUTOSAVE_OK' if _saved else 'AUTOSAVE_NODOC')\n"
    "except Exception as _e:\n"
    "    print('AUTOSAVE_ERR', _e)\n"
)


def _build_blocking(code: str, api_key: str) -> tuple[bytes | None, str | None]:
    """Corre en un hilo con su propio event loop (aislado del request)."""
    async def _go():
        from e2b_code_interpreter import AsyncSandbox
        sbx = await AsyncSandbox.create(api_key=api_key)
        try:
            ex = await sbx.run_code(_PREP + (code or "") + _POST)
            if getattr(ex, "error", None):
                return None, f"{ex.error.name}: {ex.error.value}"
            data = await sbx.files.read("/tmp/out.docx", format="bytes")
            if isinstance(data, str):
                data = data.encode("latin-1", "ignore")
            data = bytes(data) if data else b""
            return (data, None) if data else (None, "el código no produjo /tmp/out.docx")
        finally:
            try:
                await sbx.kill()
            except Exception:  # noqa: BLE001
                pass

    try:
        return asyncio.run(_go())
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


async def build(code: str) -> tuple[bytes | None, str | None]:
    if not settings.e2b_api_key:
        return None, "E2B no configurado"
    return await asyncio.to_thread(_build_blocking, code or "", settings.e2b_api_key)
