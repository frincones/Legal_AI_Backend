# Plan — Reducción de costo de tokens sin impacto en calidad

> Objetivo: bajar el costo por chat **sin cambiar el orquestador, el modelo de documento, ni
> degradar la calidad** actual. Cubre TODOS los casos de uso, no solo la creación de documentos.
> Estado: **plan aprobado, listo para ejecutar**. Última actualización: 2026-06-03.

## Principio rector

Hay un costo **irreducible**: el **output del orquestador** (el docx-js de un documento, el texto
de una respuesta). Eso ES el entregable → no se toca. **Atacamos el OVERHEAD**: contexto
re-enviado sin caché (system, historial, adjuntos), llamadas LLM redundantes y trabajo repetido.
Ninguna palanca cambia el contenido ni el modelo del entregable.

Solo hay **3 call-sites de LLM** (verificado en código):
1. `router.py:41` — Haiku, 1×/chat.
2. `runner.py:229` — **orquestador** Sonnet/tier, hasta 5 iteraciones/chat (driver principal).
3. `verificar_fuente.py:145` — Haiku, N× por consulta.
`codedoc.build` (E2B), embeddings (fastembed local) y RAG/web **no usan LLM**.

---

## 1. Taxonomía de casos de uso y su driver de costo

| Caso de uso | Driver de costo dominante | Código |
|---|---|---|
| **A. Crear documento** (poder, demanda, contrato) | OUTPUT Sonnet (docx-js 16-19k tok) | loop → `render_document_code` |
| **B. Pregunta / validación legal** (vigencia, jurisprudencia, concepto) | Haiku de `verificar_fuente` (N×) + Brave/Firecrawl; respuesta chica | `verificar_fuente.py` |
| **C. Revisar / analizar documento adjunto** | **INPUT grande** (adjunto) re-enviado sin caché | `_attachment_context` `runner.py:60-73` |
| **D. RAG sobre docs de la firma** | INPUT (chunks) inyectados | `search_documents` + loop |
| **E. Conversación multi-turno** | **Historial completo re-enviado cada turno** (crece ~cuadrático) | `_load_history` `runner.py:76-87` |
| **F. Charla general / aclaración** | Mínimo (worker Haiku, sin tools) | router → haiku |
| **G. Revisión de citas de un documento entrante** | INPUT (doc) + Haiku de vf (batch) | `_attachment_context` + `verificar_fuente` |
| **H. Watchers de plazos** (background, no interactivo) | Batch | `admin.py` deadline_watcher |
| **I. Ingesta / embeddings de adjuntos** | $0 LLM (fastembed local) | `ingest/`, `embeddings.py` |

**Lectura clave:** el driver **cambia por caso**. Documentos = OUTPUT. Preguntas = Haiku de
verificación. Reviews/multi-turno = **INPUT re-enviado**. Por eso una sola palanca no basta:
necesitamos una combinación, con el **caching** como eje transversal.

---

## 2. Las palancas (6) y a qué casos aplican

| # | Palanca | A doc | B preg | C/G review | D RAG | E multi-turno | F charla | H watchers |
|---|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| 1 | **Caching total** (system + convo + adjunto) | ✅ | ✅ | ✅✅ | ✅ | ✅✅ | ✅ | — |
| 2 | **Trim Haiku de verificar_fuente** | ✅ | ✅✅ | ✅ | — | — | — | — |
| 3 | **Instrumentación de costo exacto** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 4 | **Sub-tareas (router+vf) → Gemini Flash-Lite** | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ |
| 5 | **Caché semántica de respuestas factuales** | — | ✅✅ | — | ✅ | — | ✅ | — |
| 6 | **Compactación de historial** (ventana + resumen) | — | — | ✅ | ✅ | ✅✅ | — | — |
| — | **Batch API 50% off** | — | — | — | — | — | — | ✅✅ |

➡️ **Caching (1) es el lever universal**: todo chat que re-envía contexto (iteraciones, adjuntos,
historial) se beneficia. Es donde están los costos de los casos NO-documento.

---

## 3. Detalle por palanca

