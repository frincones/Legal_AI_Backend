-- 0010 · Biblioteca de patrones reutilizables (F4 · flywheel) — ADITIVO, no altera nada existente.
-- Cada vez que el agente genera un documento válido por código (docx-js), guardamos el código como
-- un "patrón" reutilizable. Futuras solicitudes similares parten de ese docx-js ya verificado y solo
-- modifican lo necesario → menos tokens de salida, estructura probada, costo marginal decreciente.
-- El embedding (384 dims, fastembed local · $0) habilita sugerencia semántica opcional del agente.

create table if not exists documentos_patron (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references orgs(id) on delete cascade,
  artifact_id uuid references artifacts(id) on delete set null,   -- origen (trazabilidad)
  title text not null,
  kind text not null default 'document',                           -- document|memo|letter|table
  docx_js text not null,                                           -- el código completo (reutilizable)
  params jsonb default '{}',                                       -- metadatos: tipo de pieza, materia, etc.
  embedding vector(384),                                           -- semántico (opcional, $0 local)
  used_count int not null default 0,                               -- "usado N veces"
  created_by uuid references auth.users(id),
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create index if not exists documentos_patron_org on documentos_patron (org_id, used_count desc, updated_at desc);
create index if not exists documentos_patron_hnsw on documentos_patron
  using hnsw (embedding vector_cosine_ops) with (m=16, ef_construction=64);

-- Búsqueda semántica top-k de patrones (cosine), scope-ada por org. Solo lee patrones con embedding.
create or replace function match_patrones(query_embedding vector(384), p_org uuid, k int default 4)
returns table(id uuid, title text, kind text, docx_js text, used_count int, similarity float)
language sql stable as $$
  select p.id, p.title, p.kind, p.docx_js, p.used_count, 1 - (p.embedding <=> query_embedding) as similarity
  from documentos_patron p
  where p.org_id = p_org and p.embedding is not null
  order by p.embedding <=> query_embedding
  limit k;
$$;
grant execute on function match_patrones(vector, uuid, int) to service_role, authenticated;

-- Incremento atómico de used_count (al reutilizar un patrón).
create or replace function bump_patron_use(p_id uuid)
returns void language sql as $$
  update documentos_patron set used_count = used_count + 1, updated_at = now() where id = p_id;
$$;
grant execute on function bump_patron_use(uuid) to service_role, authenticated;

-- ── RLS ── (lectura: solo miembros de la org; escritura: service_role, que bypassa RLS)
alter table documentos_patron enable row level security;
drop policy if exists documentos_patron_org on documentos_patron;
create policy documentos_patron_org on documentos_patron for select to authenticated
  using (org_id in (select org_id from memberships where user_id = auth.uid() and status = 'active'));
