"""Dashboard de economía/observabilidad (Sprint 3.4) + watcher on-demand (3.1)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import db
from ..auth import Principal, get_principal

router = APIRouter()

# Precios aprox (USD por 1M tokens) — ajustar a pricing vigente.
PRICES = {
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-8": (15.0, 75.0),
}


@router.get("/api/usage")
async def usage(principal: Principal = Depends(get_principal)) -> dict:
    org_id = principal.org_id or await db.resolve_org(principal.user_id)
    if not org_id:
        return {"error": "sin organización"}
    ledger = await db.select(
        "token_ledger",
        f"org_id=eq.{org_id}&select=model,input_tokens,output_tokens,cache_read_tokens&limit=10000")
    by_model: dict[str, dict] = {}
    cost = 0.0
    cache_reads = 0
    for r in ledger:
        m = r["model"]
        inp = r.get("input_tokens", 0) or 0
        outp = r.get("output_tokens", 0) or 0
        d = by_model.setdefault(m, {"calls": 0, "input": 0, "output": 0})
        d["calls"] += 1
        d["input"] += inp
        d["output"] += outp
        cache_reads += r.get("cache_read_tokens", 0) or 0
        pin, pout = PRICES.get(m, (3.0, 15.0))
        cost += inp / 1e6 * pin + outp / 1e6 * pout
    counts = {}
    for t in ("artifacts", "documents", "tool_calls", "guardrail_events", "verifications", "chat_sessions"):
        try:
            rows = await db.select(t, f"org_id=eq.{org_id}&select=id")
            counts[t] = len(rows)
        except Exception:  # noqa: BLE001
            counts[t] = None
    return {
        "org_id": org_id,
        "tokens_by_model": by_model,
        "estimated_cost_usd": round(cost, 4),
        "cache_read_tokens": cache_reads,
        "counts": counts,
    }


@router.post("/api/watchers/deadlines")
async def deadline_watcher(principal: Principal = Depends(get_principal)) -> dict:
    """Watcher on-demand (3.1 · parcial): matters con next_deadline próximo."""
    org_id = principal.org_id or await db.resolve_org(principal.user_id)
    if not org_id:
        return {"error": "sin organización"}
    rows = await db.select(
        "matters",
        f"org_id=eq.{org_id}&status=eq.active&next_deadline=not.is.null&select=slug,name,next_deadline&order=next_deadline.asc&limit=50")
    return {"upcoming": rows, "count": len(rows)}