### 🟢 Palanca 1 — Caching total del prompt (riesgo: CERO)
**Problema:** el orquestador reenvía `convo` completo cada iteración (`runner.py:208,260,293`); el
system solo se cachea cuando hay skill; el adjunto (`_attachment_context`) y el historial
(`_load_history`) van sin `cache_control` → se re-pagan a precio completo ($3/M) en vez de
cache-read ($0.30/M).

**Cambios (en `runner.py`, quirúrgicos):**
1. **System siempre cacheado:** tras armar `system_blocks`, `system_blocks[-1]["cache_control"] = {"type":"ephemeral"}`.
2. **Conversación + adjunto cacheados (rolling breakpoint):** antes de cada `messages.stream`,
   marcar el último bloque del último mensaje de `convo` con `cache_control`. Anthropic cachea el
   prefijo más largo → iteraciones 2-3, adjuntos y turnos previos se leen a 1/10.
3. (El adjunto, al ir dentro del primer mensaje del convo, queda cubierto por el breakpoint del
   historial automáticamente.)

**Presupuesto de breakpoints:** tools(1) + system(1) + conversación(1) = 3 de 4. OK.

**Ahorro por caso:**
- Documento (3 iter): re-paga ~30k tok → cache → **~$0.14/doc**.
- Review con adjunto (~13k tok): **~$0.07/review**.
- Multi-turno largo: convierte el crecimiento cuadrático en lineal acotado → **el mayor ahorro
  relativo** en usuarios enganchados.

**Validación calidad:** mismo contenido, mismo modelo → output **idéntico**. Gate: e2e 7/7 + doc
válido + respuestas idénticas antes/después.

### 🟢 Palanca 2 — Trim de llamadas Haiku de `verificar_fuente` (riesgo: bajo)
**Problema:** `_verificar_norma` corre Haiku extract en las **3 fuentes SIEMPRE**; un batch de 6
artículos ≈ 12-18 Haiku.
**Cambio:** extraer de la **fuente de mayor tier primero (1 Haiku)**; abanicar a las otras solo si
`no_encontrada` o confianza baja. Conserva corroboración **cuando aporta**.
**Ahorro:** ~⅔ menos Haiku en vf (de ~$0.16 a ~$0.06 en 5 chats). `estado` no cambia.
**Validación:** casos A/B/C/D del e2e → mismos estados; confianza ≥ 0.85 en unifuente.

### 🟢 Palanca 3 — Instrumentación de costo exacto (riesgo: CERO)
Hoy `token_ledger` no registra router ni Haiku de vf → costos **estimados**. Registrar tokens de
ambas llamadas (campo en `verificaciones` + log del router). Convierte estimación en medición y
habilita optimizar con datos reales. (Prerequisito de medición, no ahorra por sí solo.)

### 🟡 Palanca 4 — Sub-tareas (router + extracción vf) → Gemini Flash-Lite / GPT-5 Nano (riesgo: bajo)
Solo las 2 llamadas Haiku, **nunca el orquestador**. Structured output trivial sobre texto filtrado.
**Cómo:** LiteLLM gateway → esas 2 llamadas a `gemini-2.5-flash-lite` ($0.10/$0.40) o
`gpt-5-nano` ($0.05/$0.40), 10-20× más barato que Haiku. Orquestador sigue en Claude.
**Ahorro:** porción Haiku (~$0.06 tras P2) → ~$0.006. Modesto, pero da portabilidad + tracking.
**Validación:** e2e — mismos campos (estado/efecto/RESUELVE). Si Gemini falla un caso → fallback a
Haiku por-tarea (config).

### 🟡 Palanca 5 — Caché semántica de respuestas factuales (mayor ROI futuro, requiere criterio)
**Qué:** antes del worker loop, embeber la pregunta (fastembed local, $0) y buscar en `respuestas_cache`
(pgvector) una respuesta previa **de la misma org** con similitud ≥ 0.95. Hit fresco → responde sin LLM.
**Ahorro:** preguntas repetidas → $0 LLM (hit rates ~68%). Lever de mayor ROI a escala.
**Riesgo (por eso 🟡):** una respuesta legal puede quedar desactualizada. **Mitigación:** umbral
≥0.95, TTL corto (7 días), y **solo cachear respuestas ya verificadas y estables** ("Ley X vigente").
Se limita a **lookups factuales**, no a redacción ni análisis con matiz.

