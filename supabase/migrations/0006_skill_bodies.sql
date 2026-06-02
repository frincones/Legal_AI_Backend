-- 0006 · Cuerpo de los SKILL.md en la DB (progressive disclosure server-side)
-- El Router Haiku elige el skill; el runtime carga skills.body_md al system prompt (cacheado).

alter table skills add column if not exists body_md text;
alter table skills add column if not exists argument_hint text;
