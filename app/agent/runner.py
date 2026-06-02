"""Loop del agente (Sprint 1.3) — Router + skills + tool-use (document tools).

Router Haiku elige {skill,tier} → carga SKILL.md → loop ReAct con tool-use:
el modelo puede llamar render_memo/render_letter/build_table_doc → se generan
DOCX, se suben a Storage, se crean artifacts y se emiten eventos `artifact`.
"""
from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from .. import bridge, db
from ..auth import Principal
from ..config import settings
from ..tools.registry import TOOL_SCHEMAS, execute as exec_tool
from . import guardrails
from .llm import client, is_transient, tier_to_model
from .router import route
from .system_prompts import WORKER_SYSTEM

MAX_ITERS = 5
MAX_STREAM_RETRIES = 4  # reintentos ante errores transitorios (503/429/timeout)

SKILL_FRAMING = (
    "[CONTEXTO DE EJECUCIÓN — APP WEB MULTI-TENANT]\n"
    "Vas a ejecutar el siguiente skill de claude-for-legal. Las rutas tipo `~/.claude/...`, "
    "los 'matter workspaces' y los archivos de perfil que mencione los gestiona la APLICACIÓN, "
    "no el filesystem. Si el skill pide leer/escribir un archivo de config o perfil: asume que el "
    "practice profile aún no está configurado (usa defaults razonables y dilo explícitamente), y "
    "entrega los outputs vía las herramientas de documento o en tu respuesta. Aplica SIEMPRE la "
    "lógica y los guardrails del skill (citation hygiene, work-product header, destination check).\n\n"
    "=== SKILL ===\n"
)


async def _candidates(org_id: str) -> list[dict]:
    rows = await db.select(
        "org_skills", f"select=skills(key,name,description)&org_id=eq.{org_id}&enabled=eq.true")
    return [r["skills"] for r in rows if r.get("skills")]


async def _skill_body(skill_key: str) -> str | None:
    rows = await db.select("skills", f"select=body_md&key=eq.{skill_key}&limit=1")
    return rows[0].get("body_md") if rows else None


async def _profile_block(org_id: str) -> str | None:
    try:
        rows = await db.select("company_profiles", f"org_id=eq.{org_id}&select=body_md,primary_jurisdiction&limit=1")
    except Exception:  # noqa: BLE001
        return None
    if not rows:
        return None
    juris = rows[0].get("primary_jurisdiction") or "no especificada"
    body = rows[0].get("body_md") or ""
    return (f"[PERFIL DE LA FIRMA — jurisdicción principal: {juris}]\n{body}\n"
            "Si el usuario no especifica jurisdicción, usa la del perfil; NO la infieras de resultados web.")


async def _attachment_context(doc_ids: list[str], org_id: str) -> str | None:
    snippets = []
    for did in (doc_ids or [])[:5]:
        rows = await db.select(
            "chunks", f"org_id=eq.{org_id}&document_id=eq.{did}&select=content,idx&order=idx.asc&limit=12")
        if not rows:
            continue
        title_rows = await db.select("documents", f"id=eq.{did}&select=title&limit=1")
        title = title_rows[0]["title"] if title_rows else did
        body = "\n".join(r["content"] for r in rows)[:8000]
        snippets.append(f"# Adjunto: {title}\n{body}")
    if not snippets:
        return None
    return guardrails.wrap_untrusted("\n\n".join(snippets), source="adjuntos")


async def _load_history(session_id: str) -> list[dict]:
    rows = await db.select(
        "messages", f"select=role,seq,message_parts(idx,type,text)&session_id=eq.{session_id}&order=seq.asc")
    msgs: list[dict] = []
    for r in rows:
        if r["role"] not in ("user", "assistant"):
            continue
        parts = sorted(r.get("message_parts") or [], key=lambda p: p["idx"])
        text = "".join((p.get("text") or "") for p in parts if p["type"] == "text")
        if text.strip():
            msgs.append({"role": r["role"], "content": text})
    return msgs


_DOC_INTENT = ("genero el documento", "generar el documento", "generaré", "procedo a generar",
               "ahora genero", "documento profesional", "voy a generar", "redacto el documento",
               "elaboro el documento", "procedo a redactar", "genero el poder", "genero la carta")


def _intends_document(text: str) -> bool:
    t = (text or "").lower()
    return any(p in t for p in _DOC_INTENT)


_DOC_VERBS = ("elabora", "redacta", "genera", "prepara", "escribe", "dame", "crea", "hazme",
              "haz un", "haz una", "necesito un", "necesito una", "quiero un", "quiero una",
              "arma un", "arma una", "redáctame", "redactame", "elabórame", "elaborame")
_DOC_NOUNS = ("poder", "memo", "memorando", "memorándum", "carta", "contrato", "acta", "minuta",
              "demanda", "cláusula", "clausula", "documento", "escrito", "dictamen", "oficio",
              "borrador", "machote", "tabla", "schedule", "redline", "convenio", "acuerdo")


