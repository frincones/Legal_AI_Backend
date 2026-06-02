"""Code interpreter (Sprint 2.4 · E2B) — ejecución aislada de Python.

Gateado: el modelo lo usa solo cuando necesita CÁLCULO o manipulación de datos.
Corre el AsyncSandbox en un HILO con su propio event loop (aislado del request)
para no romper el cliente httpx de Storage — ver app/tools/codedoc.py.
"""
from __future__ import annotations

import asyncio

from ..config import settings

RUN_CODE_SCHEMA = {
    "name": "run_code",
    "description": "Ejecuta código Python en un sandbox aislado y devuelve la salida. Úsalo para CÁLCULOS exactos (plazos, fechas, intereses), parsing o generación de datos —no para razonamiento legal.",
    "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
}


def _run_blocking(code: str, api_key: str) -> str:
    async def _go():
        from e2b_code_interpreter import AsyncSandbox
        sbx = await AsyncSandbox.create(api_key=api_key)
        try:
            ex = await sbx.run_code(code)
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
        finally:
            try:
                await sbx.kill()
            except Exception:  # noqa: BLE001
                pass

    try:
        return asyncio.run(_go())
    except Exception as exc:  # noqa: BLE001
        return f"[run_code error: {exc}]"


async def run_code(code: str) -> str:
    if not settings.e2b_api_key:
        return "[run_code: E2B no configurado]"
    return await asyncio.to_thread(_run_blocking, code, settings.e2b_api_key)
