"""Loop del agente (Sprint 1.3) — Router + skills + tool-use (document tools).

Router Haiku elige {skill,tier} → carga SKILL.md → loop ReAct con tool-use:
el modelo puede llamar render_memo/render_letter/build_table_doc → se generan
DOCX, se suben a Storage, se crean artifacts y se emiten eventos `artifact`.
"""
from __future__ import annotations

from typing import AsyncGenerator

from .. import bridge, db
from ..auth import Principal
from ..config import settings
from ..tools.registry import TOOL_SCHEMAS, execute as exec_tool
from .llm import client, tier_to_model
from .router import route
from .system_prompts import WORKER_SYSTEM

MAX_ITERS = 5

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


def _assistant_content(blocks) -> list[dict]:
    out = []
    for b in blocks:
        t = getattr(b, "type", None)
        if t == "text":
            out.append({"type": "text", "text": b.text})
        elif t == "tool_use":
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
    return out


async def run_chat(session_id: str, principal: Principal, message: str) -> AsyncGenerator[str, None]:
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
    if skill_key and (body := await _skill_body(skill_key)):
        system_blocks.append({"type": "text", "text": SKILL_FRAMING + body, "cache_control": {"type": "ephemeral"}})

    yield bridge.sse(bridge.AGENT_STEP,
                     {"task_id": session_id, "agent": skill_key or "worker", "tier": tier, "status": "started"})

    ctx = {"org_id": org_id, "session_id": session_id, "matter_id": None, "user_id": principal.user_id}
    convo = list(history)
    full = ""
    artifacts: list[dict] = []
    usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}

    try:
        for _ in range(MAX_ITERS):
            turn_text = ""
            async with client().messages.stream(
                model=model, max_tokens=3072, system=system_blocks, tools=TOOL_SCHEMAS, messages=convo,
            ) as stream:
                async for text in stream.text_stream:
                    turn_text += text
                    yield bridge.sse(bridge.TEXT_DELTA, {"text": text, "message_id": session_id})
                final = await stream.get_final_message()
            full += turn_text
            u = final.usage
            usage["input"] += u.input_tokens
            usage["output"] += u.output_tokens
            usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
            usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0

            tool_uses = [b for b in final.content if getattr(b, "type", None) == "tool_use"]
            if final.stop_reason != "tool_use" or not tool_uses:
                break

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
        yield bridge.sse(bridge.ERROR, {"message": str(exc), "subtype": "anthropic"})

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
