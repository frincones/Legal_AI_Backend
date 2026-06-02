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
    "TabStopPosition } = require('docx');`\n"
    "2. Construye, SIEMPRE AL NIVEL SUPERIOR DEL MÓDULO (NUNCA dentro de una función ni de un async main), "
    "una variable llamada EXACTAMENTE `doc` con `const doc = new Document({...})`:\n"
    "`const doc = new Document({ sections: [{ properties: { page: { size: { width: 12240, height: 15840 }, "
    "margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } }, children: [...] }] });`\n"
    "   CRÍTICO: docx-js usa A4 por defecto — fija SIEMPRE US Letter (width 12240, height 15840).\n"
    "3. Fuente Arial, tamaño 24 (12pt). Títulos en negrita.\n"
    "4. Campos a completar = placeholders en NEGRITA entre corchetes: `new TextRun({ text: '[NOMBRE]', bold: true })`.\n"
    "5. NO serialices ni guardes el documento tú mismo: NO llames a `Packer`, NO escribas archivos, NO uses "
    "`Packer.toBuffer` ni `writeFileSync`. El sistema serializa automáticamente la variable `doc`. Tu código "
    "debe simplemente declarar `const doc = ...` al nivel superior y terminar ahí.\n"
    "API — errores comunes a EVITAR:\n"
    "- `PageNumber` NO es constructor. Número de página: `new TextRun({ children: [PageNumber.CURRENT] })` "
    "y total: `[PageNumber.TOTAL_PAGES]`.\n"
    "- `AlignmentType`, `HeadingLevel`, `BorderStyle`, `WidthType`, `LevelFormat` son ENUMS — úsalos como "
    "`AlignmentType.CENTER`, nunca con `new`.\n"
    "- Pie de página: `footers: { default: new Footer({ children: [ new Paragraph({...}) ] }) }` dentro de la section.\n"
    "- Negrita/itálica/fuente van en `new TextRun({ text, bold:true, italics:true, font:'Arial', size:24 })`.\n"
    "- Listas numeradas: define `numbering` en el Document y usa `numbering: { reference, level }` en el Paragraph."
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
        # template 'legal-docx' tiene docx global pre-instalado (rápido). Fallback: base + npm install.
        used = "legal-docx"
        try:
            sbx = await AsyncSandbox.create(api_key=api_key, template="legal-docx")
        except Exception as _e:  # noqa: BLE001
            used = f"base (template err: {_e})"
            sbx = await AsyncSandbox.create(api_key=api_key)
        try:
            # El sistema serializa `doc` (no el modelo): anexamos un serializador robusto al final del
            # gen.js. Al estar en el MISMO archivo/scope ve el `const doc` top-level; re-requiere Packer/fs
            # por su cuenta y SIEMPRE registra SAVED_OK o WRAP_ERR con stack → diagnóstico siempre informativo.
            serializer = (
                "\n;(async () => {\n"
                "  try {\n"
                "    const { Packer } = require('docx');\n"
                "    const fs = require('fs');\n"
                "    if (typeof doc === 'undefined') { console.error('WRAP_ERR: no existe la variable top-level `doc`'); return; }\n"
                "    const b = await Packer.toBuffer(doc);\n"
                "    fs.writeFileSync('/home/user/out.docx', b);\n"
                "    console.log('SAVED_OK', b.length);\n"
                "  } catch (e) { console.error('WRAP_ERR:', (e && e.stack) || String(e)); }\n"
                "})();\n"
            )
            await sbx.files.write("/home/user/gen.js", (js_code or "") + serializer)
            runner = (
                "import subprocess, glob, os\n"
                "opt = os.path.isdir('/opt/node_libs/node_modules/docx')\n"
                "sz = os.path.getsize('/home/user/gen.js') if os.path.exists('/home/user/gen.js') else -1\n"
                "subprocess.run('cd /home/user && ([ -d /opt/node_libs/node_modules/docx ] || npm install docx >/dev/null 2>&1)', shell=True)\n"
                "r = subprocess.run('cd /home/user && NODE_PATH=/opt/node_libs/node_modules:/home/user/node_modules node gen.js', shell=True, capture_output=True, text=True)\n"
                "f = '/home/user/out.docx' if os.path.exists('/home/user/out.docx') else ''\n"
                "if not f:\n"
                "    c = sorted(glob.glob('/home/user/*.docx') + glob.glob('/tmp/*.docx'), key=os.path.getmtime)\n"
                "    f = c[-1] if c else ''\n"
                "print('__FOUND__' + f + '__OUT__[opt=' + str(opt) + ' rc=' + str(r.returncode) + ' js=' + str(sz) + '] ' + (r.stdout or '')[:200] + ' || ' + (r.stderr or '')[:600] + '__END__')\n"
            )
            ex = await sbx.run_code(runner)
            stdout = "".join(ex.logs.stdout) if (ex.logs and ex.logs.stdout) else ""
            found, diag = "", ""
            if "__FOUND__" in stdout:
                seg = stdout.split("__FOUND__", 1)[1].split("__END__", 1)[0]
                found = seg.split("__OUT__", 1)[0].strip()
                diag = seg.split("__OUT__", 1)[1] if "__OUT__" in seg else ""
            data = b""
            if found:
                try:
                    data = bytes(await sbx.files.read(found, format="bytes")) if found else b""
                except Exception:  # noqa: BLE001
                    data = b""
            if not data:
                return None, f"[tmpl={used}] el script no generó un .docx. Diagnóstico: {diag[:600] or 'sin salida'}"
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
