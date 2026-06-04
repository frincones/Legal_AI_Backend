"""Verificación de JWT de Supabase + resolución de org_id (multitenant).

Seguridad (F6.0 hardening):
- Si hay SUPABASE_JWT_SECRET, verifica la firma HS256 (aud=authenticated). Si falta,
  loguea una alerta CRÍTICA (modo degradado) — debe setearse en producción.
- El org_id se resuelve SIEMPRE server-side desde memberships. NUNCA se confía en el
  header X-Org-Id ni en claims sin validar: el backend usa service_role y bypassa RLS,
  así que esta resolución es la ÚNICA barrera de aislamiento entre tenants.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import Header, HTTPException
from jose import jwt

from . import db
from .config import settings

_log = logging.getLogger("auth")
_warned_unsigned = False


@dataclass
class Principal:
    user_id: str | None
    email: str | None
    org_id: str | None


def _decode(token: str) -> dict:
    if settings.supabase_jwt_secret:
        try:
            return jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience="authenticated",
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=401, detail=f"invalid token: {exc}") from exc
    # Modo degradado: sin secret no se puede verificar la firma → tokens forjables.
    global _warned_unsigned
    if not _warned_unsigned:
        _log.critical("SUPABASE_JWT_SECRET no configurado: los JWT NO se verifican. "
                      "Setéalo en producción (Supabase → Settings → API → JWT Secret).")
        _warned_unsigned = True
    try:
        return jwt.get_unverified_claims(token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="malformed token") from exc


async def get_principal(
    authorization: str | None = Header(default=None),
    x_org_id: str | None = Header(default=None),
) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    claims = _decode(authorization.split(" ", 1)[1])
    user_id = claims.get("sub")

    # Org SIEMPRE desde memberships (server-side). El header X-Org-Id solo se acepta si
    # el usuario es miembro activo de ese org (soporte multi-org sin abrir el hueco).
    org_id = await db.resolve_org(user_id)
    if x_org_id and user_id and x_org_id != org_id and await db.is_member(user_id, x_org_id):
        org_id = x_org_id

    return Principal(user_id=user_id, email=claims.get("email"), org_id=org_id)
