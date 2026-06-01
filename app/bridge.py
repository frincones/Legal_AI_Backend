"""Contrato de streaming (bridge) — eventos SSE normalizados.

La UI (assistant-ui AssistantTransport) nunca ve la forma cruda del Agent SDK.
En Fase 1 el loop real (ClaudeSDKClient) emite estos mismos eventos.
"""
from __future__ import annotations

import json
from typing import Any

# Tipos de evento del contrato (ARQUITECTURA_FINAL §5/§6)
TEXT_DELTA = "text_delta"
THINKING = "thinking"
TOOL_CALL = "tool_call"
TOOL_RESULT = "tool_result"
APPROVAL_REQUEST = "approval_request"
ARTIFACT = "artifact"
AGENT_STEP = "agent_step"
USAGE = "usage"
ERROR = "error"
DONE = "done"


def sse(event: str, data: dict[str, Any]) -> str:
    """Formatea un evento SSE `event:`/`data:`."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def heartbeat() -> str:
    return ":hb\n\n"
