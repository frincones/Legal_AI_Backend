# Plan de implementación — `verificar_fuente`

> Motor de verificación/grounding legal (Colombia) usado por el agente ReAct.
> Complementa el diseño en [`DISENO_verificar_fuente.md`](./DISENO_verificar_fuente.md).
> Estado: **plan aprobado, listo para ejecutar**. Última actualización: 2026-06-02.

## Resumen ejecutivo

Construir `verificar_fuente` como **tool in-process** con un núcleo `VerificacionEngine`
**MCP-ready**, que valida normas, jurisprudencia, doctrina administrativa y datos registrales
contra portales **oficiales `.gov.co`**, devolviendo siempre **dato real + vigencia/derogación +
origen + confianza por tier**. El agente ReAct lo usa con inteligencia (modelo + detector
determinista + gate + linter) en cualquier escenario: responder preguntas, elaborar documentos
o revisar documentos entrantes.

| Fase | Entrega | Estimado | Valor independiente |
|---|---|---|---|
| F0 | Andamiaje: tablas + seed + config | ~0.5 día | Base de datos lista |
| F1 | MVP: normas + vigencia/derogación | 1-2 días | Vigencia real de leyes/decretos |
| F2 | Jurisprudencia + multi-fuente + cruce norma↔sentencia | 2-3 días | C-/T-/SU- + exequible condicionado |
| F3 | Modo C abierto + doctrina admin (DIAN, supers) | 2-3 días | Cualquier tema con procedencia |
| F4 | Inteligencia ReAct + gate duro + linter enforcement | 1-2 días | Cero citas sin verificar en entregables |

Total F0-F4: **~7-10 días**. Cada fase es desplegable y testeable por sí sola.

---

## Convenciones

- Tests = **directo al backend desplegado** (patrón actual: login pilot `lawyer@pilot.test` →
  POST `/api/chat` SSE; verificación de BD vía Management API).
- Todo contenido externo se envuelve con `_untrusted(...)` (anti prompt-injection).
- Principio **LLM-light**: determinista + cache primero; Haiku (barato) solo para clasificar
  Modo C y extraer texto; el modelo grande del agente NO participa en la verificación.
- Cada tarea tiene casilla `[ ]`; marcar `[x]` al completar.

---

## FASE 0 — Andamiaje y datos

**Objetivo:** dejar BD, seed y config listos para el engine.

### Tareas
- [ ] **F0.1** Migración SQL `supabase/migrations/NNNN_verificar_fuente.sql`:
  - Tabla `autoridades_registry` (KB extensible): `id, entidad, materias text[], tier int,
    dominios text[], tipos text[], plantillas_url jsonb, hints_extraccion text[], activo bool`.
  - Tabla `fuente_cache`: `id, jurisdiccion, clave_normalizada text unique, tipo_fuente,
    estado, record jsonb, confianza numeric, fuentes_urls text[], fecha_consulta timestamptz,
    expires_at timestamptz`.
  - Tabla `verificaciones` (auditoría): `id, org_id, session_id, run_id, consulta, tipo_fuente,
    estado, tier int, confianza numeric, fuentes jsonb, latency_ms int, creditos int, created_at`.
  - RLS: `verificaciones` por `org_id`; `autoridades_registry` y `fuente_cache` globales (lectura).
- [ ] **F0.2** Seed `autoridades_registry` con el mapa verificado (mínimo 20 entradas):
  - Tier 0: Corte Constitucional (relatoría), Corte Suprema, Consejo de Estado, Diario Oficial.
  - Tier 1: DIAN, Superfinanciera, Supersociedades, SIC, CREG, CRC, CRA, Mintrabajo, DAFP,
    Consejo de Estado–Sala de Consulta.
  - Tier 2: Función Pública (Gestor Normativo), SUIN-Juriscol, Secretaría del Senado.
  - Tier 3: RUES, Consulta de Procesos (CPNU), SECOP.
  - Cada entrada con `plantillas_url` y `hints_extraccion` reales (ver Apéndice A).
