-- 0007 · Embeddings semánticos (modelo local fastembed · 384 dims) + RPC de búsqueda
-- Recrea las columnas vector a 384 (los embeddings actuales son null → sin pérdida).

drop index if exists chunks_hnsw;
drop index if exists memories_hnsw;

alter table chunks  drop column if exists embedding;
alter table chunks  add  column embedding vector(384);
alter table memories drop column if exists embedding;
alter table memories add  column embedding vector(384);

create index chunks_hnsw   on chunks   using hnsw (embedding vector_cosine_ops) with (m=16, ef_construction=64);
create index memories_hnsw on memories using hnsw (embedding vector_cosine_ops) with (m=16, ef_construction=64);

-- Búsqueda semántica top-k (cosine). El backend la llama vía /rpc con org explícito.
create or replace function match_chunks(query_embedding vector(384), p_org uuid, p_matter uuid default null, k int default 6)
returns table(content text, document_id uuid, similarity float)
language sql stable as $$
  select c.content, c.document_id, 1 - (c.embedding <=> query_embedding) as similarity
  from chunks c
  where c.org_id = p_org and c.embedding is not null
    and (p_matter is null or c.matter_id = p_matter)
  order by c.embedding <=> query_embedding
  limit k;
$$;

grant execute on function match_chunks(vector, uuid, uuid, int) to service_role, authenticated;
