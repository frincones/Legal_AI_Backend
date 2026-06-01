-- 0003 · Helper functions + Row Level Security
-- Regla: service_role (backend) BYPASSA RLS → toda query se scope-a por org_id del JWT.
-- RLS aquí es el backstop. force row level security en todas las tablas tenant.

-- ── Helpers (se crean aquí porque dependen de memberships, creada en 0002) ──
create or replace function app.is_member(p_org uuid)
returns boolean language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from memberships
    where user_id = (select auth.uid()) and org_id = p_org and status = 'active'
  );
$$;

create or replace function app.has_role(p_org uuid, p_roles text[])
returns boolean language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from memberships
    where user_id = (select auth.uid()) and org_id = p_org and status = 'active'
      and role = any(p_roles)
  );
$$;

revoke all on function app.is_member(uuid) from public;
revoke all on function app.has_role(uuid, text[]) from public;
grant execute on function app.is_member(uuid) to authenticated, service_role;
grant execute on function app.has_role(uuid, text[]) to authenticated, service_role;

-- ── Bulk: tablas tenant con patrón is_member estándar ──
do $$
declare t text;
begin
  foreach t in array array[
    'company_profiles','practice_profiles','org_plugins','org_skills',
    'projects','chat_sessions','messages','message_parts',
    'artifacts','artifact_versions','matters','active_matter','matter_outputs',
    'documents','chunks','memories','agent_runs','tool_calls'
  ] loop
    execute format('alter table %I enable row level security', t);
    execute format('alter table %I force row level security', t);
    execute format('drop policy if exists %I_sel on %I', t, t);
    execute format('drop policy if exists %I_wr on %I', t, t);
    execute format('create policy %I_sel on %I for select using (app.is_member(org_id))', t, t);
    execute format('create policy %I_wr on %I for all using (app.is_member(org_id)) with check (app.is_member(org_id))', t, t);
  end loop;
end $$;

-- ── orgs ──
alter table orgs enable row level security;  alter table orgs force row level security;
drop policy if exists orgs_sel on orgs;
create policy orgs_sel on orgs for select using (app.is_member(id));

-- ── profiles (self) ──
alter table profiles enable row level security;  alter table profiles force row level security;
drop policy if exists prof_self on profiles;
create policy prof_self on profiles for all using (id = (select auth.uid())) with check (id = (select auth.uid()));

-- ── memberships (no recursivo: self + admin) ──
alter table memberships enable row level security;  alter table memberships force row level security;
drop policy if exists mem_self on memberships;
drop policy if exists mem_admin on memberships;
create policy mem_self  on memberships for select using (user_id = (select auth.uid()));
create policy mem_admin on memberships for all
  using (app.has_role(org_id, '{admin}')) with check (app.has_role(org_id, '{admin}'));

-- ── catálogo global (lectura para autenticados; escritura solo service_role) ──
alter table plugins enable row level security;  alter table plugins force row level security;
alter table skills  enable row level security;  alter table skills  force row level security;
drop policy if exists plugins_sel on plugins;  create policy plugins_sel on plugins for select using (true);
drop policy if exists skills_sel  on skills;   create policy skills_sel  on skills  for select using (true);

-- ── mcp_connectors (metadata solo admins; secreto vive en Vault) ──
alter table mcp_connectors enable row level security;  alter table mcp_connectors force row level security;
drop policy if exists conn_admin on mcp_connectors;
create policy conn_admin on mcp_connectors for all
  using (app.has_role(org_id, '{admin}')) with check (app.has_role(org_id, '{admin}'));

-- ── caches con org_id nullable (global o por org) ──
alter table web_cache enable row level security;        alter table web_cache force row level security;
alter table context7_sources enable row level security; alter table context7_sources force row level security;
drop policy if exists web_cache_sel on web_cache;
create policy web_cache_sel on web_cache for select using (org_id is null or app.is_member(org_id));
drop policy if exists ctx7_sel on context7_sources;
create policy ctx7_sel on context7_sources for select using (org_id is null or app.is_member(org_id));

-- ── APPEND-ONLY (select + insert; sin update/delete) ──
do $$
declare t text;
begin
  foreach t in array array['matter_history','guardrail_events','verifications'] loop
    execute format('alter table %I enable row level security', t);
    execute format('alter table %I force row level security', t);
    execute format('drop policy if exists %I_sel on %I', t, t);
    execute format('drop policy if exists %I_ins on %I', t, t);
    execute format('create policy %I_sel on %I for select using (app.is_member(org_id))', t, t);
    execute format('create policy %I_ins on %I for insert with check (app.is_member(org_id))', t, t);
    execute format('revoke update, delete on %I from authenticated, anon', t);
  end loop;
end $$;

-- ── token_ledger (lectura admins; escritura solo service_role; inmutable) ──
alter table token_ledger enable row level security;  alter table token_ledger force row level security;
drop policy if exists ledger_admin on token_ledger;
create policy ledger_admin on token_ledger for select using (app.has_role(org_id, '{admin}'));
revoke update, delete on token_ledger from authenticated, anon;