- [ ] **F0.3** `app/config.py`: settings nuevos — `vf_ttl_vigente=30d`, `vf_ttl_derogada=∞`,
  `vf_ttl_no_encontrada=1d`, `vf_max_fetch=3`, `vf_max_saltos=2`, `vf_allowlist=[*.gov.co]`,
  `vf_fase` (flag para activar fases incrementalmente).
- [ ] **F0.4** Aplicar migración a Supabase (prod) y verificar.

### Criterios de aceptación
- `select count(*) from autoridades_registry` ≥ 20.
- Las 3 tablas existen con RLS correcta.
- Config carga sin error en arranque del backend.

---

## FASE 1 — MVP: normas + vigencia/derogación (casi $0)

**Objetivo:** dado "Ley N de AÑO" / "Decreto N" / "art. N de [código]", devolver estado de
vigencia + derogaciones + origen, usando Gestor Normativo (regex, sin LLM).

### Tareas
- [ ] **F1.1** `app/tools/verificar_fuente.py` — esqueleto del `VerificacionEngine`
  (orquestador) + dataclasses `Consulta`, `VigenciaRecord`, `Procedencia`.
- [ ] **F1.2** `CitationParser` (determinista): regex para `Ley N de AÑO`, `Decreto N de AÑO`,
  `art. N` + diccionario de códigos→ley base (`CGP→Ley 1564/2012`, `C. Civil→Ley 84/1873`,
  `C. Comercio→Decreto 410/1971`, `CPACA→Ley 1437/2011`, `C. Penal→Ley 599/2000`, etc.).
  Salida: `{tipo, numero, anio, articulo?, codigo?, original}`.
- [ ] **F1.3** `AuthorityRegistry` loader (lee `autoridades_registry`, cachea en memoria) +
  selector de autoridad por tipo de consulta.
- [ ] **F1.4** `CacheLayer`: lookup/upsert en `fuente_cache` por `clave_normalizada` con TTL
  adaptativo. Short-circuit en hit.
- [ ] **F1.5** `Locator` (Fase 1: solo Gestor Normativo). Estrategia:
  `web_search(allowed_domains=["funcionpublica.gov.co"])` por "Ley N de AÑO" → toma `norma.php?i=ID`.
  (El `i=ID` es interno; se resuelve por búsqueda dirigida.)
- [ ] **F1.6** `web.py`: añadir `allowed_domains` a `web_search` (Brave acepta `site:`); helper
  `fetch_many(urls)` paralelo con `asyncio.gather` + timeout + 1 retry; reusa `web_cache`.
