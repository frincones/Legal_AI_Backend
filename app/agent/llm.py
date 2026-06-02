"""Cliente Anthropic compartido + mapeo de tier → modelo."""
from __future__ import annotations

from anthropic import AsyncAnthropic

from ..config import settings

_client: AsyncAnthropic | None = None


def client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


def tier_to_model(tier: str) -> str:
    return {
        "haiku": settings.model_router,
        "sonnet": settings.model_worker,
        "opus": settings.model_deep,
    }.get(tier, settings.model_worker)
