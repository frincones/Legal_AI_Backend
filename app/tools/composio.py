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
                    # Composio devuelve el status en MAYÚSCULA ("ACTIVE"); normalizamos a minúscula
                    # para que coincida con los filtros (UI 'connected' y la inyección de tools).
                    "status": (it.get("status") or "").lower()})
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


# Set CURADO de tools de alto valor por toolkit (acota contexto y costo; slugs verificados).
CORE_TOOLS: dict[str, list[str]] = {
    "gmail": ["GMAIL_SEND_EMAIL", "GMAIL_FETCH_EMAILS", "GMAIL_CREATE_EMAIL_DRAFT",
              "GMAIL_REPLY_TO_THREAD", "GMAIL_FETCH_MESSAGE_BY_THREAD_ID", "GMAIL_GET_CONTACTS"],
    "googlecalendar": ["GOOGLECALENDAR_CREATE_EVENT", "GOOGLECALENDAR_EVENTS_LIST",
                       "GOOGLECALENDAR_FIND_EVENT", "GOOGLECALENDAR_FIND_FREE_SLOTS",
                       "GOOGLECALENDAR_DELETE_EVENT", "GOOGLECALENDAR_GET_CURRENT_DATE_TIME"],
    "googledrive": ["GOOGLEDRIVE_FIND_FILE", "GOOGLEDRIVE_DOWNLOAD_FILE",
                    "GOOGLEDRIVE_CREATE_FILE_FROM_TEXT", "GOOGLEDRIVE_CREATE_FOLDER", "GOOGLEDRIVE_UPLOAD_FILE"],
    "googledocs": ["GOOGLEDOCS_CREATE_DOCUMENT_MARKDOWN", "GOOGLEDOCS_GET_DOCUMENT_BY_ID",
                   "GOOGLEDOCS_SEARCH_DOCUMENTS", "GOOGLEDOCS_UPDATE_DOCUMENT_MARKDOWN"],
    "googlesheets": ["GOOGLESHEETS_BATCH_GET", "GOOGLESHEETS_BATCH_UPDATE",
                     "GOOGLESHEETS_CREATE_GOOGLE_SHEET1", "GOOGLESHEETS_GET_SPREADSHEET_INFO"],
    "outlook": ["OUTLOOK_OUTLOOK_SEND_EMAIL", "OUTLOOK_OUTLOOK_LIST_MESSAGES",
                "OUTLOOK_OUTLOOK_REPLY_EMAIL", "OUTLOOK_OUTLOOK_CALENDAR_CREATE_EVENT",
                "OUTLOOK_OUTLOOK_SEARCH_MESSAGES"],
    "microsoft_teams": ["MICROSOFT_TEAMS_CREATE_MEETING", "MICROSOFT_TEAMS_CHATS_GET_ALL_MESSAGES",
                        "MICROSOFT_TEAMS_LIST_TEAM_MEMBERS"],
}


async def tools_for(toolkit_slugs: list[str], per_toolkit: int = 8) -> list[dict]:
    """Schemas de tools (formato Anthropic) de los toolkits, acotado al set curado. Para el agente."""
    out: list[dict] = []
    for slug in toolkit_slugs:
        allow = set(CORE_TOOLS.get(slug, []))
        st, data = await _req("GET", f"/tools?toolkit_slug={slug}&limit=60")
        items = (data or {}).get("items", []) if isinstance(data, dict) else []
        sel = [t for t in items if t.get("slug") in allow] if allow else items[:per_toolkit]
        out.extend(_to_anthropic(t) for t in sel if t.get("slug"))
    return out


COMPOSIO_TOOL_NAMES: set[str] = set()  # se llena al cargar tools por-turno (dispatch en registry)


async def execute(tool_slug: str, user_id: str, arguments: dict,
                  connected_account_id: str | None = None) -> str:
    """Ejecuta una tool de Composio para el usuario y devuelve un resumen para el modelo."""
    import json as _json
    if not available():
        return "[integración no disponible]"
    body: dict = {"tool_slug": tool_slug, "user_id": user_id, "arguments": arguments or {}}
    if connected_account_id:
        body["connected_account_id"] = connected_account_id
    st, data = await _req("POST", "/tools/execute", body)
    if not isinstance(data, dict):
        return f"[{tool_slug}] error de ejecución (HTTP {st}). ¿La cuenta está conectada?"
    ok = data.get("successful", data.get("success", True))
    payload = data.get("data", data.get("response_data", data))
    try:
        text = _json.dumps(payload, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        text = str(payload)
    prefix = "OK" if ok else "ERROR"
    return f"[{tool_slug} · {prefix}] {text[:3500]}"
