-- 0012 · Integraciones del usuario con Composio (F6.2) — ADITIVO.
-- Cada conexión OAuth (Gmail, Calendar, Drive, Outlook…) de un usuario, atada a su cuenta de
-- Supabase. El agente solo usa las habilitadas del usuario actual. Aislamiento por-usuario (RLS).

create table if not exists user_integrations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  org_id uuid references orgs(id) on delete cascade,
  toolkit text not null,                       -- slug Composio: gmail|googlecalendar|googledrive|...
  connected_account_id text,                   -- id de la connected account en Composio
  account_label text,                          -- ej. correo conectado (para distinguir varias cuentas)
  status text not null default 'initiated',    -- initiated|active|failed|expired
  enabled boolean not null default true,       -- ¿el agente puede usar esta integración?
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  unique (user_id, toolkit, connected_account_id)
);

create index if not exists user_integrations_user on user_integrations (user_id, status);

-- ── RLS: cada usuario ve/gestiona SOLO sus integraciones (el backend usa service_role aparte). ──
alter table user_integrations enable row level security;
alter table user_integrations force row level security;
drop policy if exists user_integrations_self on user_integrations;
create policy user_integrations_self on user_integrations for all
  using (user_id = (select auth.uid()))
  with check (user_id = (select auth.uid()));
