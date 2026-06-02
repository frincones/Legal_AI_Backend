# Diseño — `verificar_fuente`: motor de verificación/grounding legal (Colombia)

> Estado: **diseño aprobado, sin implementar**. Documento vivo.
> Autor: equipo Legal AI · Última actualización: 2026-06-02
> **Plan de ejecución detallado (F0-F4):** ver [`PLAN_verificar_fuente.md`](./PLAN_verificar_fuente.md)

## 1. Objetivo

Dar al agente la capacidad de **validar a fondo cualquier tema jurídico** que un abogado
necesite confirmar —no solo leyes/jurisprudencia, sino doctrina administrativa (DIAN,
súperintendencias), regulación sectorial (CREG, SIC, CRC), datos registrales (RUES, Rama
Judicial) y preguntas puntuales sin cita— **obteniendo el dato real y su ORIGEN**, con
honestidad explícita sobre qué tan autoritativa es la fuente.

Convierte el producto de "redactor con disclaimer" en "asistente legal **verificable**".

### Por qué lo construimos nosotros
Claude for legal resuelve esto **delegando en conectores autoritativos** (CourtListener,
Westlaw/CoCounsel, Trellis, Descrybe, Midpage, Legal Data Hunter) y **etiquetando la
procedencia** (cita verificada con `[source]` vs. solo-modelo con `[verify]`). **Todos esos
conectores son US-céntricos.** Para Colombia **no existe** ese conector: probamos Legal Data
Hunter y no expone vigencia estructurada (`status/effective_date/expiry_date` = NULL) y
`resolve_reference` falla (0/3 en normas CO). Los portales `.gov.co` **sí** publican vigencia y
derogaciones. Por tanto: **nosotros construimos el conector colombiano que Claude no tiene.**

## 2. Decisión de arquitectura: Tool vs MCP

**Conclusión: tool in-process AHORA, con un núcleo agnóstico al transporte (MCP-ready).**

| Criterio | Tool in-process | MCP server |
|---|---|---|
| Latencia | en proceso (~0 overhead) | +RTT de red por llamada |
| Reúso entre clientes | solo nuestra app | cualquier cliente MCP |
| Infra extra | ninguna (reúsa web/ db/ cache/ Haiku) | otro servicio (deploy/auth/secretos) |
| Cuándo gana | pocas tools, 1 app, latencia sensible | 10+ tools, multi-consumidor, multi-modelo |

- **Hoy:** 1 solo consumidor (nuestro agente), latencia sensible (varios fetches por verificación),
  reúsa toda la infraestructura existente ⇒ **tool in-process**.
- **Diseño "mejor" (Anthropic):** híbrido — function calling para tools de la app; MCP para
  infraestructura compartida. Por eso el núcleo es un **`VerificacionEngine`** independiente del
  transporte, con dos fachadas finas:
  - **Fachada tool** (hoy): `registry.execute("verificar_fuente", …)` → 0 hops.
  - **Fachada MCP** (después): servidor MCP remoto que llama el mismo engine.
- **El MCP se justifica** cuando aparezca un 2º consumidor (claude.ai, Claude Desktop, otros
  agentes) o si productizamos "el connector legal colombiano" (formato `.mcp.json` de
  claude-for-legal). Migrar cuesta poco porque solo se envuelve el engine.

```
        ┌─────────────────────────────────────────┐
        │         VerificacionEngine (núcleo)      │
        │  clasificar · localizar · fetch · extraer│
        │  · reconciliar · procedencia · cache     │
        └───────────────┬───────────────┬──────────┘
            (HOY) fachada tool   (DESPUÉS) fachada MCP
```

## 3. Taxonomía de fuentes + niveles de autoridad (tiers)

La confianza se deriva del tier. **Nunca se afirma como verificado algo de tier ≥ 4.**

