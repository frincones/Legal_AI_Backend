"""Cliente Anthropic compartido + mapeo de tier → modelo."""
from __future__ import annotations

import anthropic
from anthropic import AsyncAnthropic

from ..config import settings

_client: AsyncAnthropic | None = None

# Estados HTTP transitorios que vale la pena reintentar.
_RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}


def client() -> AsyncAnthropic:
    global _client
    if _client is None:
        # max_retries: el SDK reintenta solo con backoff los errores transitorios al iniciar.
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=4)
    return _client


def is_transient(exc: Exception) -> bool:
    """True si el error de Anthropic es transitorio (503 overloaded, 429, timeouts…)."""
    if isinstance(exc, (anthropic.APITimeoutError, anthropic.APIConnectionError,
                        anthropic.InternalServerError, anthropic.RateLimitError)):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return getattr(exc, "status_code", None) in _RETRY_STATUS
    return False


def tier_to_model(tier: str) -> str:
    return {
        "haiku": settings.model_router,
        "sonnet": settings.model_worker,
        "opus": settings.model_deep,
    }.get(tier, settings.model_worker)
