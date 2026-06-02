-- 0008 · verificar_fuente: motor de verificación/grounding legal (Colombia)
-- Tablas: autoridades_registry (KB extensible), fuente_cache (veredictos), verificaciones (auditoría)

-- ── Registro de Autoridades (mapa de fuentes oficiales CO) ──
create table if not exists autoridades_registry (
  id uuid primary key default gen_random_uuid(),
  entidad text not null unique,
  materias text[] default '{}',
  tier int not null,                       -- 0 primaria · 1 doctrina/regulación · 2 compilador · 3 registro
  dominios text[] default '{}',
  tipos text[] default '{}',
  plantillas_url jsonb default '{}'::jsonb,
  hints_extraccion text[] default '{}',
  activo bool default true,
  created_at timestamptz default now()
);

-- ── Caché de veredictos (TTL adaptativo por estado) ──
create table if not exists fuente_cache (
  id uuid primary key default gen_random_uuid(),
  jurisdiccion text default 'CO',
  clave_normalizada text not null unique,
  tipo_fuente text,
  estado text,
  record jsonb,
  confianza numeric,
  fuentes_urls text[] default '{}',
  fecha_consulta timestamptz default now(),
  expires_at timestamptz
);
create index if not exists fuente_cache_expires on fuente_cache (expires_at);

-- ── Auditoría de verificaciones ──
create table if not exists verificaciones (
  id uuid primary key default gen_random_uuid(),
  org_id uuid,
  session_id uuid,
  run_id uuid,
  consulta text,
  tipo_fuente text,
  estado text,
  tier int,
  confianza numeric,
  fuentes jsonb,
  latency_ms int,
  creditos int default 0,
  created_at timestamptz default now()
);
create index if not exists verificaciones_org on verificaciones (org_id, created_at desc);

-- ── RLS ──
alter table autoridades_registry enable row level security;
alter table fuente_cache enable row level security;
alter table verificaciones enable row level security;

-- Registro y caché: lectura para usuarios autenticados; escritura solo service_role (bypassa RLS).
drop policy if exists autoridades_read on autoridades_registry;
create policy autoridades_read on autoridades_registry for select to authenticated using (true);
drop policy if exists fuente_cache_read on fuente_cache;
create policy fuente_cache_read on fuente_cache for select to authenticated using (true);

-- Verificaciones: solo miembros de la org (auditoría).
drop policy if exists verificaciones_org on verificaciones;
create policy verificaciones_org on verificaciones for select to authenticated
  using (org_id in (select org_id from memberships where user_id = auth.uid() and status = 'active'));

