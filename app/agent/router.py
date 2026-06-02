"""Router Haiku (Sprint 1.2) — elige skill + tier.

Capa 2 del diseño (§12): pasada barata con Haiku que clasifica el turno contra
los skills habilitados de la org. El skill elegido se carga (body_md) al system
prompt del worker. Es optimización: si elige mal, el worker responde igual.
"""
from __future__ import annotations

from .llm import client
from ..config import settings

ROUTER_TOOL = {
    "name": "select_skill",
    "description": "Selecciona el skill legal más apropiado para el turno del usuario, o null si ninguno aplica (respuesta ad-hoc).",
    "input_schema": {
        "type": "object",
        "properties": {
            "skill": {"type": ["string", "null"], "description": "key exacta del skill del catálogo, o null"},
            "tier": {"type": "string", "enum": ["haiku", "sonnet", "opus"],
                     "description": "haiku=trivial/lookup · sonnet=la mayoría · opus=razonamiento legal complejo"},
            "reasoning": {"type": "string"},
        },
        "required": ["skill", "tier"],
    },
}

_SYS = (
    "Eres un router de un asistente legal. NO ejecutes la tarea. "
    "Elige del catálogo el skill cuya descripción mejor calza con el mensaje del usuario, "
    "o devuelve skill=null si ninguno aplica claramente. "
    "tier: haiku para trivial/saludo, sonnet por defecto, opus solo para razonamiento legal complejo."
)


async def route(message: str, candidates: list[dict]) -> dict:
    if not candidates:
        return {"skill": None, "tier": "sonnet"}
    catalog = "\n".join(f"- {c['key']}: {(c.get('description') or '')[:220]}" for c in candidates)
    keys = {c["key"] for c in candidates}
    try:
        resp = await client().messages.create(
            model=settings.model_router,
            max_tokens=300,
            system=_SYS,
            tools=[ROUTER_TOOL],
            tool_choice={"type": "tool", "name": "select_skill"},
            messages=[{"role": "user", "content": f"Catálogo:\n{catalog}\n\nMensaje:\n{message}"}],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "select_skill":
                out = dict(block.input)
                if out.get("skill") not in keys:
                    out["skill"] = None
                if out.get("tier") not in ("haiku", "sonnet", "opus"):
                    out["tier"] = "sonnet"
                return out
    except Exception:  # noqa: BLE001 — degradar a ad-hoc/sonnet
        pass
    return {"skill": None, "tier": "sonnet"}