| Tier | Qué es | Ejemplos CO | Uso |
|---|---|---|---|
| 0 Primaria oficial | emisor con potestad | Diario Oficial, Congreso, Presidencia, Cortes | verdad legal |
| 1 Doctrina/regulación oficial | autoridad admin. emitiendo su acto | DIAN, Superfinanciera, Supersociedades, SIC, CREG, CRC, Mintrabajo, DAFP | autoritativa en su materia |
| 2 Compiladores oficiales | reproducen normas | Función Pública (Gestor Normativo), SUIN-Juriscol, Secretaría Senado | vigencia/derogación |
| 3 Registros/datos | bases con dato verificable | RUES/Confecámaras, Rama Judicial, SECOP, RUNT | dato registral |
| 4 Secundaria reputada | seria, no oficial | Ámbito Jurídico, universidades, gremios | contexto/pista |
| 5 Web general | cualquier cosa | blogs, prensa | solo pista |

## 4. Registro de Autoridades (config extensible = robustez sin tocar código)

Tabla/JSON de conocimiento. Agregar una fila ⇒ nueva fuente cubierta. Esquema por entrada:

```jsonc
{
  "entidad": "DIAN",
  "materias": ["tributario","aduanero","cambiario","retención","IVA","UVT","factura electrónica"],
  "tier": 1,
  "dominios": ["dian.gov.co","normograma.dian.gov.co"],
  "tipos": ["concepto","resolución","circular","oficio"],
  "plantillas_url": { "concepto": "https://normograma.dian.gov.co/...{num}_{anio}...",
                       "buscador": "https://www.dian.gov.co/normatividad/..." },
  "hints_extraccion": ["Tesis jurídica","Problema jurídico","Vigencia","Deroga el oficio"]
}
```

### Seed inicial (autoridades CO a sembrar)
- **Normas/vigencia (tier 2):** Función Pública – Gestor Normativo, SUIN-Juriscol, Secretaría del Senado.
- **Primaria (tier 0):** Diario Oficial / Imprenta Nacional, Gaceta del Congreso, Presidencia (decretos).
- **Cortes (tier 0):** Corte Constitucional (relatoría), Corte Suprema, Consejo de Estado.
- **Doctrina/regulación (tier 1):** DIAN, Superfinanciera, Supersociedades, Superservicios, SIC, CREG, CRC, ANLA, Mintrabajo, Minhacienda, DAFP, Secretaría Jurídica de Presidencia.
- **Registros (tier 3):** RUES/Confecámaras, Rama Judicial (consulta de procesos), SECOP, RUNT.

## 5. Modos de operación

```
MODO A — Cita estructurada   "Ley 820/2003", "Concepto DIAN 1234 de 2024", "C-031/19"
MODO B — Entidad/dato         "circular SuperFin 029", "estado del RUT de NIT X"
MODO C — Tema abierto         "retención en la fuente por servicios 2026", "¿cambió el UVT?"
```

## 6. Pipeline interno (engine)

```
verificar_fuente(consultas[])
  ① CLASIFICAR (reglas para citas; Haiku solo para Modo C)
       → {source_type, materia, entidad_probable, estrategia, num, anio, articulo?}
  ② CACHE  (clave = consulta normalizada; TTL adaptativo)
       hit → return
  ③ LOCALIZAR
       a. plantillas del registro (Tier 0-3, sin red)
       b. fallback: búsqueda dirigida con allowed_domains por tier (gov.co primero)
       → URLs candidatas etiquetadas con tier
  ④ FETCH MULTI-SALTO (paralelo, bounded ≤2 saltos)
       sigue índices/buscadores/PDF hasta el documento primario. Cacheado.
  ⑤ EXTRAER (pre-filtro regex + Haiku structured output)
       dato/respuesta, tipo_acto, emisor, número, fecha, vigencia/derogación,
       y la CITA TEXTUAL que lo soporta
  ⑥ RECONCILIAR + PROCEDENCIA
       ≥2 fuentes si es posible · pesos por autoridad · confianza=f(tier,corrob,recencia)
       · marca conflicto · article-level · SIEMPRE adjunta origen
  → persistir (cache + auditoría) + evento SSE + <untrusted> resultado </untrusted>
```

