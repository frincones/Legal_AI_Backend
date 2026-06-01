# Legal AI — Backend

Motor del agente legal multi-tenant: **FastAPI + Claude Agent SDK** sobre **Railway**, con **Supabase** (Postgres + pgvector + Auth + Storage + Vault) como capa de datos.

> Arquitectura completa: ver `ARQUITECTURA_FINAL.md` · Plan: `PLAN_IMPLEMENTACION_v2.md` (repo `Legal_AI`).

## Estado: Fase 0 (cimientos)
- ✅ Esquema multi-tenant + RLS (`supabase/migrations/`)
- ✅ FastAPI: `/health`, `/ready`, `POST /api/chat/{session_id}` (bridge SSE de demo)
- ✅ Verificación JWT Supabase + resolución de org
- ⏳ Fase 1: loop real `ClaudeSDKClient`, carga de plugins `claude-for-legal`, document tools, RAG

## Estructura
```
app/
  main.py            # FastAPI + CORS
  config.py          # settings desde env
  auth.py            # JWT Supabase → Principal(org_id)
  bridge.py          # contrato SSE normalizado
  api/health.py      # /health, /ready
  api/chat.py        # POST /api/chat/{session_id} (SSE)
supabase/migrations/ # 0001..0004 (extensions, tablas, RLS, seed)
tests/               # test_cross_tenant
```

## Local
```bash
python -m venv .venv && . .venv/Scripts/activate   # Windows
pip install -r requirements.txt
cp .env.example .env   # rellena claves
uvicorn app.main:app --reload
```

## Migraciones (Supabase Management API)
```bash
python - <<'PY'
import json,urllib.request,glob,os
REF=os.environ["SUPABASE_PROJECT_REF"]; TOKEN=os.environ["SUPABASE_ACCESS_TOKEN"]
url=f"https://api.supabase.com/v1/projects/{REF}/database/query"
UA="Mozilla/5.0"
for f in sorted(glob.glob("supabase/migrations/*.sql")):
    req=urllib.request.Request(url,data=json.dumps({"query":open(f,encoding='utf-8').read()}).encode(),
        method="POST",headers={"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json","User-Agent":UA})
    print(os.path.basename(f), urllib.request.urlopen(req).status)
PY
```

## Deploy (Railway)
Dockerfile + `railway.json`. Variables: ver `.env.example`.
