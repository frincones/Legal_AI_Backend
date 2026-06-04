"""Broker de Composio (F6.2) — integraciones del agente con Google Workspace / Office 365.

El backend es el ÚNICO que conoce la API key de Composio (nunca llega al browser). Cada usuario
es una "entity" en Composio (entity/user_id = user_id de Supabase) → sus connected accounts le
pertenecen solo a él (aislamiento por-usuario, alineado con el hardening multitenant F6.0).

Auth gestionado (zero-config GCP/Azure): usamos auth configs gestionados por Composio. El OAuth
del consentimiento lo maneja Composio; el usuario autoriza vía una URL hosteada.

Sin dependencias nuevas: REST vía httpx (ya presente). Todo es ADITIVO y defensivo: si Composio
no está configurado o falla, las funciones devuelven vacío/None y el agente sigue igual que hoy.
"""
from __future__ import annotations

import httpx

from ..config import settings

# Toolkits habilitados para el asistente legal + su auth_config_id gestionado (creados una vez).
# slug Composio -> {auth_config_id, label, icon (para el front)}
TOOLKITS: dict[str, dict] = {
    "gmail":           {"auth_config_id": "ac_Ez9PIhnO9aSN", "label": "Gmail",            "icon": "mail",     "provider": "google"},
    "googlecalendar":  {"auth_config_id": "ac_A8RdXe30BUge", "label": "Google Calendar",  "icon": "calendar", "provider": "google"},
    "googledrive":     {"auth_config_id": "ac_yYgDZQoKiYRY", "label": "Google Drive",     "icon": "folder",   "provider": "google"},
    "googledocs":      {"auth_config_id": "ac_cz6wMcJvze7a", "label": "Google Docs",      "icon": "fileText", "provider": "google"},
    "googlesheets":    {"auth_config_id": "ac_Eb-APVpGpIQ0", "label": "Google Sheets",    "icon": "layers",   "provider": "google"},
    "outlook":         {"auth_config_id": "ac_6dEJzLscr4mM", "label": "Outlook",          "icon": "mail",     "provider": "microsoft"},
    "microsoft_teams": {"auth_config_id": "ac_e81jKOwOF9cK", "label": "Microsoft Teams",  "icon": "message",  "provider": "microsoft"},
}


def available() -> bool:
    return bool(settings.composio_api_key)


def _headers() -> dict:
    return {"x-api-key": settings.composio_api_key, "Content-Type": "application/json"}


async def _req(method: str, path: str, body: dict | None = None) -> tuple[int, dict | list | None]:
    if not available():
        return 0, None
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.request(method, f"{settings.composio_base}{path}", headers=_headers(), json=body)
            data = r.json() if r.content else None
            return r.status_code, data
    except Exception:  # noqa: BLE001
        return 0, None


# ── Conexiones (OAuth gestionado) ──
async def initiate(user_id: str, toolkit: str, callback_url: str) -> dict | None:
    """Inicia la conexión OAuth de un usuario a un toolkit. Devuelve {redirect_url, ...} o None."""
    tk = TOOLKITS.get(toolkit)
    if not tk or not user_id:
        return None
    st, data = await _req("POST", "/connected_accounts/link", {
        "auth_config_id": tk["auth_config_id"], "user_id": user_id, "callback_url": callback_url})
    if st in (200, 201) and isinstance(data, dict):
        redirect = data.get("redirect_url") or data.get("redirectUrl")
        return {"redirect_url": redirect, "id": data.get("id") or data.get("nanoid"), "toolkit": toolkit}
    return None


async def list_connections(user_id: str) -> list[dict]:
    """Connected accounts ACTIVAS del usuario. Devuelve [{toolkit, connected_account_id, status}]."""
    if not user_id:
        return []
    st, data = await _req("GET", f"/connected_accounts?user_ids={user_id}&limit=100")
    items = (data or {}).get("items", []) if isinstance(data, dict) else []
    out = []
    for it in items:
        tkslug = (it.get("toolkit") or {}).get("slug") or it.get("toolkit_slug")
        out.append({"toolkit": tkslug, "connected_account_id": it.get("id") or it.get("nanoid"),
                    "status": it.get("status")})
    return out


async def get_connection(ca_id: str) -> dict | None:
    st, data = await _req("GET", f"/connected_accounts/{ca_id}")
    return data if st == 200 and isinstance(data, dict) else None


async def delete_connection(ca_id: str) -> bool:
    st, _ = await _req("DELETE", f"/connected_accounts/{ca_id}")
    return st in (200, 204)


# ── Tools (definiciones para el LLM + ejecución) ──
def _to_anthropic(tool: dict) -> dict:
    """Mapea una tool de Composio a schema de tool de Anthropic. El nombre = slug de Composio."""
    schema = tool.get("input_parameters") or {"type": "object", "properties": {}}
    desc = (tool.get("description") or tool.get("name") or "")[:1024]
    return {"name": tool["slug"], "description": desc, "input_schema": schema}


async def tools_for(toolkit_slugs: list[str], limit: int = 40) -> list[dict]:
    """Schemas de tools (formato Anthropic) de los toolkits dados. Para inyectar al agente."""
    if not toolkit_slugs:
        return []
    slugs = ",".join(toolkit_slugs)
    st, data = await _req("GET", f"/tools?toolkit_slugs={slugs}&limit={limit}")
    items = (data or {}).get("items", []) if isinstance(data, dict) else []
    return [_to_anthropic(t) for t in items if t.get("slug")]


COMPOSIO_TOOL_NAMES: set[str] = set()  # se llena al cargar tools por-turno (dispatch en registry)


async def execute(tool_slug: str, user_id: str, arguments: dict,
                  connected_account_id: str | None = None) -> dict:
    """Ejecuta una tool de Composio para el usuario (con su connected account)."""
    body: dict = {"tool_slug": tool_slug, "user_id": user_id, "arguments": arguments or {}}
    if connected_account_id:
        body["connected_account_id"] = connected_account_id
    st, data = await _req("POST", "/tools/execute", body)
    if isinstance(data, dict):
        return data
    return {"successful": False, "error": f"composio execute fallo (HTTP {st})"}
