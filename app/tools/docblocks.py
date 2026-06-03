"""Deriva el modelo de bloques del Canvas a partir del DOCX ya generado.

DETERMINISTA y $0 LLM: parsea el DOCX (python-docx, ya es dependencia) en bloques
{type, text, num, cites[]} y matchea las citas contra los `vf_records` verificados de la
sesión. NO toca el agente, el docx-js, la generación ni el costo — solo lee el DOCX hecho.
El Canvas usa estos bloques para preview con chips [VERIFICADO], highlight-to-edit y redline.
"""
from __future__ import annotations

import io
import re

# Patrones de cita en el texto del documento
_RE_LEY = re.compile(r"\b(?:ley|decreto|resoluci[oó]n)\s*(?:n[°º.]?\s*)?(\d{1,4})\b", re.I)
_RE_ART = re.compile(r"\bart[íi]?culo?s?\.?\s*(\d{1,4})\b", re.I)
_RE_SENT = re.compile(r"\b(C|T|SU|A)\s*[-–]\s*(\d{1,4})\b", re.I)
# Numerador al inicio de un párrafo (1., PRIMERA., PRIMERO., a))
_RE_NUM = re.compile(
    r"^((?:PRIMER[AO]|SEGUND[AO]|TERCER[AO]|CUART[AO]|QUINT[AO]|SEXT[AO]|S[ÉE]PTIM[AO]|OCTAV[AO]|NOVEN[AO]|D[ÉE]CIM[AO])\.|"
    r"\d{1,3}[\.\)]|[a-z]\))\s+", re.I)


def _chip_status(estado: str | None) -> str:
    """vf estado → estado visual del chip (vigente=oro, exequible=verde, derogada=rojo, verificar=ámbar)."""
    if not estado:
        return "verificar"
    e = estado.lower()
    if e.startswith("exequible"):
        return "exequible"
    if e.startswith("vigente"):
        return "vigente"
    if e.startswith("derogada") or e.startswith("inexequible") or e == "suspendida":
        return "derogada"
    return "verificar"


def _citations_from_records(vf_records: list | None) -> tuple[dict, dict]:
    """Construye {id: CITATION} y un índice {numero/clave: id} para matchear por texto."""
    cits: dict = {}
    by_key: dict = {}
    for r in (vf_records or []):
        estado = r.get("estado") or r.get("efecto")
        if not estado or estado == "no_encontrada":
            continue
        proc = (r.get("procedencia") or [{}])[0]
        if r.get("tipo_fuente") == "jurisprudencia":
            num = r.get("numero")
            cid = f"sent-{r.get('clase')}-{num}-{r.get('anio')}"
            label = f"{r.get('clase')}-{num}/{r.get('anio')}"
            try:
                by_key[f"{r.get('clase')}-{int(num)}"] = cid
            except Exception:  # noqa: BLE001
                pass
        else:
            num = r.get("numero")
            art = r.get("articulo")
            cid = f"norma-{r.get('tipo')}-{num}-{r.get('anio')}" + (f"-art{art}" if art else "")
            label = ((f"Art. {art} " if art else "") + f"{(r.get('tipo') or '').capitalize()} {num}").strip()
            if art:
                by_key[str(art)] = cid           # el artículo suele aparecer en el texto
            by_key.setdefault(str(num), cid)     # y/o el número de la ley
        cits[cid] = {
            "label": label or "cita",
            "status": _chip_status(estado),
            "tier": proc.get("tier", 9),
            "title": r.get("consulta") or label or "",
            "source": proc.get("entidad") or "—",
            "consulted": proc.get("fecha_consulta") or "",
            "note": (r.get("soporte_textual") or estado or "")[:140],
            "url": proc.get("url") or "",
        }
    return cits, by_key


def _classify(text: str, style: str, bold: bool, seen_body: bool) -> str:
    low = text.lower()
    flat = low.replace(" ", "").replace(".", "")
    if not seen_body and ("juzgado" in low or low.startswith("señor") or "esd" == flat[:3] or "esd" in flat[:6]):
        return "court"
    if (low.startswith("ref") or low.startswith("referencia")) and len(text) < 140:
        return "ref"
    if "heading" in style.lower() or "title" in style.lower():
        return "h"
    if bold and len(text) < 95:
        return "h"
    return "p"


def to_blocks(docx_bytes: bytes, vf_records: list | None = None) -> tuple[list, dict]:
    """DOCX (bytes) → (blocks, citations). Robusto: si algo falla, devuelve lo que pudo."""
    cits, by_key = _citations_from_records(vf_records)
    try:
        from docx import Document  # python-docx (ya es dependencia)
        d = Document(io.BytesIO(docx_bytes))
    except Exception:  # noqa: BLE001
        return [], cits
    blocks: list = []
    seen_body = False
    for p in d.paragraphs:
        text = (p.text or "").strip()
        if not text:
            continue
        style = (getattr(p.style, "name", "") or "") if p.style else ""
        runs = [r for r in p.runs if (r.text or "").strip()]
        bold = bool(runs) and all(bool(r.bold) for r in runs)
        btype = _classify(text, style, bold, seen_body)
        if btype in ("h", "p"):
            seen_body = True
        block: dict = {"type": btype, "text": text}
        if btype == "p":
            m = _RE_NUM.match(text)
            if m:
                block["num"] = m.group(1).strip()
                block["text"] = text[m.end():].strip()
        # match de citas por número/artículo/sentencia contra lo verificado
        cides: list = []
        body = block["text"]
        for rx in (_RE_LEY, _RE_ART):
            for mm in rx.finditer(body):
                cid = by_key.get(mm.group(1))
                if cid and cid not in cides:
                    cides.append(cid)
        for mm in _RE_SENT.finditer(body):
            try:
                cid = by_key.get(f"{mm.group(1).upper()}-{int(mm.group(2))}")
            except Exception:  # noqa: BLE001
                cid = None
            if cid and cid not in cides:
                cides.append(cid)
        if cides:
            block["cites"] = cides
        blocks.append(block)
    return blocks, cits


def diff_changed(prev_blocks: list | None, new_blocks: list) -> list:
    """Marca `changed=True` en los bloques cuyo texto cambió o son nuevos (para el redline)."""
    prev_texts = {(b.get("num"), (b.get("text") or "").strip()) for b in (prev_blocks or [])}
    prev_bodies = {(b.get("text") or "").strip() for b in (prev_blocks or [])}
    out = []
    for b in new_blocks:
        body = (b.get("text") or "").strip()
        changed = body not in prev_bodies
        nb = dict(b)
        if changed and prev_blocks:
            nb["changed"] = True
        out.append(nb)
    return out
