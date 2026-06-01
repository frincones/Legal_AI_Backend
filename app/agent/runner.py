"""Loop del agente (Sprint 1.1) — streaming real con Claude + bridge SSE + persistencia.

Usa el SDK de Anthropic directo (el ReAct loop con tools/plugins del Agent SDK
entra en sprints posteriores). Emite el contrato de eventos del bridge y persiste
messages / message_parts / agent_runs / token_ledger con scoping por org_id.
"""
from __future__ import annotations

from typing import AsyncGenerator

from anthropic import AsyncAnthropic

from .. import bridge, db
from ..auth import Principal
from ..config import settings
from .system_prompts import WORKER_SYSTEM

_client: AsyncAnthropic | None = None


def client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


async def _load_history(session_id: str) -> list[dict]:
    rows = await db.select(
        "messages",
        f"select=role,seq,message_parts(idx,type,text)&session_id=eq.{session_id}&order=seq.asc",
    )
    msgs: list[dict] = []
    for r in rows:
        if r["role"] not in ("user", "assistant"):
            continue
        parts = sorted(r.get("message_parts") or [], key=lambda p: p["idx"])
        text = "".join((p.get("text") or "") for p in parts if p["type"] == "text")
        if text.strip():
            msgs.append({"role": r["role"], "content": text})
    return msgs


async def run_chat(session_id: str, principal: Principal, message: str) -> AsyncGenerator[str, None]:
    org_id = principal.org_id or await db.resolve_org(principal.user_id)
    persist = bool(org_id and settings.supabase_service_role_key)

    run_id = None
    assistant_msg_id = None
    history: list[dict] = []

    if persist:
        try:
            await db.upsert(
                "chat_sessions",
                {"id": session_id, "org_id": org_id, "user_id": principal.user_id, "title": message[:60]},
                on_conflict="id",
            )
            seq = await db.next_seq(session_id)
            umsg = await db.insert(
                "messages",
                {"org_id": org_id, "session_id": session_id, "seq": seq, "role": "user", "status": "complete"},
                returning=True,
            )
            await db.insert(
                "message_parts",
                {"org_id": org_id, "message_id": umsg[0]["id"], "idx": 0, "type": "text", "text": message},
            )
            run = await db.insert(
                "agent_runs",
                {"org_id": org_id, "session_id": session_id, "agent_key": "worker",
                 "model": settings.model_worker, "model_tier": "sonnet", "status": "running"},
                returning=True,
            )
            run_id = run[0]["id"]
            amsg = await db.insert(
                "messages",
                {"org_id": org_id, "session_id": session_id, "seq": seq + 1, "role": "assistant",
                 "status": "streaming", "model": settings.model_worker},
                returning=True,
            )
            assistant_msg_id = amsg[0]["id"]
            history = await _load_history(session_id)
        except Exception as exc:  # noqa: BLE001 — degradar a stream sin persistencia
            persist = False
            yield bridge.sse(bridge.ERROR, {"message": f"persist degradado: {exc}", "subtype": "db"})

    if not history:
        history = [{"role": "user", "content": message}]

    yield bridge.sse(bridge.AGENT_STEP, {"task_id": session_id, "agent": "worker", "status": "started"})

    full = ""
    usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    try:
        async with client().messages.stream(
            model=settings.model_worker,
            max_tokens=2048,
            system=WORKER_SYSTEM,
            messages=history,
        ) as stream:
            async for text in stream.text_stream:
                full += text
                yield bridge.sse(bridge.TEXT_DELTA, {"text": text, "message_id": session_id})
            final = await stream.get_final_message()
            u = final.usage
            usage = {
                "input": u.input_tokens,
                "output": u.output_tokens,
                "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
                "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
            }
    except Exception as exc:  # noqa: BLE001
        yield bridge.sse(bridge.ERROR, {"message": str(exc), "subtype": "anthropic"})

    if persist and assistant_msg_id:
        try:
            await db.insert(
                "message_parts",
                {"org_id": org_id, "message_id": assistant_msg_id, "idx": 0, "type": "text", "text": full},
            )
            await db.patch("messages", f"id=eq.{assistant_msg_id}", {"status": "complete"})
            await db.patch("agent_runs", f"id=eq.{run_id}", {"status": "complete"})
            await db.insert("token_ledger", {
                "org_id": org_id, "run_id": run_id, "session_id": session_id, "user_id": principal.user_id,
                "model": settings.model_worker, "input_tokens": usage["input"], "output_tokens": usage["output"],
                "cache_read_tokens": usage["cache_read"], "cache_creation_tokens": usage["cache_write"],
            })
        except Exception:  # noqa: BLE001
            pass

    yield bridge.sse(bridge.USAGE, usage)
    yield bridge.sse(bridge.DONE, {"session_id": session_id, "result": "ok"})
