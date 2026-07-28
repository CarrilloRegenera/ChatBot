# Subagentes de Claude Code

Subagentes especializados versionados en `.claude/agents/`. Son **herramientas de desarrollo**: ayudan a diagnosticar y auditar este repositorio. **No forman parte del runtime del chatbot** y no afectan a las respuestas que recibe el usuario final.

## Origen y licencia

Adaptados del catálogo [agency-agents](https://github.com/msitarzewski/agency-agents) (licencia MIT, © 2025 AgentLand Contributors).

No son copias literales. Los originales usan un frontmatter incompatible con Claude Code (`name` con espacios, `color` en hexadecimal, campos `emoji` y `vibe`) y ocupan 20-35 KB de texto genérico. Cada agente aquí está reescrito en español, recortado al método esencial y anclado a los ficheros y constantes reales de este repositorio.

## Agentes disponibles

| Agente | Cuándo invocarlo |
|---|---|
| `rag-pipeline-engineer` | Una pregunta no recupera el chunk correcto. O quieres evaluar un cambio de chunking / embeddings / top-K / reranking antes de implementarlo. |
| `search-relevance-engineer` | Falla la recuperación por término exacto (`ITC-BT-24`, `IT 3.3`, códigos, valores de tabla). O hay que revisar el índice de Azure AI Search. |
| `model-qa-auditor` | Quieres saber si los pesos del scoring, los umbrales de confianza y el arnés de evaluación están justificados por evidencia o son tuning a ojo. |

Ejemplo de invocación:

> Usa el subagente `rag-pipeline-engineer` para diagnosticar por qué la pregunta "sección mínima del conductor de protección" devuelve confianza baja.

## Restricciones comunes

Los tres comparten las mismas reglas, deliberadamente:

- **Solo lectura.** `tools: Read, Grep, Glob, Bash`. Sin `Write` ni `Edit`: diagnostican y proponen, no modifican código. `Bash` está disponible únicamente para ejecutar tests y evaluación.
- **Modelo: `sonnet`** (Sonnet 5), fijado en el frontmatter. Son tareas de lectura y medición sobre ficheros concretos, no de diseño: Sonnet 5 las cubre con coste y latencia menores.
- **Evidencia obligatoria.** Todo hallazgo cita `fichero:línea` y se etiqueta como `MEDIDO` o `HIPÓTESIS`.
- **Baseline antes de propuesta.** Deben ejecutar el arnés de evaluación y anotar las métricas actuales antes de recomendar un cambio.
- **`CLAUDE.md` manda.** Cambio mínimo y quirúrgico. Si un agente propone un rediseño, se ignora la propuesta.

## Arnés de evaluación que usan

- Dataset: `src/backend/tests/golden_questions.json`
- Runner: `scripts/evaluate_golden_retrieval.py`
- Métricas del runner: tasa de recuperación vacía, acierto de dominio, cobertura de frases esperadas y latencia p50/p95. El golden set no etiqueta chunks esperados, así que no mide recall/precision por chunk ni fidelidad de respuesta.

```bash
python scripts/evaluate_golden_retrieval.py --backend chroma
```

```bash
python scripts/evaluate_golden_retrieval.py --backend azure_search
```

```bash
pytest src/backend/tests -q
```

## Criterio de permanencia

Un agente se mantiene solo si produce **al menos un hallazgo accionable que no estuviera ya** en `docs/auditoria_arquitectura_rag.md` (ni en `docs/auditoria_tecnica_rag_2026-06-25.md`, que no está versionado y solo existe en local). Si no pasa esa prueba, se borra: no acumulamos prompts por moda.

Estado de las pruebas:

| Agente | Estado | Resultado |
|---|---|---|
| `model-qa-auditor` | **PASA** | Auditoría del arnés de evaluación: 5 hallazgos ALTA no documentados previamente. Cuatro verificados a mano (`rag_evaluator.py:74-101`, `:104-111`, `azure_rag_service.py:1097`, `chat_technical_response_service.py:46-110`). Se mantiene. |
| `rag-pipeline-engineer` | **PASA** | Encargo: ¿justifica un cross-encoder? Concluyó **no justificado** y aportó un hallazgo colateral verificable: las llamadas a `collection.query()` no piden `distances`, así que la similitud que Chroma ya calculó se descarta y el "rerank" reencodea query + candidatos con el mismo bi-encoder. A/B medido: `ENABLE_RERANK` on/off da métricas idénticas y ~24x de latencia (p50 4980 ms → 206 ms). Se mantiene. |
| `search-relevance-engineer` | **PASA** | Rechazó la premisa del encargo con evidencia: `text_normalization.py` no interviene en la ruta RAG (solo `query_router.py:19` y `business_query_service.py:70` — verificado). Hallazgo real: `IT_SECTION_REFERENCE_PATTERN` (`rag_service.py:267`) exige espacio (`\bit\s+`), así que "IT-3.3" e "IT3.3" no se capturan, mientras `ITC_REFERENCE_PATTERN` (`:265`) sí acepta `[-\s]*`. Se mantiene. |

Errores cometidos en las pruebas, para calibrar cuánto fiarse:

- `search-relevance-engineer` inspeccionó `chroma_db/` en la raíz del repo y concluyó "0 embeddings". La ruta real es `src/chroma_db` (el `.env` define `../chroma_db`, que `_to_absolute_path` resuelve desde `src/backend/`) y tiene 1044 embeddings. El de la raíz es un residuo obsoleto.
- `rag-pipeline-engineer` midió su A/B sobre un corpus local con **un solo documento** indexado (`alta_tension/A16436-16554.pdf`). Lo advirtió él mismo, pero su "cross-encoder no justificado" debe leerse como *no demostrado con este corpus*, no como cerrado.

**Lección aplicable:** ambos agentes son fiables cuando trazan el valor a través del código y poco fiables cuando inspeccionan el entorno a mano. Exígeles siempre la ruta de código, no el atajo.

**Nota operativa:** un subagente recién añadido a `.claude/agents/` no se registra hasta reiniciar la sesión de Claude Code. La primera prueba de `model-qa-auditor` se hizo por proxy (agente genérico cargando el fichero de instrucciones).

## Descartados a propósito

- **`agents-orchestrator`** — se solapa con la orquestación nativa de Claude Code y su prompt reclama liderazgo autónomo con reintentos sin intervención manual, lo que contradice `CLAUDE.md` ("si algo no está claro, detente, pregunta").
- **Verticales ajenas** (healthcare, legal, real estate, hospitality, loan officer, etc.) — la mayor parte del catálogo original no aplica a este proyecto.
- **`specialized-civil-engineer`** — es ingeniería civil, no eléctrica. No aporta al dominio REBT/RLAT/RITE.

## Candidatos para más adelante

Solo si los tres actuales demuestran valor: `codebase-archaeologist` (deriva entre sesiones de IA y entre las dos rutas RAG Chroma/Azure), `workflow-architect` (mapa de ramas de fallo del pipeline de chat), `data-privacy-officer` (retención y minimización de datos de conversación).
