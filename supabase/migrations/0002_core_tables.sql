-- 0002 · Core tables (multi-tenant legal agent)
-- Mapea el filesystem local de claude-for-legal → Postgres + Storage.

set search_path = public, extensions;

-- ───────────────────────── Tenancy ─────────────────────────
create table if not exists orgs (
  id uuid primary key default gen_random_uuid(),
  slug text unique not null,
  name text not null,
  kind text not null default 'firm',
  plan text not null default 'trial',
  settings jsonb not null default '{}',
  created_at timestamptz default now()
);

create table if not exists profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null,
  full_name text,
  bar_number text,
  is_lawyer boolean not null default false,
  created_at timestamptz default now()
);

create table if not exists memberships (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role text not null check (role in ('admin','lawyer','paralegal','staff')),
  status text not null default 'active' check (status in ('active','invited','suspended')),
  attorney_contact uuid references auth.users(id),
  created_at timestamptz default now(),
  unique (org_id, user_id)
);
create index if not exists memberships_user_active on memberships(user_id) where status = 'active';

-- ───────────────────────── Perfiles de práctica ─────────────────────────
create table if not exists company_profiles (
  org_id uuid primary key references orgs(id) on delete cascade,
  entity_name text, industry text, stage text, primary_jurisdiction text,
  practice_setting text, risk_posture text, key_people jsonb default '[]',
  body_md text, updated_at timestamptz default now()
);

create table if not exists practice_profiles (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  plugin_key text not null,
  active_modules text[] default '{}',
  cross_matter_context boolean default false,
  matter_workspaces_enabled boolean default false,
  body_md text,
  is_configured boolean default false,
  configured_by uuid references auth.users(id),
  updated_at timestamptz default now(),
  unique (org_id, plugin_key)
);

-- ───────────────────────── Catálogo + conectores ─────────────────────────
create table if not exists plugins (
  key text primary key, name text, description text, version text
);
create table if not exists skills (
  id uuid primary key default gen_random_uuid(),
  plugin_key text references plugins(key) on delete cascade,
  key text not null, name text, description text,
  user_invocable boolean default true,
  unique (plugin_key, key)
);
create table if not exists org_plugins (
  org_id uuid references orgs(id) on delete cascade,
  plugin_key text references plugins(key) on delete cascade,
  enabled boolean default true,
  primary key (org_id, plugin_key)
);
create table if not exists org_skills (
  org_id uuid references orgs(id) on delete cascade,
  skill_id uuid references skills(id) on delete cascade,
  enabled boolean default true,
  primary key (org_id, skill_id)
);

create table if not exists mcp_connectors (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  connector_key text not null,
  transport text default 'http',
  base_url text,
  auth_type text,
  secret_ref uuid,                 -- → vault.secrets(id); nunca secreto en claro
  scopes text[] default '{}',
  enabled boolean default true,
  status text default 'unconfigured',
  last_checked_at timestamptz,
  unique (org_id, connector_key)
);

-- ───────────────────────── Chat streaming-native ─────────────────────────
create table if not exists projects (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  matter_id uuid,
  name text not null,
  instructions_md text,
  created_by uuid references auth.users(id),
  created_at timestamptz default now()
);

create table if not exists chat_sessions (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  project_id uuid references projects(id) on delete set null,
  matter_id uuid,
  user_id uuid references auth.users(id),
  title text,
  model_tier text default 'sonnet',
  status text default 'active',
  token_summary jsonb default '{}',
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists messages (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  session_id uuid not null references chat_sessions(id) on delete cascade,
  seq bigint not null,
  role text not null check (role in ('user','assistant','tool','system')),
  status text default 'complete',
  model text,
  created_at timestamptz default now(),
  unique (session_id, seq)
);

create table if not exists message_parts (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  message_id uuid not null references messages(id) on delete cascade,
  idx int not null,
  type text not null,              -- text|thinking|tool_use|tool_result|artifact|citation
  text text,
  tool_name text, tool_use_id text,
  input jsonb, output jsonb,
  is_untrusted boolean default false,
  artifact_version_id uuid,
  citations jsonb,
  created_at timestamptz default now(),
  unique (message_id, idx)
);

-- ───────────────────────── Artifacts / redlines ─────────────────────────
create table if not exists artifacts (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  matter_id uuid,
  session_id uuid references chat_sessions(id),
  title text not null,
  kind text not null,              -- document|contract|memo|markdown|code|html|redline
  current_version_id uuid,
  created_by uuid references auth.users(id),
  created_at timestamptz default now()
);

create table if not exists artifact_versions (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  artifact_id uuid not null references artifacts(id) on delete cascade,
  version int not null,
  content text,
  storage_path text,
  diff_from_version int,
  diff jsonb,
  authored_by text default 'agent',
  created_at timestamptz default now(),
  unique (artifact_id, version)
);

-- ───────────────────────── Matters ─────────────────────────
create table if not exists matters (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  slug text not null,
  plugin_key text,
  name text not null,
  matter_type text, role text, client text, counterparty text, jurisdiction text,
  status text default 'active', stage text, confidentiality text default 'standard',
  risk text, materiality text, exposure_range text,
  outside_counsel jsonb, conflicts jsonb, legal_hold jsonb, internal_owners jsonb,
  related_matter_ids uuid[] default '{}',
  key_facts_md text, notes_md text,
  opened date, next_deadline date, closed date, outcome text,
  created_by uuid references auth.users(id),
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  unique (org_id, slug)
);

create table if not exists active_matter (
  org_id uuid references orgs(id) on delete cascade,
  user_id uuid references auth.users(id) on delete cascade,
  plugin_key text,
  matter_id uuid references matters(id),
  primary key (org_id, user_id, plugin_key)
);

create table if not exists matter_history (             -- APPEND-ONLY
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  matter_id uuid not null references matters(id) on delete cascade,
  ts timestamptz default now(),
  actor uuid references auth.users(id),
  actor_kind text default 'agent',
  event_type text,
  summary text not null,
  detail jsonb,
  session_id uuid references chat_sessions(id),
  artifact_version_id uuid
);

create table if not exists matter_outputs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  matter_id uuid not null references matters(id) on delete cascade,
  skill_key text, title text, storage_path text,
  artifact_id uuid references artifacts(id),
  created_at timestamptz default now()
);

