"""Export PDF on-demand (F5) — convierte un DOCX a PDF en un sandbox E2B AISLADO.

NO toca el flujo de render_document_code ni el template de generación: usa su propio sandbox,
sube el DOCX, corre LibreOffice headless (`soffice --convert-to pdf`) y devuelve los bytes PDF.
LibreOffice corre en E2B (no en Railway) → cero dependencias nuevas en el backend. Si el template
'legal-docx' ya trae soffice, es inmediato; si no, lo instala una vez en el sandbox efímero.
"""
from __future__ import annotations

import asyncio

from ..config import settings


def _convert_blocking(docx: bytes, api_key: str) -> tuple[bytes | None, str | None]:
    async def _go():
        from e2b_code_interpreter import AsyncSandbox
        try:
            sbx = await AsyncSandbox.create(api_key=api_key, template="legal-docx")
        except Exception:  # noqa: BLE001
            sbx = await AsyncSandbox.create(api_key=api_key)
        try:
            await sbx.files.write("/home/user/in.docx", docx)
            runner = (
                "import subprocess, os\n"
                # soffice presente? si no, instalar headless (no-recommends) una vez.
                "have = subprocess.run('command -v soffice || command -v libreoffice', shell=True, "
                "capture_output=True, text=True).stdout.strip()\n"
                "if not have:\n"
                "    subprocess.run('sudo apt-get update -qq && sudo apt-get install -y --no-install-recommends "
                "libreoffice-writer-nogui >/dev/null 2>&1 || sudo apt-get install -y --no-install-recommends "
                "libreoffice-core libreoffice-writer >/dev/null 2>&1', shell=True)\n"
                "    have = subprocess.run('command -v soffice || command -v libreoffice', shell=True, "
                "capture_output=True, text=True).stdout.strip()\n"
                "bin = have or 'soffice'\n"
                "r = subprocess.run(bin + ' --headless --convert-to pdf --outdir /home/user /home/user/in.docx', "
                "shell=True, capture_output=True, text=True)\n"
                "ok = os.path.exists('/home/user/in.pdf')\n"
                "print('__PDF__' + ('/home/user/in.pdf' if ok else '') + '__OUT__rc=' + str(r.returncode) + ' ' "
                "+ (r.stderr or '')[:400] + '__END__')\n"
            )
            ex = await sbx.run_code(runner)
            stdout = "".join(ex.logs.stdout) if (ex.logs and ex.logs.stdout) else ""
            path, diag = "", ""
            if "__PDF__" in stdout:
                seg = stdout.split("__PDF__", 1)[1].split("__END__", 1)[0]
                path = seg.split("__OUT__", 1)[0].strip()
                diag = seg.split("__OUT__", 1)[1] if "__OUT__" in seg else ""
            if not path:
                return None, f"no se generó PDF. {diag[:300] or 'sin salida'}"
            try:
                data = bytes(await sbx.files.read(path, format="bytes"))
            except Exception as exc:  # noqa: BLE001
                return None, f"no se pudo leer el PDF: {exc}"
            return (data or None), (None if data else "PDF vacío")
        finally:
            try:
                await sbx.kill()
            except Exception:  # noqa: BLE001
                pass

    try:
        return asyncio.run(_go())
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


async def docx_to_pdf(docx: bytes) -> tuple[bytes | None, str | None]:
    if not settings.e2b_api_key:
        return None, "E2B no configurado"
    if not docx:
        return None, "DOCX vacío"
    return await asyncio.to_thread(_convert_blocking, docx, settings.e2b_api_key)
