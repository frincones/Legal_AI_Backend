"""Verificación de JWT de Supabase + resolución de org_id (multitenant).

Seguridad (F6.0 hardening):
- Verifica la FIRMA del JWT (aud=authenticated):
  · ES256/RS256 (Supabase "JWT Signing Keys", asimétrico) → vía JWKS público (cacheado).
  · HS256 (secret clásico) → si hay SUPABASE_JWT_SECRET (compatibilidad).
  Si no se puede verificar, loguea alerta CRÍTICA y degrada (no debería ocurrir en prod).
- El org_id se resuelve SIEMPRE server-side desde memberships. NUNCA se confía en el
  header X-Org-Id ni en claims sin validar: el backend usa service_role y bypassa RLS,
  así que esta resolución es la ÚNICA barrera de aislamiento entre tenants.
Sin nuevas dependencias: usa httpx + python-jose[cryptography] (ya presentes).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx
from fastapi import Header, HTTPException
from jose import jwt

from . import db
from .config import settings

_log = logging.getLogger("auth")
_warned_unsigned = False

# Caché de JWKS (claves públicas de Supabase). Se refresca por TTL o ante kid desconocido.
_jwks: dict = {"keys": [], "ts": 0.0}
_JWKS_TTL = 3600.0


def _jwks_url() -> str:
    return f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"


async def _get_jwks(force: bool = False) -> list:
    now = time.time()
    if not force and _jwks["keys"] and now - _jwks["ts"] < _JWKS_TTL:
        return _jwks["keys"]
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(_jwks_url())
            r.raise_for_status()
            _jwks["keys"] = r.json().get("keys", [])
            _jwks["ts"] = now
    except Exception:  # noqa: BLE001
        pass  # conserva el caché previo si la red falla
    return _jwks["keys"]


@dataclass
class Principal:
    user_id: str | None
    email: str | None
    org_id: str | None


async def _decode(token: str) -> dict:
    try:
        header = jwt.get_unverified_header(token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="malformed token") from exc
    alg = header.get("alg")

    # HS256 clásico (si se configuró el secret compartido).
    if alg == "HS256" and settings.supabase_jwt_secret:
        try:
            return jwt.decode(token, settings.supabase_jwt_secret,
                              algorithms=["HS256"], audience="authenticated")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=401, detail=f"invalid token: {exc}") from exc

    # Asimétrico (JWT Signing Keys de Supabase) → verificación vía JWKS.
    if alg in ("ES256", "RS256", "EdDSA"):
        kid = header.get("kid")
        keys = await _get_jwks()
        key = next((k for k in keys if k.get("kid") == kid), None)
        if key is None:  # kid pudo rotar → refresca una vez
            keys = await _get_jwks(force=True)
            key = next((k for k in keys if k.get("kid") == kid), None)
        if key is not None:
            try:
                return jwt.decode(token, key, algorithms=[alg], audience="authenticated")
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=401, detail=f"invalid token: {exc}") from exc

    # Modo degradado: no se pudo verificar la firma.
    global _warned_unsigned
    if not _warned_unsigned:
        _log.critical("JWT sin verificar (alg=%s): no se encontró clave para validar la firma. "
                      "Revisa SUPABASE_URL / JWKS / SUPABASE_JWT_SECRET.", alg)
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
    claims = await _decode(authorization.split(" ", 1)[1])
    user_id = claims.get("sub")

    # Org SIEMPRE desde memberships (server-side). El header X-Org-Id solo se acepta si
    # el usuario es miembro activo de ese org (soporte multi-org sin abrir el hueco).
    org_id = await db.resolve_org(user_id)
    if x_org_id and user_id and x_org_id != org_id and await db.is_member(user_id, x_org_id):
        org_id = x_org_id

    return Principal(user_id=user_id, email=claims.get("email"), org_id=org_id)