def _wants_document(text: str) -> bool:
    """Detecta intención de ENTREGABLE en el mensaje del usuario."""
    t = (text or "").lower()
    return any(v in t for v in _DOC_VERBS) and any(n in t for n in _DOC_NOUNS)


def _assistant_content(blocks, include_thinking: bool = True) -> list[dict]:
    out = []
    for b in blocks:
        t = getattr(b, "type", None)
        if t == "thinking" and include_thinking:
            out.append({"type": "thinking", "thinking": b.thinking, "signature": b.signature})
        elif t == "redacted_thinking" and include_thinking:
            out.append({"type": "redacted_thinking", "data": b.data})
        elif t == "text":
            out.append({"type": "text", "text": b.text})
        elif t == "tool_use":
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
    return out


async def run_chat(session_id: str, principal: Principal, message: str,
                   document_ids: list[str] | None = None) -> AsyncGenerator[str, None]:
    org_id = principal.org_id or await db.resolve_org(principal.user_id)
    persist = bool(org_id and settings.supabase_service_role_key)

    decision = {"skill": None, "tier": "sonnet"}
    if org_id:
        try:
            decision = await route(message, await _candidates(org_id))
        except Exception:  # noqa: BLE001
            pass
    skill_key, tier = decision.get("skill"), decision.get("tier", "sonnet")
    model = tier_to_model(tier)

    run_id = assistant_msg_id = None
    history: list[dict] = []
    if persist:
        try:
            await db.upsert("chat_sessions",
                {"id": session_id, "org_id": org_id, "user_id": principal.user_id,
                 "title": message[:60], "model_tier": tier}, on_conflict="id")
            seq = await db.next_seq(session_id)
            umsg = await db.insert("messages",
                {"org_id": org_id, "session_id": session_id, "seq": seq, "role": "user", "status": "complete"},
                returning=True)
            await db.insert("message_parts",
                {"org_id": org_id, "message_id": umsg[0]["id"], "idx": 0, "type": "text", "text": message})
            run = await db.insert("agent_runs",
                {"org_id": org_id, "session_id": session_id, "agent_key": skill_key or "worker",
                 "model": model, "model_tier": tier, "status": "running"}, returning=True)
            run_id = run[0]["id"]
            amsg = await db.insert("messages",
                {"org_id": org_id, "session_id": session_id, "seq": seq + 1, "role": "assistant",
                 "status": "streaming", "model": model}, returning=True)
            assistant_msg_id = amsg[0]["id"]
            history = await _load_history(session_id)
        except Exception as exc:  # noqa: BLE001
            persist = False
            yield bridge.sse(bridge.ERROR, {"message": f"persist degradado: {exc}", "subtype": "db"})

    if not history:
        history = [{"role": "user", "content": message}]

    system_blocks = [{"type": "text", "text": WORKER_SYSTEM}]
    if org_id and (prof := await _profile_block(org_id)):
        system_blocks.append({"type": "text", "text": prof})
    if skill_key and (body := await _skill_body(skill_key)):
        system_blocks.append({"type": "text", "text": SKILL_FRAMING + body, "cache_control": {"type": "ephemeral"}})

    # ── Guardrails (pre) ──
    if org_id:
        notice = await guardrails.pre_checks(principal, org_id, skill_key, session_id=session_id, run_id=run_id)
        if notice:
            system_blocks.append({"type": "text", "text": "[GUARDRAIL] " + notice})

    yield bridge.sse(bridge.AGENT_STEP,
                     {"task_id": session_id, "agent": skill_key or "worker", "tier": tier, "status": "started"})

    ctx = {"org_id": org_id, "session_id": session_id, "matter_id": None, "user_id": principal.user_id}
    convo = list(history)
    # ── Adjuntos (Sprint 1.4): inyecta contenido del adjunto como DATA no confiable ──
    if document_ids and org_id and convo and convo[-1]["role"] == "user":
        actx = await _attachment_context(document_ids, org_id)
        if actx:
            convo[-1] = {"role": "user", "content": f"{convo[-1]['content']}\n\n{actx}"}
    full = ""
    artifacts: list[dict] = []
    nudged = False
    wants_doc = _wants_document(message)
    usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}

    try:
        for _ in range(MAX_ITERS):
            turn_text = ""
            emitted = False
            final = None
            kwargs = dict(model=model, max_tokens=4096, system=system_blocks, tools=TOOL_SCHEMAS, messages=convo)
            if settings.thinking_budget and tier in ("sonnet", "opus"):
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": settings.thinking_budget}
            for attempt in range(MAX_STREAM_RETRIES):
                try:
                    turn_text = ""
                    async with client().messages.stream(**kwargs) as stream:
                        async for ev in stream:
                            if getattr(ev, "type", None) != "content_block_delta":
                                continue
                            d = ev.delta
                            dt = getattr(d, "type", None)
                            if dt == "text_delta":
                                turn_text += d.text
                                emitted = True
                                yield bridge.sse(bridge.TEXT_DELTA, {"text": d.text, "message_id": session_id})
                            elif dt == "thinking_delta":
                                emitted = True
                                yield bridge.sse(bridge.THINKING, {"text": d.thinking, "message_id": session_id})
                        final = await stream.get_final_message()
                    break
                except Exception as exc:  # noqa: BLE001
                    # Reintenta solo si es transitorio Y aún no se emitió contenido en este turno.
                    if emitted or not is_transient(exc) or attempt == MAX_STREAM_RETRIES - 1:
                        raise
                    yield bridge.sse(bridge.AGENT_STEP,
                                     {"task_id": session_id, "agent": "worker", "status": "retry", "attempt": attempt + 1})
                    await asyncio.sleep(min(2 ** attempt, 8))
            if final is None:
                break
            full += turn_text
            u = final.usage
            usage["input"] += u.input_tokens
            usage["output"] += u.output_tokens
            usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
            usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0

            tool_uses = [b for b in final.content if getattr(b, "type", None) == "tool_use"]
            if not tool_uses:
                # Sin tool calls este turno → nudge una vez (texto sin thinking) o terminar.
                if not artifacts and not nudged and (wants_doc or _intends_document(turn_text)):
                    nudged = True
                    ac = _assistant_content(final.content, include_thinking=False) or [{"type": "text", "text": turn_text or "."}]
                    convo.append({"role": "assistant", "content": ac})
                    convo.append({"role": "user", "content":
                        "Procede AHORA: genera el documento llamando la herramienta "
                        "(render_document_code / render_letter / render_memo). Si faltan datos, usa "
                        "PLACEHOLDERS entre corchetes [ASÍ]. NO pidas más información; NO respondas solo con texto."})
                    continue
                break

            # Hay tool_use(s) → ejecutar SIEMPRE y devolver tool_results (sin depender de stop_reason).
            convo.append({"role": "assistant", "content": _assistant_content(final.content)})
            results = []
            for tu in tool_uses:
                yield bridge.sse(bridge.TOOL_CALL, {"id": tu.id, "name": tu.name, "input": tu.input})
                summary, artifact = await exec_tool(tu.name, tu.input, ctx)
                if artifact:
                    artifacts.append(artifact)
                    yield bridge.sse(bridge.ARTIFACT, artifact)
                yield bridge.sse(bridge.TOOL_RESULT, {"id": tu.id, "name": tu.name, "output": summary})
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": summary})
                if persist:
                    try:
                        await db.insert("tool_calls", {
                            "org_id": org_id, "run_id": run_id, "tool_name": tu.name,
                            "output_summary": summary[:500], "status": "ok"})
                    except Exception:  # noqa: BLE001
                        pass
            convo.append({"role": "user", "content": results})
    except Exception as exc:  # noqa: BLE001
        msg = ("El servicio de IA está temporalmente saturado. Intenta de nuevo en unos segundos."
               if is_transient(exc) else str(exc))
        yield bridge.sse(bridge.ERROR, {"message": msg, "subtype": "anthropic"})

    # ── Guardrails (post): citation linter ──
    if org_id and full:
        note = await guardrails.lint_citations(full, org_id, session_id=session_id, run_id=run_id)
        if note:
            yield bridge.sse(bridge.TEXT_DELTA, {"text": note, "message_id": session_id})
            full += note

    if persist and assistant_msg_id:
        try:
            await db.insert("message_parts",
                {"org_id": org_id, "message_id": assistant_msg_id, "idx": 0, "type": "text", "text": full})
            for i, a in enumerate(artifacts, start=1):
                await db.insert("message_parts", {
                    "org_id": org_id, "message_id": assistant_msg_id, "idx": i, "type": "artifact",
                    "text": a["title"], "artifact_version_id": a.get("version_id")})
            await db.patch("messages", f"id=eq.{assistant_msg_id}", {"status": "complete"})
            await db.patch("agent_runs", f"id=eq.{run_id}", {"status": "complete"})
            await db.insert("token_ledger", {
                "org_id": org_id, "run_id": run_id, "session_id": session_id, "user_id": principal.user_id,
                "model": model, "input_tokens": usage["input"], "output_tokens": usage["output"],
                "cache_read_tokens": usage["cache_read"], "cache_creation_tokens": usage["cache_write"]})
        except Exception:  # noqa: BLE001
            pass

    yield bridge.sse(bridge.USAGE, usage)
    yield bridge.sse(bridge.DONE, {"session_id": session_id, "result": "ok",
                                   "skill": skill_key, "artifacts": len(artifacts)})