-- ── Seed: Registro de Autoridades (mapa verificado de Colombia) ──
insert into autoridades_registry (entidad, materias, tier, dominios, tipos, plantillas_url, hints_extraccion) values
 ('Corte Constitucional', '{constitucional,tutela,control}', 0,
   '{corteconstitucional.gov.co}', '{sentencia}',
   '{"sentencia":"https://www.corteconstitucional.gov.co/relatoria/{anio}/{clase}-{num}-{aa}.htm"}',
   '{RESUELVE,EXEQUIBLE,INEXEQUIBLE,"exequible condicionado","Magistrado Ponente","problema jurídico"}'),
 ('Corte Suprema de Justicia', '{civil,penal,laboral,casacion}', 0,
   '{cortesuprema.gov.co,consultajurisprudencial.ramajudicial.gov.co}', '{sentencia,auto}',
   '{"buscador":"https://consultajurisprudencial.ramajudicial.gov.co/"}',
   '{radicado,"Magistrado Ponente",RESUELVE,casación}'),
 ('Consejo de Estado', '{administrativo,contencioso,concepto}', 0,
   '{consejodeestado.gov.co,samai.consejodeestado.gov.co}', '{sentencia,concepto}',
   '{"buscador":"https://www.consejodeestado.gov.co/buscador-de-jurisprudencia2/index.htm"}',
   '{radicado,RESUELVE,unificación,"extensión de jurisprudencia"}'),
 ('Diario Oficial', '{publicacion}', 0,
   '{imprenta.gov.co}', '{ley,decreto,acto}',
   '{"buscador":"https://www.imprenta.gov.co/diario-oficial"}',
   '{"Diario Oficial",promulgación,publicación}'),
 ('Funcion Publica - Gestor Normativo', '{general,administrativo,vigencia,derogatoria}', 2,
   '{funcionpublica.gov.co}', '{ley,decreto,concepto,sentencia}',
   '{"norma":"https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i={id}","buscador":"https://www.funcionpublica.gov.co/eva/gestor-normativo"}',
   '{"Notas de Vigencia","Resumen de Notas de Vigencia","Derogado por","Modificado por",INEXEQUIBLE,"rige a partir","Jurisprudencia Vigencia"}'),
 ('SUIN-Juriscol', '{general,vigencia,derogatoria,jurisprudencia}', 2,
   '{suin-juriscol.gov.co}', '{ley,decreto,jurisprudencia}',
   '{"depuracion":"https://www.suin-juriscol.gov.co/legislacion/depuracionNormativa.html","buscador":"https://www.suin-juriscol.gov.co/"}',
   '{"Notas de Vigencia",Derogado,Inexequible,Nulo,"impactos normativos"}'),
 ('Secretaria del Senado', '{general,leyes,codigos}', 2,
   '{secretariasenado.gov.co}', '{ley,codigo,sentencia}',
   '{"ley":"http://www.secretariasenado.gov.co/senado/basedoc/ley_{num4}_{anio}.html"}',
   '{"Notas de Vigencia","Vigencia expresa","Notas del Editor",Jurisprudencia}'),
 ('DIAN', '{tributario,aduanero,cambiario,retencion,iva,uvt,"factura electronica"}', 1,
   '{normograma.dian.gov.co,dian.gov.co}', '{concepto,oficio,resolucion,circular}',
   '{"buscador":"https://normograma.dian.gov.co/dian/"}',
   '{"Tesis jurídica","Problema jurídico",Concepto,Vigencia,"deroga el"}'),
 ('Superintendencia Financiera', '{financiero,bancario,seguros,valores}', 1,
   '{superfinanciera.gov.co}', '{circular,concepto,resolucion}',
   '{"sitio":"https://www.superfinanciera.gov.co/"}',
   '{"Circular Básica Jurídica",concepto,vigencia}'),
 ('Superintendencia de Sociedades', '{societario,insolvencia,comercial}', 1,
   '{supersociedades.gov.co}', '{circular,concepto,oficio}',
   '{"sitio":"https://www.supersociedades.gov.co/"}',
   '{"Circular Básica Jurídica",concepto,oficio,vigencia}'),
 ('Superintendencia de Industria y Comercio', '{"datos personales",consumidor,competencia,"propiedad industrial"}', 1,
   '{sic.gov.co}', '{circular,concepto,resolucion}',
   '{"sitio":"https://www.sic.gov.co/"}', '{circular,concepto,vigencia}'),
 ('CREG', '{energia,gas}', 1,
   '{gestornormativo.creg.gov.co,creg.gov.co}', '{resolucion}',
   '{"gestor":"https://gestornormativo.creg.gov.co/"}', '{resolución,compilación,vigencia,derogada}'),
 ('CRC', '{comunicaciones,tic}', 1,
   '{crcom.gov.co}', '{resolucion}', '{"sitio":"https://www.crcom.gov.co/"}',
   '{resolución,"CRC 5050",vigencia}'),
 ('CRA', '{agua,saneamiento,"servicios publicos"}', 1,
   '{cra.gov.co,normas.cra.gov.co}', '{resolucion}',
   '{"gestor":"https://normas.cra.gov.co/gestor/"}', '{resolución,vigencia,derogada}'),
 ('Consejo de Estado - Sala de Consulta', '{concepto,administrativo}', 1,
   '{consejodeestado.gov.co}', '{concepto}',
   '{"sitio":"https://www.consejodeestado.gov.co/sala-de-consulta-y-servicio-civil/index.htm"}',
   '{concepto,"Sala de Consulta"}'),
 ('RUES', '{registral,empresarial,mercantil}', 3,
   '{rues.org.co}', '{registro}', '{"sitio":"https://www.rues.org.co/"}',
   '{NIT,matrícula,"representante legal",existencia}'),
 ('Consulta de Procesos (Rama Judicial)', '{procesal,radicado}', 3,
   '{consultaprocesos.ramajudicial.gov.co}', '{proceso}',
   '{"sitio":"https://consultaprocesos.ramajudicial.gov.co/"}', '{radicado,actuación,juzgado}'),
 ('SECOP', '{contratacion,publica}', 3,
   '{colombiacompra.gov.co,community.secop.gov.co}', '{proceso,contrato}',
   '{"sitio":"https://www.colombiacompra.gov.co/secop"}', '{proceso,contrato,adjudicación}')
on conflict (entidad) do update set
  materias = excluded.materias, tier = excluded.tier, dominios = excluded.dominios,
  tipos = excluded.tipos, plantillas_url = excluded.plantillas_url,
  hints_extraccion = excluded.hints_extraccion, activo = true;