- [ ] **F1.7** `Extractor` **regex-first**: pre-filtro de secciones ("Resumen de Notas de
  Vigencia", "Notas de Vigencia", "Derogado por", "Modificado por", "INEXEQUIBLE", "rige a
  partir"); produce `VigenciaRecord` con `estado, fecha_promulgacion, derogada_por[],
  modificada_por[], control_constitucional[], soporte_textual`.
- [ ] **F1.8** `ProvenanceBuilder`: arma procedencia (entidad, url, tier, fecha_consulta) +
  `confianza` inicial por tier; persiste en `verificaciones`.
- [ ] **F1.9** `registry.py`: registrar `VERIFICAR_FUENTE_SCHEMA` en `TOOL_SCHEMAS` + branch en
  `execute()` que llama `engine.verificar(consultas, ctx)`; salida envuelta `_untrusted`.
- [ ] **F1.10** Tests directos al backend (script tipo `e2e`): "¿Ley 820/2003 vigente?",
  "¿Ley 56/1985 sigue vigente?" (debe decir derogada por 820), "art. 422 CGP".

### Criterios de aceptación
- 3/3 normas con estado correcto + ≥1 fuente oficial + URL + fecha de consulta.
- Cache frío < 8s; cache hit = 0 red, 0 LLM.
- "Ley 56/1985" reporta derogación por Ley 820/2003.
- `verificaciones` registra cada llamada con latencia y tier.

---

## FASE 2 — Jurisprudencia + multi-fuente + cruce norma↔sentencia

**Objetivo:** resolver sentencias C-/T-/SU- por URL determinista, extraer su efecto (incl.
**exequible condicionado**), reconciliar varias fuentes para normas, y cruzar norma↔sentencia.

### Tareas
- [ ] **F2.1** `CitationParser`: añadir patrones de sentencias — `C-NNN de AAAA`, `C-NNN/AA`,
  `T-NNN...`, `SU-NNN...`, `A-NNN...`; CSJ `SC/SP/SL + consecutivo-año` + radicado; Consejo de
  Estado por radicado. Mapear prefijo→corte.
- [ ] **F2.2** `Locator` jurisprudencia:
  - Corte Constitucional: **plantilla determinista** `corteconstitucional.gov.co/relatoria/AÑO/TIPO-NNN-AA.htm` (sin búsqueda).
  - CSJ / Consejo de Estado: búsqueda dirigida en sus relatorías/aplicativos (multi-salto).
- [ ] **F2.3** `Extractor` jurisprudencial (schema propio): `corte, sala, clase, numero, anio,
  magistrado_ponente, fecha, efecto (exequible|inexequible|exequible_condicionado|inhibitoria|
  estese), norma_afectada{norma,articulo,resultado_vigencia}, ratio_decidendi, resuelve_textual,
  problema_juridico`. **Citar textual el RESUELVE** (anti-holding-alucinado).
- [ ] **F2.4** `Locator`/`Fetcher` normas multi-fuente: añadir SUIN-Juriscol y Secretaría del
  Senado como fuentes secundarias (+ lista SUIN "Depuración Normativa" para cruce de derogadas).
- [ ] **F2.5** `Reconciler`: consenso entre fuentes; pesos por tier (Gestor≈SUIN > Senado);
  `confianza = f(tier, corroboración, recencia)`; `conflicto=true` si difieren; **article-level**
  (estado del artículo citado, no solo de la ley).
- [ ] **F2.6** **Cruce norma↔sentencia**: al verificar una norma, exponer las sentencias C- que
  la afectan; al verificar una sentencia C-, exponer la norma afectada y su `resultado_vigencia`.
- [ ] **F2.7** `Extractor` fallback **Haiku structured output** cuando el regex no alcanza
  (pre-filtro de texto → Haiku con JSON schema → `VigenciaRecord`).
- [ ] **F2.8** `TREATMENT` (tratamiento posterior, best-effort + honesto): buscar el número/
  radicado en sentencias posteriores + línea de la propia corte; marcar `reiterada|posible
  cambio|no detectado` con **confianza reducida** y `advertencia: "aproximado — confirmar"`.
- [ ] **F2.9** Tests: "C-031 de 2019" (exequible, art.421 CGP), "C-670 de 2004" (inexequibilidad
  parcial art.12 Ley 820); cruce: verificar art.12 Ley 820 → trae C-670/2004; un "exequible
  condicionado" real detectado.

### Criterios de aceptación
- C-/T-/SU- resueltas **sin búsqueda** (plantilla determinista) con RESUELVE textual.
- "exequible condicionado" detectado y reportado con su condicionamiento.
- Cruce norma↔sentencia operativo en ambas direcciones.
- Reconciliación marca conflictos y ajusta confianza; tratamiento posterior con caveat.

---

## FASE 3 — Modo C abierto + doctrina administrativa

**Objetivo:** responder temas **sin cita** ("retención servicios 2026", "concepto DIAN sobre X")
buscando en autoridades por tier, con multi-salto hasta el documento primario.

### Tareas
- [ ] **F3.1** `Clasificador` (Haiku) para Modo C: dada una consulta sin cita → `{source_type,
  materia, entidad_probable, estrategia}`. Reglas para casos estructurados (sin LLM).
- [ ] **F3.2** `Locator` Modo C: `web_search` dirigida por **orden de tier** (gov.co/oficial
  primero; amplía solo si no hay); devuelve URLs etiquetadas con tier.
- [ ] **F3.3** `Fetcher` **multi-salto** (≤ `vf_max_saltos`): si la página es índice/buscador/
  enlace a PDF, sigue el rastro hasta el documento primario.
- [ ] **F3.4** Autoridades doctrina: DIAN normograma, Superfinanciera, Supersociedades, SIC,
  CREG/CRC/CRA, Consejo de Estado–Sala de Consulta (extractores con sus `hints`).
- [ ] **F3.5** Datos registrales: RUES (empresa por NIT/nombre), CPNU (proceso por radicado).
- [ ] **F3.6** **Freshness gate**: TTL por estado; datos con periodo (UVT, intereses) marcan
  `desactualizado` si el periodo no cuadra con la fecha actual.
- [ ] **F3.7** Política de honestidad: si solo hay tier ≥4 → `advertencia: "no confirmado
  oficialmente"`, nunca afirma; si nada → `estado=no_encontrada` con candidatas probadas.
- [ ] **F3.8** Tests: "presencia económica significativa DIAN", "UVT 2026", tema novel sin cita,
  empresa por NIT en RUES.

### Criterios de aceptación
- Modo C devuelve dato + procedencia para temas sin cita.
- Multi-salto llega al documento primario (no se queda en el índice).
- Nunca afirma con solo tier ≥4; degrada con honestidad.

---

## FASE 4 — Inteligencia del ReAct + gate duro + linter enforcement

**Objetivo:** que el agente decida bien cuándo usar el tool en cualquier escenario y que ninguna
cita salga sin verificar en los entregables.

### Tareas
- [ ] **F4.1** `system_prompts.py`: instrucción precisa — "antes de AFIRMAR o CITAR norma/
  sentencia/concepto, llama `verificar_fuente`; NO para texto con placeholders sin fundamentos".
  Ejemplos de cuándo sí / cuándo no.
- [ ] **F4.2** **Detector determinista** (`guardrails` o util): regex de referencias legales
  (`Ley N`, `art. N`, `C-NNN`, `Decreto N`, `concepto DIAN`, nombres de entidades) sobre (a) el
  mensaje del usuario y (b) el borrador del modelo → señal para disparar/forzar.
- [ ] **F4.3** **Gate research-before-draft** en `runner.py`: si el turno producirá un entregable
  que cita (reusa `_intends_document` + detector) y aún no hay verificación → intercepta y exige
  `verificar_fuente` antes de `render_document_code`.
- [ ] **F4.4** **Batch**: agrupar todas las citas detectadas de un turno/documento en **una**
  llamada al tool. Reuso de cache de sesión.
- [ ] **F4.5** `guardrails.lint_citations` → **enforcement**: cruzar cada cita detectada contra
  los `VigenciaRecord` de la sesión; verificada → `[VERIFICADO: fuente, fecha]`; sin registro →
  `[verificar contra fuente primaria]` + nota del revisor.
- [ ] **F4.6** `render_document_code`/generadores: inyectar las marcas `[VERIFICADO …]` /
  `[verificar]` en el documento final junto a cada cita.
- [ ] **F4.7** Evento SSE `verify_progress` ("Verificando art. 422 CGP…") durante la ejecución.
- [ ] **F4.8** Tests e2e de los 4 flujos:
  - Pregunta ("¿Ley 820 vigente?") → responde con procedencia.
  - Demanda (gate) → todas las citas verificadas en el .docx.
  - Revisión de doc entrante → informe de citas vigentes/derogadas.
  - Poder con placeholders → NO dispara verificación (0 costo).

### Criterios de aceptación
- Entregables formales: 100% de citas con `[VERIFICADO]` o marcadas `[verificar]`.
- Poder/carta sin fundamentos: 0 llamadas a `verificar_fuente`.
- En el set de prueba, el modelo decide correctamente (usar/no usar) en ≥90% de casos.
- El usuario ve `verify_progress` en vivo.

---

## Observabilidad y métricas (transversal)
- [ ] Dashboard/queries sobre `verificaciones`: latencia p50/p95, tasa cache-hit, % no_encontrada,
  distribución por tier, créditos consumidos (Brave/Firecrawl), Haiku tokens.
- [ ] Log de `guardrail_events` rule `norma_vigencia` para auditoría.

## Riesgos y mitigaciones
| Riesgo | Mitigación |
|---|---|
| Portal oficial caído | usar fuentes restantes; bajar confianza; servir de cache |
| Secretaría Senado HTTP intermitente | fallback a SUIN/Gestor Normativo |
| Sin créditos Firecrawl | servir de `fuente_cache`; si no, `indeterminado` honesto |
| Cambio de layout de un portal | extractor Haiku fallback + alerta en métricas |
| Sobre-costo por exceso de llamadas | detector + batch + cache + no-disparo sin señal legal |
| No hay citador oficial CO | tratamiento posterior marcado "aproximado — confirmar" |

## Fase 5 (opcional, futura) — Fachada MCP
- [ ] Envolver `VerificacionEngine` como servidor MCP remoto (mismo núcleo) si aparece 2º
  consumidor (claude.ai / Claude Desktop) o se productiza el connector colombiano.

---

## Apéndice A — Seed inicial del Registro de Autoridades (verificado)

| entidad | tier | dominios | tipos | plantilla_url / acceso |
|---|---|---|---|---|
| Corte Constitucional | 0 | corteconstitucional.gov.co | sentencia C/T/SU/A | `/relatoria/{anio}/{C\|T\|SU\|A}-{num}-{aa}.htm` ✅ |
| Corte Suprema de Justicia | 0 | cortesuprema.gov.co, consultajurisprudencial.ramajudicial.gov.co | casación/tutela | aplicativo de consulta (sala, radicado) |
| Consejo de Estado | 0 | consejodeestado.gov.co, samai.consejodeestado.gov.co | providencias/conceptos | buscador + SAMAI (≥dic-2021) |
| Diario Oficial | 0 | imprenta.gov.co | publicación oficial | buscador 1864–presente |
| Gestor Normativo (Función Pública) | 2 | funcionpublica.gov.co | ley/decreto/concepto/sentencia | `/eva/gestornormativo/norma.php?i={id}` ✅ |
| SUIN-Juriscol | 2 | suin-juriscol.gov.co | ley/decreto/jurisprudencia | buscador + Depuración Normativa (derogadas) |
| Secretaría del Senado | 2 | secretariasenado.gov.co | ley/código/sentencia | `/senado/basedoc/ley_{num}_{anio}.html` |
| DIAN | 1 | normograma.dian.gov.co | concepto/oficio/resolución | búsqueda avanzada por materia |
| Superfinanciera | 1 | superfinanciera.gov.co | circular/concepto | Circular Básica Jurídica |
| Supersociedades | 1 | supersociedades.gov.co | circular/concepto/oficio | Circular Básica Jurídica interactiva |
| SIC | 1 | sic.gov.co | circular/concepto | normograma |
| CREG | 1 | gestornormativo.creg.gov.co | resolución | gestor "Alejandría" |
| CRC | 1 | crcom.gov.co | resolución | compilación (Res. 5050/2016) |
| CRA | 1 | normas.cra.gov.co | resolución | `/gestor/...` |
| Consejo de Estado–Sala de Consulta | 1 | consejodeestado.gov.co | concepto | por número de concepto |
| RUES | 3 | rues.org.co | registro empresa | por NIT/nombre/matrícula |
| Consulta de Procesos (CPNU) | 3 | consultaprocesos.ramajudicial.gov.co | estado de proceso | por radicado (23 dígitos) |
| SECOP | 3 | colombiacompra.gov.co | contratación | buscador público |

## Apéndice B — Cómo funciona el precedente (referencia para el extractor)
- **Doctrina probable** (art. 4 Ley 169/1896): 3 decisiones uniformes CSJ; apartarse exige carga argumentativa (C-836/2001).
- **Precedente constitucional**: ratio vinculante; resolutiva de C- con efecto erga omnes y cosa juzgada (art. 243 C.P.).
- **Unificación**: SU- (C. Constitucional) y unificación del Consejo de Estado (arts. 270-271 CPACA, extensión de jurisprudencia).
- **Efectos de fallo C-**: exequible / inexequible (sale del ordenamiento) / **exequible condicionado** (sobrevive solo con la interpretación fijada) / inexequibilidad diferida.
