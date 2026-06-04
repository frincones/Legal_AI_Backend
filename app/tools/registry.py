"""Registro de tools del agente: schemas para Anthropic + dispatcher.

Document tools (T5, plantilla) + render_document_code (E2B) + RAG + web + run_code.
"""
from __future__ import annotations

import uuid

from .. import db
from . import code, codedoc, docblocks, documents, patrones, rag, storage, verificar_fuente, web

# ── Schemas (tool-use de Anthropic) ──
TOOL_SCHEMAS = [
    {
        "name": "render_memo",
        "description": "Genera un MEMO legal profesional en DOCX (con header de work-product). Úsalo cuando el usuario pida un memo, análisis escrito o dictamen entregable.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "sections": {
                    "type": "array",
                    "items": {"type": "object", "properties": {
                        "heading": {"type": "string"}, "body": {"type": "string"}},
                        "required": ["body"]},
                },
            },
            "required": ["title", "sections"],
        },
    },
    {
        "name": "render_letter",
        "description": "Genera una CARTA formal en DOCX (demanda, C&D, respuesta, carta al cliente). Úsalo cuando el usuario pida una carta entregable simple.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "asunto/título interno"},
                "recipient": {"type": "string"},
                "body": {"type": "string"},
                "sender": {"type": "string"},
                "date": {"type": "string"},
            },
            "required": ["title", "recipient", "body"],
        },
    },
    {
        "name": "build_table_doc",
        "description": "Genera una TABLA/schedule/grid en DOCX (claim chart, disclosure schedule, diligence grid). Úsalo cuando el entregable sea tabular.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "columns": {"type": "array", "items": {"type": "string"}},
                "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
            },
            "required": ["title", "columns", "rows"],
        },
    },
]
TOOL_SCHEMAS.extend([codedoc.RENDER_CODE_SCHEMA, verificar_fuente.VERIFICAR_FUENTE_SCHEMA,
                     rag.SEARCH_SCHEMA, web.WEB_SEARCH_SCHEMA, web.WEB_FETCH_SCHEMA, code.RUN_CODE_SCHEMA])
# Sprint 2.5 · prompt caching: cachea TODAS las defs de tools marcando la última.
TOOL_SCHEMAS[-1]["cache_control"] = {"type": "ephemeral"}

KINDS = {"render_memo": "memo", "render_letter": "letter", "build_table_doc": "table",
         "render_document_code": "document"}


async def _store(ctx: dict, title: str, kind: str, data: bytes, md: str) -> tuple[str, dict | None]:
    org_id = ctx["org_id"]
    # Edición (F3): si el turno edita un documento existente, versionamos ese artifact (N+1).
    edit_target = ctx.get("edit_target") if kind == "document" else None

    # Modelo de bloques + citas (F2) — determinista, $0 LLM, a partir del DOCX ya hecho.
    blocks: list = []
    citations: dict = {}
    if kind == "document":
        try:
            blocks, citations = docblocks.to_blocks(data, ctx.get("vf_records"))
            if edit_target:
                blocks = docblocks.diff_changed(edit_target.get("prev_blocks"), blocks)
        except Exception:  # noqa: BLE001
            blocks, citations = [], {}

    if edit_target and edit_target.get("artifact_id"):
        artifact_id = edit_target["artifact_id"]
        version = int(edit_target.get("base_version") or 1) + 1
    else:
        artifact_id = str(uuid.uuid4())
        version = 1
    path = f"org/{org_id}/artifacts/{artifact_id}/v{version}.docx"
    try:
        await storage.ensure_bucket()
        await storage.upload(path, data)
        url = await storage.signed_url(path)
    except Exception as exc:  # noqa: BLE001
        return (f"documento generado pero falló el guardado: {exc}", None)

    version_id = None
    try:
        if version == 1:
            await db.insert("artifacts", {
                "id": artifact_id, "org_id": org_id, "session_id": ctx.get("session_id"),
                "matter_id": ctx.get("matter_id"), "title": title, "kind": kind,
                "created_by": ctx.get("user_id")})
        ver = await db.insert("artifact_versions", {
            "org_id": org_id, "artifact_id": artifact_id, "version": version,
            "content": md, "storage_path": path, "authored_by": "agent",
            "diff_from_version": (version - 1) if version > 1 else None,
            "blocks": blocks or None}, returning=True)
        version_id = ver[0]["id"] if ver else None
        await db.patch("artifacts", f"id=eq.{artifact_id}", {"current_version_id": version_id})
    except Exception:  # noqa: BLE001
        pass

    artifact = {"id": artifact_id, "kind": kind, "title": title, "version": version,
                "uri": url, "version_id": version_id}
    if kind == "document":
        artifact["blocks"] = blocks
        artifact["citations"] = citations
        # F4 · flywheel: el docx-js validado se archiva como patrón reutilizable (solo creación,
        # no en cada edición). Aditivo y defensivo: si falla, no afecta la generación.
        if version == 1 and md:
            await patrones.save(ctx, artifact_id, title, kind, md,
                                params={"materia": ctx.get("materia") or ""})
    verb = f"actualizado a v{version}" if version > 1 else "generado y guardado"
    return (f"Documento '{title}' {verb} (artifact {artifact_id}, v{version}). Disponible para descarga.", artifact)


async def execute(name: str, args: dict, ctx: dict) -> tuple[str, dict | None]:
    """Devuelve (summary_para_el_modelo, artifact_dict_para_el_bridge)."""
    # F6.5 — Tools de integraciones (Composio): se despachan al broker con el connected account
    # del usuario actual. Solo activo si el usuario habilitó integraciones este turno (aditivo).
    if name in ctx.get("composio_tools", ()):  # set vacío si no hay integraciones → cero overhead
        from . import composio
        summary = await composio.execute(name, ctx.get("composio_user_id") or ctx.get("user_id"), args)
        return (summary, None)
    if name == "search_documents":
        return (await rag.search(args.get("query", ""), ctx["org_id"], ctx.get("matter_id")), None)
    if name == "web_search":
        return (await web.web_search(args.get("query", "")), None)
    if name == "web_fetch":
        return (await web.web_fetch(args.get("url", ""), ctx.get("org_id")), None)
    if name == "verificar_fuente":
        summary, records = await verificar_fuente.verificar(args.get("consultas", []), ctx)
        ctx.setdefault("vf_records", []).extend(records)  # para el citation linter (F4)
        return (summary, None)
    if name == "run_code":
        return (await code.run_code(args.get("code", "")), None)
    if name == "render_document_code":
        data, err = await codedoc.build(args.get("code", ""))
        if err or not data:
            return (f"error generando documento por código: {err}", None)
        # Guardamos el docx-js COMPLETO (antes truncado a 2000) → habilita edición/versionado y
        # la biblioteca de patrones reutilizables. El campo content es text (sin límite práctico).
        return await _store(ctx, args.get("title", "documento"), "document", data, args.get("code", ""))

    gen = documents.GENERATORS.get(name)
    if not gen:
        return (f"tool desconocida: {name}", None)
    try:
        data, md = gen(**args)
    except Exception as exc:  # noqa: BLE001
        return (f"error generando documento: {exc}", None)
    return await _store(ctx, args.get("title", "documento"), KINDS.get(name, "document"), data, md)
