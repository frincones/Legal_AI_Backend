"""Test cross-tenant (Fase 0) — el backstop RLS no debe filtrar entre orgs.

Requiere SUPABASE_URL + SUPABASE_ANON_KEY + dos JWTs de usuario de orgs distintas.
En CI se ejecuta contra el proyecto Supabase. Marcado skip si faltan credenciales.
"""
from __future__ import annotations

import os

import httpx
import pytest

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
ANON = os.environ.get("SUPABASE_ANON_KEY", "")
JWT_ORG_A = os.environ.get("TEST_JWT_ORG_A", "")


@pytest.mark.skipif(not (SUPABASE_URL and ANON and JWT_ORG_A), reason="faltan credenciales de prueba")
def test_org_a_no_ve_filas_de_org_b():
    # Como usuario de org A, una consulta a matters NO debe devolver filas de org B.
    r = httpx.get(
        f"{SUPABASE_URL}/rest/v1/matters?select=org_id",
        headers={"apikey": ANON, "Authorization": f"Bearer {JWT_ORG_A}"},
        timeout=15,
    )
    assert r.status_code == 200
    org_ids = {row["org_id"] for row in r.json()}
    # Solo debe ver su propia org (o ninguna). Nunca varias.
    assert len(org_ids) <= 1, f"fuga cross-tenant: {org_ids}"
