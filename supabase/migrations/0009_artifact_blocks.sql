-- 0009 · Modelo de bloques del documento (para el Canvas) — ADITIVO, no altera nada existente.
-- El Canvas renderiza el documento como bloques {type,text,num,cites[],changed}; se derivan del
-- DOCX de forma determinista (sin LLM) y se guardan aquí para preview + diff + highlight-to-edit.
alter table artifact_versions add column if not exists blocks jsonb;