Principio **LLM-light**: determinista y cacheado primero; Haiku (modelo barato) solo para
clasificar Modo C y extraer texto; el modelo grande del agente **no** participa en la verificación.

### Enrutamiento por tipo de cita
| Tipo | Portal primario | Secundario(s) | Extrae |
|---|---|---|---|
| Ley/Decreto N | Función Pública | Secretaría Senado, SUIN | vigencia, derogaciones, inexequibilidad |
| Art. de Código (CGP, C.C., C.Co.) | resolver código→ley → Función Pública | Secretaría Senado | estado del artículo + de la ley |
| Sentencia C-/T-/SU- | Corte Constitucional | — | efecto (exequible/inexequible), tesis, fecha |
| Concepto/Circular DIAN/SuperX | normograma de la entidad | buscador de la entidad | tesis, vigencia, deroga oficios |
| Tema sin cita | búsqueda dirigida (gov.co) | — | desambiguar → reintentar pipeline |

## 7. Modelo de datos (salida por consulta)

```json
{
  "consulta": "presencia económica significativa DIAN",
  "tipo_fuente": "norma|jurisprudencia|doctrina_admin|regulacion|registro|tema_abierto",
  "entidad_emisora": "DIAN",
  "respuesta": "…dato/estado real extraído…",
  "soporte_textual": "cita textual que lo prueba",
  "documento": {"tipo":"Concepto Unificado","numero":"…","fecha":"…","estado":"vigente|derogado|inexequible_parcial|…"},
  "procedencia": [{"entidad":"DIAN","url":"…","tier":1,"oficial":true,"fecha_publicacion":"…","fecha_consulta":"2026-06-02"}],
  "vigencia": {"estado":"vigente","deroga":["Oficio X"],"derogado_por":[],"control_constitucional":[]},
  "confianza": 0.85,
  "nivel_autoridad": "tier_1_oficial",
  "corroborado_por": 2,
  "conflicto": false,
  "advertencia": null,
  "saltos_realizados": 1
}
```

### Tablas nuevas (Supabase)
- `autoridades_registry` — KB extensible (sección 4).
- `fuente_cache` — `clave_normalizada` uniq, `record jsonb`, `estado`, `confianza`, `expires_at`, `fuentes_urls`. TTL adaptativo: derogada/inexequible → largo; vigente → ~30d; no_encontrada → ~1d.
- `verificaciones` — auditoría: `org_id, session_id, run_id, consulta, tipo_fuente, estado, tier, confianza, fuentes, latency_ms, creditos, created_at`.

## 8. Adopción de patrones probados de Claude for legal

