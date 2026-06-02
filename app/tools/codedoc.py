"""Document-by-code (método de Claude) — el modelo escribe docx-js (Node) en E2B.

Replica el docx skill de Anthropic: el modelo genera código JavaScript con la
librería `docx` (docx-js), se ejecuta en el sandbox E2B (que tiene Node), y el
resultado se VALIDA antes de entregarse. El AsyncSandbox corre en un hilo con su
propio event loop para no interferir con el cliente httpx de Storage.
"""
from __future__ import annotations

import asyncio
import io
import zipfile

from ..config import settings

# Guía condensada del docx skill de Anthropic (patrones docx-js que hacen el código confiable).
_DOCX_GUIDE = (
    "Escribe código JavaScript con la librería docx-js (Node). Reglas:\n"
    "1. `const { Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, AlignmentType, "
    "HeadingLevel, BorderStyle, PageNumber, Footer, Header, LevelFormat, WidthType, TabStopType, "
    "TabStopPosition } = require('docx'); const fs = require('fs');`\n"
    "2. Construye `const doc = new Document({ sections: [{ properties: { page: { size: { width: 12240, "
    "height: 15840 }, margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } }, children: [...] }] });`\n"
    "   CRÍTICO: docx-js usa A4 por defecto — fija SIEMPRE US Letter (width 12240, height 15840) como arriba.\n"
    "3. Fuente Arial, tamaño 24 (12pt) en los TextRun. Títulos en negrita.\n"
    "4. Para campos a completar usa placeholders en NEGRITA entre corchetes, ej. "
    "`new TextRun({ text: '[NOMBRE DEL PODERDANTE]', bold: true })`.\n"
    "5. TERMINA SIEMPRE con: `Packer.toBuffer(doc).then(b => fs.writeFileSync('/home/user/out.docx', b));`\n"
    "6. NO envuelvas el código en funciones async sin invocarlas; el archivo DEBE quedar escrito en "
    "/home/user/out.docx al ejecutar `node`."
)

RENDER_CODE_SCHEMA = {
    "name": "render_document_code",
    "description": (
        "Genera un DOCX profesional con FORMATO AVANZADO (numeración, estilos, tablas, encabezados, "
        "pies de página) — el método del docx skill de Anthropic. Úsalo para documentos formales "
        "(poderes, contratos, demandas, minutas, escrituras). Para entregables simples usa "
        "render_letter/render_memo.\n\n" + _DOCX_GUIDE
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "code": {"type": "string", "description": "Código JavaScript docx-js que escribe /home/user/out.docx"},
        },
        "required": ["title", "code"],
    },
}


def _build_blocking(js_code: str, api_key: str) -> tuple[bytes | None, str | None]:
    """Hilo con event loop propio: AsyncSandbox E2B, ejecuta Node, devuelve bytes."""
    async def _go():
        from e2b_code_interpreter import AsyncSandbox
        # template 'legal-docx' tiene docx + python-docx pre-instalados (rápido). Fallback: base.
        try:
            sbx = await AsyncSandbox.create(api_key=api_key, template="legal-docx")
        except Exception:  # noqa: BLE001 — si el template no existe aún, usa la base
            sbx = await AsyncSandbox.create(api_key=api_key)
        try:
            await sbx.files.write("/home/user/gen.js", js_code)
            runner = (
                "import subprocess\n"
                # instala docx solo si no está (en el template ya está → instantáneo)
                "subprocess.run('cd /home/user && ([ -d node_modules/docx ] || npm install docx >/dev/null 2>&1)', shell=True)\n"
                "r = subprocess.run('cd /home/user && node gen.js', shell=True, capture_output=True, text=True)\n"
                "print('__STDERR__'); print((r.stderr or '')[:1200]); print('__END__')\n"
            )
            ex = await sbx.run_code(runner)
            stdout = "".join(ex.logs.stdout) if (ex.logs and ex.logs.stdout) else ""
            try:
                data = await sbx.files.read("/home/user/out.docx", format="bytes")
                data = bytes(data) if data else b""
            except Exception:  # noqa: BLE001
                data = b""
            if not data:
                err = ""
                if "__STDERR__" in stdout:
                    err = stdout.split("__STDERR__", 1)[1].split("__END__", 1)[0].strip()
                return None, f"el script no generó out.docx. Error de Node: {err[:700] or 'desconocido'}"
            return data, None
        finally:
            try:
                await sbx.kill()
            except Exception:  # noqa: BLE001
                pass

    try:
        return asyncio.run(_go())
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def _validate(data: bytes) -> str | None:
    """Validación ligera: el .docx es un zip con word/document.xml parseable."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        xml = z.read("word/document.xml")
        if b"<w:document" not in xml and b"<w:body" not in xml:
            return "word/document.xml sin contenido esperado"
        return None
    except Exception as exc:  # noqa: BLE001
        return f"docx inválido ({exc})"


async def build(code: str) -> tuple[bytes | None, str | None]:
    if not settings.e2b_api_key:
        return None, "E2B no configurado"
    data, err = await asyncio.to_thread(_build_blocking, code or "", settings.e2b_api_key)
    if err or not data:
        return None, err or "sin datos"
    vmsg = _validate(data)
    if vmsg:
        return None, f"el documento no pasó validación: {vmsg}. Revisa el código docx-js."
    return data, None