### 🟡 Palanca 6 — Compactación de historial (riesgo: bajo, solo no-documentos)
**Qué:** para conversaciones largas, enviar **ventana móvil** (últimos N turnos) + **resumen** de los
anteriores (generado una vez con Haiku y guardado en `chat_sessions`). En vez de re-enviar 20 turnos
crudos, se manda resumen + recientes.
**Ahorro:** corta el input de conversaciones largas de cuadrático a lineal acotado.
**Aplica a:** E (multi-turno) y reviews largos. **No** a la creación puntual de un documento.
**Validación:** comparar respuestas con/ sin compactación en conversaciones de ≥10 turnos → coherencia
mantenida (el resumen preserva hechos clave).

### 🟢 Batch API (watchers/TTL) — 50% off (riesgo: CERO)
Trabajo no interactivo (watchers de plazos, re-verificación de vigencias por TTL, embeddings masivos)
→ Anthropic **Message Batches API = 50% descuento**. No afecta UX.

---

## 4. Lo que NO se toca (preserva calidad)
- El **orquestador (Sonnet)**: max_tokens, extended thinking, tool-use, streaming.
- El **output del docx-js** (el entregable; piso de costo).
- El **modelo de documento** (re-abriría el riesgo docx-js ya cerrado).
- El **contenido** de respuestas/documentos (todas las palancas preservan el contenido).

---

## 5. Proyección apilada (sobre ~$35/mes/abogado en uso intensivo)

| Etapa | Palancas | Costo/mes | Riesgo |
|---|---|---|---|
| Hoy | — | $35 | — |
| + P1 caching total (transversal) | 1 | ~$27 | **cero** |
| + P2 trim vf Haiku | 2 | ~$24 | bajo |
| + P3 instrumentación | 3 | ~$24 (mide) | cero |
| + Batch watchers | — | ~$23 | cero |
| + P6 compactación historial | 6 | ~$20 | bajo |
| + P5 caché semántica factual | 5 | ~$16 | scoped |
| + P4 gateway sub-tareas | 4 | **~$15** | bajo |

➡️ **De $35 a ~$15/mes (−57%)**, orquestador + documentos **intactos**.
**Tier riesgo-cero/bajo (P1+P2+P3+Batch) = $35→$23 (−34%)** sin tocar nada de calidad.

---

## 6. Roadmap por fases

- **Fase A — riesgo cero/bajo (implementar ya):** P1 (caching total: system+convo+adjunto), P2
  (trim vf Haiku), P3 (instrumentación). Validar con e2e 7/7 + regeneración de demanda/poder.
- **Fase B — operativa:** Batch API para watchers + P6 (compactación de historial).
- **Fase C — con criterio:** P5 (caché semántica factual, umbral alto + TTL).
- **Fase D — opcional:** P4 (LiteLLM gateway + sub-tareas a Gemini Flash-Lite), si se quiere
  portabilidad y el ahorro extra.

## 7. Gate de aceptación (probar que NO baja la calidad)
1. **e2e 7/7** (vigencia, derogada, artículo, jurisprudencia, demanda-gate, poder-sin-gate, modo C)
   → mismos `estado/efecto/procedencia`.
2. **Regenerar demanda + poder** → DOCX válidos, mismos párrafos, marca `[VERIFICADO]`.
3. **Respuestas factuales** antes/después (P1-P3) → idénticas.
4. **Conversación de ≥10 turnos** (P6) → coherencia mantenida.
5. **`token_ledger` + `verificaciones` instrumentados** → baja real de tokens medida, no estimada.

## 8. Métricas a observar post-implementación
- cache hit-rate (cache_read / total input) por tipo de chat.
- Haiku de vf por verificación (debe bajar ~⅔ con P2).
- hit-rate de `fuente_cache` y `respuestas_cache`.
- costo medido por tipo de chat (A-H) en `token_ledger`/`verificaciones`.