- **Procedencia obligatoria + flag `[verify]` como gate DURO** (principio Eve: "una alucinación
  con tono autoritativo es peor que no responder"). El citation linter pasa de *avisar* a *exigir*
  el flag verificado.
- **Citations API de Anthropic** para respuestas ancladas en documentos del caso (grounding
  char-level cuando el doc está en contexto).
- **Web search tool nativa de Anthropic** (citaciones integradas + `allowed_domains` + agéntica)
  como motor preferente del **Modo C**; Brave/Firecrawl quedan para portales que la nativa no rinde.
- **Freshness gate**: cada veredicto de vigencia caduca (TTL) y se re-valida.
- **Practice profile** (jurisdicción CO, fuentes por defecto, estándar de citación) — extiende la
  inyección de perfil ya existente.

## 9. Integración con el código (puntos de cambio)

| Archivo | Cambio |
|---|---|
| `app/tools/verificar_fuente.py` (nuevo) | `VerificacionEngine` + `VERIFICAR_FUENTE_SCHEMA` + parser + registro + extractor + reconciliador |
| `app/tools/web.py` | `web_search` acepta `allowed_domains`; helper `fetch_many()` paralelo; multi-salto |
| `app/tools/registry.py` | registrar schema + branch en `execute()` (fachada tool) |
| `app/agent/guardrails.py` | linter de citas: de *avisar* → **exigir** flag verificado; gate "research-before-draft" |
| `app/agent/system_prompts.py` | instrucción: antes de **citar** normas/doctrina, llamar `verificar_fuente` |
| `app/agent/runner.py` | (opcional) Citations API + web search tool nativa para grounding |
| migración SQL | `autoridades_registry`, `fuente_cache`, `verificaciones` |
| `app/config.py` | TTLs, caps de fetch/saltos, allowlist de dominios, flags de modo |

### Contrato de la tool
```json
{
  "name": "verificar_fuente",
  "description": "Verifica a fondo cualquier fuente jurídica colombiana (normas, jurisprudencia, doctrina DIAN/súperintendencias, regulación, registros) contra portales OFICIALES y devuelve el dato real + su origen + vigencia. Úsala ANTES de citar o afirmar cualquier norma/doctrina.",
  "input_schema": {
    "consultas": ["string"],
    "jurisdiccion": "CO",
    "nivel_detalle": "norma|articulo"
  }
}
```

## 10. Costo (LLM-light)

| Escenario | LLM (Anthropic) | Créditos externos |
|---|---|---|
| Cache hit | 0 | 0 |
| Cita estructurada (reglas + regex) | 0 (Haiku solo fallback) | 1-2 Firecrawl |
| Tema abierto Modo C | ~2 Haiku (~8k tok) | 1 Brave + 2-3 Firecrawl (o web search nativa) |

El modelo grande del agente no participa en la verificación. Caché de 2 niveles
(`web_cache` HTML→md + `fuente_cache` veredicto) minimiza repetición.

## 11. Seguridad y modos de fallo

- Todo contenido externo se envuelve con `_untrusted(...)`; el extractor Haiku se instruye a
  **ignorar** instrucciones embebidas (anti prompt-injection). Allowlist solo `.gov.co` oficial en `locate`.

| Caso | Comportamiento |
|---|---|
| Portal caído | usa fuentes restantes; baja confianza |
| No encontrado en ningún portal | `estado=no_encontrada` → el agente marca `[no verificado]`, no afirma |
| Conflicto entre fuentes | `conflicto=true`, gana mayor autoridad, confianza < 0.6 |
| Sin créditos Firecrawl | sirve de `fuente_cache` si existe; si no, `indeterminado` honesto |
| Solo tier ≥4 | devuelve pista + advertencia "no confirmado oficialmente" |

## 12. Roadmap por fases

- **Fase 1 — MVP (tool, casi $0):** parser de citas estructuradas + Función Pública como fuente
  única + extracción regex + cache + procedencia/flag. Cubre Ley/Decreto vigencia/derogación.
- **Fase 2 — Multi-fuente + Cortes:** añade SUIN, Secretaría Senado, Corte Constitucional;
  reconciliación con consenso y confianza por tier; article-level.
- **Fase 3 — Modo C abierto + doctrina admin:** web search nativa con `allowed_domains`,
  multi-salto, DIAN/súperintendencias/CREG; freshness gate.
- **Fase 4 — Gate duro + Citations API:** linter exige flag verificado; research-before-draft;
  grounding char-level de documentos del caso.
- **Fase 5 (opcional) — Fachada MCP:** envolver el engine como servidor MCP remoto si aparece
  2º consumidor o se productiza el connector colombiano.

## 13. Referencias

- Web search tool — Claude API Docs: https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool
- MCP connector — Claude API Docs: https://platform.claude.com/docs/en/agents-and-tools/mcp-connector
- Citations — Claude API Docs: https://platform.claude.com/docs/en/build-with-claude/citations
- Code execution with MCP (Anthropic Engineering): https://www.anthropic.com/engineering/code-execution-with-mcp
- Claude for the legal industry: https://claude.com/blog/claude-for-the-legal-industry
- anthropics/claude-for-legal: https://github.com/anthropics/claude-for-legal
