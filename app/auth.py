"""Verificación de JWT de Supabase + resolución de org_id.

Fase 0: si hay SUPABASE_JWT_SECRET, verifica firma HS256 (aud=authenticated).
Si no, decodifica claims sin verificar (solo skeleton) — se endurece en Fase 1.
El org_id se resuelve desde memberships en Fase 1; aquí se acepta del header
X-Org-Id o claim, para poder probar el stream end-to-end.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException
from jose import jwt

from .config import settings


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
    # Skeleton: decode claims without signature verification.
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
    org_id = x_org_id or claims.get("org_id") or (claims.get("app_metadata") or {}).get("org_id")
    return Principal(
        user_id=claims.get("sub"),
        email=claims.get("email"),
        org_id=org_id,
    )