-- ───────────────────────── RAG + adjuntos + memoria ─────────────────────────
create table if not exists documents (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  matter_id uuid, project_id uuid,
  source text, source_ref text, title text, mime_type text, storage_path text,
  retrieved_at timestamptz,
  is_untrusted boolean default true,
  ingest_status text default 'pending',
  created_at timestamptz default now()
);

create table if not exists chunks (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  document_id uuid not null references documents(id) on delete cascade,
  matter_id uuid, idx int not null, content text not null, token_count int,
  embedding vector(1536),
  fts tsvector generated always as (to_tsvector('english', content)) stored,
  metadata jsonb default '{}',
  unique (document_id, idx)
);
create index if not exists chunks_hnsw on chunks using hnsw (embedding vector_cosine_ops) with (m=16, ef_construction=64);
create index if not exists chunks_fts on chunks using gin (fts);
create index if not exists chunks_scope on chunks(org_id, matter_id);

create table if not exists web_cache (
  id uuid primary key default gen_random_uuid(),
  org_id uuid references orgs(id) on delete cascade,   -- nullable = caché global
  url text not null, content_md text, title text,
  source text default 'firecrawl', credits int default 1,
  fetched_at timestamptz default now(), ttl_seconds int default 86400,
  unique (org_id, url)
);
create index if not exists web_cache_url on web_cache(url);

create table if not exists memories (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  scope text not null,             -- user|matter|org
  user_id uuid references auth.users(id), matter_id uuid references matters(id),
  kind text, content text not null, embedding vector(1536),
  salience real default 0.5, source_session_id uuid, expires_at timestamptz,
  created_at timestamptz default now()
);
create index if not exists memories_hnsw on memories using hnsw (embedding vector_cosine_ops) with (m=16, ef_construction=64);

-- ───────────────────────── Observabilidad / economía ─────────────────────────
create table if not exists agent_runs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  session_id uuid references chat_sessions(id), matter_id uuid,
  parent_run_id uuid references agent_runs(id),
  agent_key text, model text, model_tier text, status text default 'running',
  started_at timestamptz default now(), ended_at timestamptz, error text
);

create table if not exists tool_calls (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  run_id uuid references agent_runs(id) on delete cascade,
  message_part_id uuid, tool_name text, mcp_connector_key text,
  input jsonb, output_summary text, is_untrusted boolean default false,
  credits int default 0, duration_ms int, status text,
  created_at timestamptz default now()
);

create table if not exists token_ledger (              -- núcleo de LLM compounding
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  run_id uuid references agent_runs(id), session_id uuid, user_id uuid, matter_id uuid,
  model text not null, input_tokens int default 0, output_tokens int default 0,
  cache_creation_tokens int default 0, cache_read_tokens int default 0,
  thinking_tokens int default 0, cost_usd numeric(12,6),
  created_at timestamptz default now()
);
create index if not exists token_ledger_org_time on token_ledger(org_id, created_at);

create table if not exists guardrail_events (          -- APPEND-ONLY
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  session_id uuid, matter_id uuid, run_id uuid,
  rule text not null, decision text not null,
  actor uuid, override_by uuid, override_rationale text, detail jsonb,
  created_at timestamptz default now()
);

create table if not exists verifications (             -- APPEND-ONLY · paper trail de citas
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  matter_id uuid, cite text not null, source text, verdict text,
  verified_by uuid references auth.users(id), verified_at timestamptz default now(),
  detail jsonb
);

create table if not exists context7_sources (
  id uuid primary key default gen_random_uuid(),
  org_id uuid, library_id text not null, topic text, content text,
  embedding vector(1536), fetched_at timestamptz default now(),
  ttl_seconds int default 86400, unique (org_id, library_id, topic)
);
