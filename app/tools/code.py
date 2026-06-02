"""Code interpreter (Sprint 2.4 · E2B) — ejecución aislada de Python.

Gateado: el modelo lo usa solo cuando necesita CÁLCULO o manipulación de datos
(plazos, fechas, tablas, parsing). microVM Firecracker efímera, se destruye al terminar.

Usa el Sandbox SÍNCRONO en `asyncio.to_thread` (no AsyncSandbox) para no interferir
con el cliente httpx de Storage en el mismo request — ver app/tools/codedoc.py.
"""
from __future__ import annotations

import asyncio

from ..config import settings

RUN_CODE_SCHEMA = {
    "name": "run_code",
    "description": "Ejecuta código Python en un sandbox aislado y devuelve la salida. Úsalo para CÁLCULOS exactos (plazos, fechas, intereses), parsing o generación de datos —no para razonamiento legal.",
    "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
}


def _run_sync(code: str, api_key: str) -> str:
    try:
        from e2b_code_interpreter import Sandbox
    except Exception as exc:  # noqa: BLE001
        return f"[run_code: SDK E2B no disponible: {exc}]"
    sbx = None
    try:
        sbx = Sandbox(api_key=api_key)
        ex = sbx.run_code(code)
        out = "".join(ex.logs.stdout) if ex.logs and ex.logs.stdout else ""
        err = "".join(ex.logs.stderr) if ex.logs and ex.logs.stderr else ""
        res = str(ex.text) if getattr(ex, "text", None) else ""
        if getattr(ex, "error", None):
            err += f"\n{ex.error.name}: {ex.error.value}"
        parts = []
        if out:
            parts.append(f"stdout:\n{out}")
        if res:
            parts.append(f"result:\n{res}")
        if err.strip():
            parts.append(f"stderr:\n{err}")
        return ("\n".join(parts) or "[sin salida]")[:4000]
    except Exception as exc:  # noqa: BLE001
        return f"[run_code error: {exc}]"
    finally:
        if sbx is not None:
            try:
                sbx.kill()
            except Exception:  # noqa: BLE001
                pass


async def run_code(code: str) -> str:
    if not settings.e2b_api_key:
        return "[run_code: E2B no configurado]"
    return await asyncio.to_thread(_run_sync, code, settings.e2b_api_key)
