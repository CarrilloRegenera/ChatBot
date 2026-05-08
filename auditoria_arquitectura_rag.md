# Auditoría Técnica — ChatBot RAG Corporativo

**Fecha:** 2026-05-08
**Auditor:** Principal AI Architect + Staff Engineer
**Proyecto:** ChatBot RAG — Regenera Energy

---

## 1. Resumen Ejecutivo

### Hallazgos clave

| # | Hallazgo | Severidad |
|---|----------|-----------|
| 1 | `time.sleep()` síncrono dentro de FastAPI bloquea el event loop en escenarios de carga | Alta |
| 2 | Dos instancias separadas de `chromadb.PersistentClient` apuntan al mismo path (SQLite lock) | Crítica |
| 3 | `validate_answer` se llama **dos veces** por request: una dentro de `generate_ai_response_with_fallback` y otra en `send_message` — los resultados de la segunda pisan a los de la primera | Alta |
| 4 | El modelo de embeddings `paraphrase-multilingual-MiniLM-L12-v2` tiene un límite real de **128 tokens**. Los chunks de 950 chars exceden ese límite y el modelo trunca silenciosamente | Crítica |
| 5 | Búsqueda léxica con `where_document={"$contains": term}` es O(n) full-scan sobre Chroma. A >5k chunks se vuelve lento | Alta |
| 6 | `_get_indexed_sources()` llama a `collection.get(include=["metadatas"])` sin límite — carga todos los metadatos en memoria en cada sincronización | Alta |
| 7 | Auth de admin: `role == "administrador"` viene como **query parameter sin firmar**. Cualquiera puede elevar permisos | Crítica |
| 8 | `InteraccionesRAG.Contexto NVARCHAR(MAX)` almacena el contexto RAG completo en SQL Server. Crecerá a cientos de MB rápidamente | Alta |
| 9 | Naming venenenoso: `OPENAI_API_KEY`/`OPENAI_MODEL` contienen credenciales de Google. El pricing en `memory_service.py` mezcla GPT-4o con Gemini sin coherencia | Media |
| 10 | `_conversation_locks` es un `dict` que crece sin límite — memory leak en el proceso FastAPI | Media |
| 11 | Scoring de confianza tiene 30+ reglas heurísticas hardcodeadas que no escalan a nuevos dominios o tipos de pregunta | Alta |
| 12 | Sin migration system (Alembic/Flyway). La gestión de esquema con `IF OBJECT_ID ... ALTER TABLE ADD COLUMN` no tiene versionado, ordering ni rollback | Media |
| 13 | `TOP_K_RESULTS = 3` es extremadamente bajo para consultas de normativa compleja | Alta |
| 14 | Sin paginación en `list_conversations`, `list_pending_interactions` sólo tiene `TOP(?)` sin offset | Media |
| 15 | El sistema de confianza mide overlap léxico answer↔context, no grounding semántico — puede dar alta confianza a respuestas incorrectas con vocabulario compartido | Alta |

### Nivel de madurez

**3/10 — Prototipo funcional avanzado.** El sistema demuestra buen pensamiento de producto (flujo de validación humana, fallback de modelos, scoring de confianza, memoria validada), pero la implementación técnica tiene deuda arquitectónica que se pagará cara al escalar.

### Riesgos críticos en los próximos 6 meses

1. Corrupción de ChromaDB por dual PersistentClient bajo carga concurrente
2. Degradación silenciosa de calidad por truncamiento de embeddings
3. Brecha de seguridad por auth de query param sin firmar
4. Latencia creciente por O(n) lexical scan al indexar más documentos

---

## 2. Lo que está bien

**Flujo de revisión humana (HITL)** — La distinción entre `InteraccionesRAG` (pendiente/validada/rechazada) y `ConocimientoValidado` + la colección Chroma de memoria validada es un patrón correcto y maduro. Pocos chatbots RAG corporativos tienen este circuito desde el inicio.

**Sistema de fallback multi-modelo** — La lógica de escalado `flash → pro` basada en confidence + conflict detection + 503-abort está bien diseñada. El `_MAX_CONSECUTIVE_503 = 3` antes de abandonar el modelo es sensato.

**Query profiling** — El `_build_query_profile` que detecta intención (tabla, definición, lista, numérico, procedimiento) para modificar retrieval y prompting es un patrón correcto que pocos sistemas tienen en esta fase.

**Metadatos estructurados en chunks** — `source`, `folder`, `page`, `chunk`, `section`, `topics`, `chunk_kind`, `file_hash` es una estrategia de metadata útil y más rica que la mayoría de implementaciones básicas.

**Detección de ef_version** — El mecanismo `_get_or_reset_collection` que detecta cambio de modelo de embeddings y fuerza reindexado es correcto y evita corrupción silenciosa.

**Logging estructurado por stages** — `router_ms`, `rag_ms`, `llm_ms`, `db_ms` en el log de cada request permite diagnóstico de latencia por componente. Es la base correcta para observabilidad.

**Hash de archivo para reindexado incremental** — El MD5 por fichero evita reindexar documentos no modificados. Correcto para volumen actual.

---

## 3. Debilidades y Riesgos (Priorizados)

### CRÍTICA-1: Dual ChromaDB PersistentClient — riesgo de corrupción

**Problema:** `rag_service.py:150` y `memory_service.py:39` crean dos instancias de `chromadb.PersistentClient(path=CHROMA_DB_PATH)` apuntando al mismo directorio SQLite.

**Por qué ocurrirá:** ChromaDB usa SQLite como backend. SQLite tiene limitaciones de concurrencia en escritura (`SQLITE_BUSY`). Dos `PersistentClient` distintos en el mismo proceso abren dos conexiones SQLite independientes. Cuando ocurran writes concurrentes (sync + validate en paralelo), pueden producirse locks o corrupción de índice.

**Síntomas:** Errores `database is locked`, colecciones que desaparecen o devuelven resultados inconsistentes, pérdida de memoria validada.

**Impacto:** Pérdida de datos. **Probabilidad:** Alta en producción con >3 usuarios concurrentes.

**Recomendación:** Centralizar en un singleton compartido:

```python
# chroma_client.py
import chromadb
from config import CHROMA_DB_PATH

_client: chromadb.PersistentClient | None = None

def get_chroma_client() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    return _client
```

Ambos módulos importan `get_chroma_client()`. Una sola instancia, sin riesgo de lock.

---

### CRÍTICA-2: Truncamiento silencioso de embeddings

**Problema:** `paraphrase-multilingual-MiniLM-L12-v2` tiene un límite de **128 tokens** (no chars). Un chunk de 950 caracteres en español equivale aproximadamente a 200-250 tokens. El modelo **silenciosamente trunca** todo lo que supere su ventana.

**Por qué ocurrirá:** Es el comportamiento predeterminado de SentenceTransformers con este modelo. No hay warning, no hay error. El embedding resultante representa sólo los primeros ~100-110 tokens del chunk.

**Síntomas:** Preguntas sobre el contenido de la segunda mitad de un chunk no encuentran ese chunk por similitud semántica. La búsqueda léxica lo rescata parcialmente, ocultando el problema.

**Impacto:** Degradación sistemática de recall en consultas que apuntan a contenido al final de chunks largos. **Probabilidad:** Segura, ya está ocurriendo.

**Recomendación a corto plazo:** Reducir `CHUNK_SIZE` a ~400-500 chars (≈100-120 tokens en español). Con `CHUNK_OVERLAP=100`.

**Recomendación a medio plazo:** Migrar a `intfloat/multilingual-e5-base` (512 tokens) o `BAAI/bge-m3` (8192 tokens). Requiere reindexado completo.

---

### CRÍTICA-3: Auth de admin por query parameter sin firmar

**Problema:** `chat.py:311` — `def admin_sync(role: str)` y todos los endpoints admin reciben `role` como query param. No hay JWT, no hay sesión, no hay middleware de autenticación.

**Síntomas:** `GET /admin/metrics?role=administrador` funciona para cualquier persona que conozca la URL.

**Impacto:** Exposición completa de métricas internas, posibilidad de desencadenar reindexado masivo desde exterior, acceso a conversaciones de otros usuarios.

**Recomendación inmediata:**

```python
from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

def require_admin(credentials: HTTPAuthorizationCredentials = Security(security)):
    # validate JWT token, extract role from payload
    ...
```

No escalar este sistema a más usuarios sin resolver esto primero.

---

### ALTA-1: `validate_answer` se llama dos veces por request

**Problema:** En `generate_ai_response_with_fallback` (`ai_service.py:767`) se llama `validate_answer`. Luego en `send_message` (`chat.py:227`) se vuelve a llamar sobre el texto ya procesado por `postprocess_answer`.

**Por qué es problemático:** El segundo `validate_answer` llama a `postprocess_answer` internamente de nuevo. El texto puede ser modificado dos veces. El `confidence` calculado en el fallback (que tomó la decisión de escalar a Pro) es descartado, y se recalcula un score diferente que es el que se guarda en BD y se devuelve al usuario. Los dos scores pueden divergir.

**Recomendación:** `generate_ai_response_with_fallback` debe devolver el `confidence` en su dict result, y `send_message` debe usarlo directamente sin recalcular.

---

### ALTA-2: `time.sleep()` en retry loop puede bloquear

**Problema:** `generate_ai_response` (`ai_service.py:727`) usa `time.sleep(_RETRY_WAITS[attempt])` con esperas de hasta 20 segundos. FastAPI ejecuta las rutas sync en un threadpool, pero si los threads están todos bloqueados en sleep, nuevas requests se encolan.

Con `_RETRY_WAITS = [2, 5, 10, 20]` y fallback a Pro, una request puede tardar teóricamente:

```
2 + 5 + 10 + 20 (flash) + 2 + 5 + 10 + 20 (pro) = 74 segundos bloqueando un thread
```

**Recomendación:** Convertir la ruta a `async def send_message` y usar `await asyncio.sleep()`. O usar `httpx.AsyncClient` con el SDK de Google.

---

### ALTA-3: `_get_indexed_sources()` carga todos los metadatos en RAM

**Problema:** `rag_service.py:454` — `collection.get(include=["metadatas"])` sin `limit`. Con 50 PDFs × 100 chunks = 5.000 registros. Con 500 PDFs = 50.000 registros cargados en RAM en cada sync.

**Recomendación:** Usar un `SET` de IDs de fuentes del lado de Chroma, o mantener un registro externo en SQL de qué archivos están indexados y su hash. La tabla `DocumentosFuente` (ver sección D) eliminaría esta necesidad.

---

### ALTA-4: Búsqueda léxica O(n)

**Problema:** `rag_service.py:666` — `collection.get(where_document={"$contains": term})` escanea todos los documentos para cada core term. Con ThreadPoolExecutor de 4 workers, son hasta 4 scans paralelos sobre toda la colección.

Con 10.000 chunks y 4 core terms = 40.000 comparaciones de string por request, todo dentro del ciclo de cada pregunta.

**Recomendación:** BM25 dedicado (Elasticsearch, Qdrant con sparse vectors, o una tabla FTS en SQL Server) en paralelo con la búsqueda semántica. Eliminar la búsqueda O(n) de Chroma.

---

### ALTA-5: `TOP_K_RESULTS = 3` — retrieval insuficiente

**Problema:** El sistema recupera 3 chunks finales (aunque la ventana de candidatos es más amplia). Para normativa eléctrica donde una respuesta puede requerir ITC-BT-XX + tabla + artículo relacionado, 3 chunks es frecuentemente insuficiente.

**Síntomas:** El modelo recibe contexto truncado para preguntas de múltiples normas y responde con "No hay información suficiente" cuando sí existe en el corpus.

**Recomendación:** `TOP_K_RESULTS = 6` mínimo. Para table queries, 10. El prompt ya gestiona el caso de contexto parcial.

---

### ALTA-6: Confidence scoring heurístico no escala a múltiples dominios

**Problema:** El `validate_answer` en `ai_service.py:811` tiene ~35 reglas heurísticas específicas para normativa eléctrica española (IP, IK, TT/TN/IT, circuitos mínimos, ITC-BT). Si se añaden RRHH, Legal o Finanzas, estas reglas producirán falsos negativos masivos (vocabulario distinto, sin números eléctricos = baja confianza falsa).

**Recomendación a corto plazo:** Añadir un flag de dominio al query profile y skip de ciertas reglas heurísticas fuera del dominio eléctrico.

**Recomendación a medio plazo:** Sustituir o complementar con LLM-as-judge (mini modelo que evalúa grounding) — más costoso pero dominio-agnóstico.

---

### MEDIA-1: `_conversation_locks` — memory leak

**Problema:** `chat.py:66` — el dict `_conversation_locks` nunca se poda (excepto en delete, que no siempre ocurre). Con 10.000 conversaciones activas, 10.000 `Lock()` objects en memoria para siempre.

**Recomendación:** TTL-based LRU cache con `cachetools.TTLCache` o `functools.lru_cache`. O simplemente eliminar el lock y usar DB-level optimistic locking.

---

### MEDIA-2: Contexto completo almacenado en SQL Server

**Problema:** `InteraccionesRAG.Contexto NVARCHAR(MAX)` guarda el contexto RAG completo (3-5 KB por fila). Con 1.000 interacciones/mes, eso son varios GB/año de texto redundante que ya existe en Chroma.

**Recomendación:** Guardar sólo los IDs de los chunks recuperados + score, no el texto completo. Para reproducir el contexto, consultar Chroma por ID.

---

## 4. Análisis Profundo por Capas

### Arquitectura general

La arquitectura sigue un patrón monolítico modular: un proceso FastAPI que contiene RAG engine, LLM client, memory service y DB access. Para la fase actual esto es correcto. El riesgo es que a medida que crezca, todo está en el mismo proceso sin aislamiento de fallos. Un crash del embedding model tira todo el servicio.

**Patrón de inicialización global problemático:** `rag_service.py` ejecuta código a nivel de módulo (líneas 150-152) que crea el cliente Chroma, carga el modelo SentenceTransformer y crea la colección. Si el modelo no está descargado, el import falla y FastAPI no arranca. No hay lazy loading ni health check independiente.

---

### Backend (FastAPI)

- Las rutas sync en FastAPI se ejecutan en el threadpool de Starlette. Correcto para código blocking, pero el threadpool tiene un límite de workers. Con timeouts de 20s por retry, la saturación es posible.
- No hay rate limiting en ningún endpoint.
- `create_conversation` no verifica que el `user_id` exista.
- La ruta `routes/auth.py` debe verificar que implementa JWT real y no sesiones basadas en cookie sin firma.

---

### Base de datos

**Lo que falta en el schema actual:**

- Sin índice en `InteraccionesRAG.FechaCreacion` — las queries de métricas hacen full scan.
- Sin índice en `InteraccionesRAG.Estado` — el panel admin filtra por pendiente sin índice.
- Sin índice en `Mensajes.ConversacionId` — critical path de `_get_recent_history`.
- `_save_chat_message` y `record_interaction_pending` son **dos writes separadas** sin transacción. Si `record_interaction_pending` falla, el mensaje ya fue guardado en `Mensajes` pero no en `InteraccionesRAG`. Estado inconsistente.
- Sin sistema de migraciones. La gestión con `IF OBJECT_ID ... ALTER TABLE ADD COLUMN` no tiene versionado, ordering ni rollback.

---

### RAG — Chunking

El chunker basado en caracteres (`CHUNK_SIZE=950`) es razonable para el dominio pero tiene limitaciones:

- **Problema con tablas:** `fitz.Page.get_text("text")` extrae tablas como texto plano. Las celdas se separan por espacios, las filas por `\n`. Una tabla de ITC-BT puede quedar fragmentada en 3-4 chunks sin que ninguno sea semánticamente completo.
- **Sección heredada:** El algoritmo de detección de sección se asigna al primer bloque de la página y no se propaga correctamente a chunks de páginas posteriores que continúan la misma sección.
- **No hay chunk de solapamiento estructural:** el overlap de 200 chars es positional, no semántico. Si un artículo importante cae en el boundary, puede quedar partido.

**Recomendación:** Para PDFs de normativa, explorar `pdfplumber` para extracción de tablas como objetos y chunking separado de tablas vs. texto corrido.

---

### RAG — Retrieval

El pipeline tiene dos fases:

1. Búsqueda semántica con `collection.query(n_results=candidate_count)` — usa HNSW de Chroma.
2. Búsqueda léxica `$contains` por core terms — O(n).

Luego una fase de scoring con 15+ señales combinadas en un score lineal. Esto es frágil porque:

- Los pesos (`SECTION_PRIORITY_BOOST = 8`, `LABELED_MATCH_PRIORITY_BOOST = 12`, etc.) fueron tuneados manualmente y no tienen baseline de evaluación offline.
- Un solo campo cambiante (p.ej. un documento sin sections) puede hacer colapsar el score.
- El sistema no aprende: si un chunk siempre se devuelve pero el usuario siempre valida otra respuesta, no hay feedback loop al retrieval.

---

### Reranking

El "reranking" actual usa el **mismo modelo** que los embeddings (`paraphrase-multilingual-MiniLM-L12-v2`) para calcular `cos_sim(query, candidate)`. Esto no es reranking real — es repetir la similitud semántica que Chroma ya calculó internamente con HNSW.

El `RERANK_WEIGHT = 9.0` suma `base_score + (cos_sim * 9.0)`. Dado que base_score puede ser 20-50 puntos y cos_sim está entre 0 y 1, el rerank añade entre 0 y 9 puntos. Su influencia relativa es baja cuando el heurístico score es alto.

**Un reranker real** (cross-encoder como `cross-encoder/ms-marco-MiniLM-L-6-v2` o el API de Cohere Rerank) evalúa el par (query, document) juntos, captando interacciones semánticas que el bi-encoder no puede ver.

---

### Prompting

El prompt tiene **25 reglas numeradas** + instrucciones de formato + historial + contexto. Para Gemini Flash 2.5 esto puede ser sub-óptimo: más instrucciones no siempre = mejor compliance.

**Problemas específicos:**

- Las reglas 1-12 son generales, 13-25 son dinámicas por intent. La instrucción más importante (sólo usar el contexto) está enterrada entre otras.
- El historial se trunca a 300 chars por turno — agresivo. Un historial de 2 turnos con truncado puede perder el referente de la pregunta anterior.
- No hay few-shot examples para casos difíciles (tabla incompleta en contexto, valores numéricos contradictorios entre fuentes).

---

### LLM — Modelo y estrategia

**Gemini 2.5 Flash como modelo primario** es una elección razonable: buena comprensión del español, bajo coste, latencia aceptable. El fallback a un modelo más potente para preguntas complejas o baja confianza es el patrón correcto.

**Riesgo del fallback:** El modelo secundario en `config.py` hace fallback a `gemini-3-flash-preview` que **no existe como modelo GA**. Si este modelo no está disponible en la API de Google, el fallback falla y el sistema devuelve un error en lugar de la respuesta del modelo primario.

**Recomendación:** Siempre tener el fallback secundario sobre un modelo GA conocido (`gemini-2.0-pro` o `gemini-1.5-pro`).

---

### Validación de respuestas

El sistema de confidence tiene un problema conceptual serio: mide **overlap léxico** entre respuesta y contexto. Un modelo que copia frases del contexto tendrá alta confianza, aunque esas frases no respondan a la pregunta. Un modelo que parafrasea correctamente puede tener baja confianza por vocabulario diferente.

La métrica más fiable para grounding sería: ¿puede localizarse cada afirmación clave de la respuesta en al menos un chunk del contexto? Esto requiere un LLM judge o un sistema de span attribution, no overlap léxico.

---

### Observabilidad

`observability.py` es un stub de 23 líneas que sólo gestiona un `request_id` en contexto. No hay:

- Métricas de Prometheus/OpenMetrics.
- Trazas de OpenTelemetry.
- Dashboard de retrieval quality (¿cuántos chunks se recuperan? ¿cuál es el avg score? ¿cuántos memory hits?).
- Alertas automáticas (error rate, latency p95, confidence media).
- Distribución de confidence scores a lo largo del tiempo.

El logging estructurado con stages es útil pero requiere parsing manual de logs. No es observable.

---

### Seguridad

1. Auth de admin sin JWT — ya descrito (CRÍTICA-3).
2. Sin validación de `conversation_id` — un usuario puede leer mensajes de la conversación de otro si conoce el ID.
3. Sin rate limiting — un usuario puede hacer flood de requests.
4. `pyodbc` con `Trusted_Connection=yes` y `Encrypt=no` — la conexión a SQL Server no está cifrada. Aceptable en localhost, inaceptable si BD remota.
5. El contexto RAG completo se guarda en la BD — si la BD se compromete, se expone todo el corpus.
6. Sin sanitización del output antes de enviarlo al frontend — si el modelo devuelve HTML/JS en su respuesta, podría haber XSS.

---

### Coste y rendimiento

Con `CONFIDENCE_FALLBACK_THRESHOLD = 0.65`, el sistema escala al modelo secundario en muchos casos. Si el 40% de las queries escalan (estimación conservadora para normativa técnica compleja):

```
coste_efectivo ≈ 0.6 × flash_cost + 0.4 × (flash_cost + pro_cost)
               = 1.0 × flash + 0.4 × pro
```

Sin telemetría de cuánto % escala de verdad, no se puede optimizar este parámetro.

**La latencia potencial es inaceptable para producción.** Caso worst-case:

| Etapa | Tiempo |
|-------|--------|
| Routing | ~5 ms |
| RAG + lexical | ~200 ms |
| Flash (3 reintentos + sleep 2+5+10) | ~20 s |
| 503 fallback Flash-model | ~5 s |
| Pro fallback | ~10 s |
| DB writes | ~50 ms |
| **Total worst-case** | **~35+ segundos** |

---

## 5. Escalabilidad Multi-Departamento

### El problema fundamental: una sola colección Chroma

El sistema actual tiene `COLLECTION_NAME = "reglamentos"` — una colección única. Si se añaden documentos de RRHH, Legal y Finanzas:

1. **Contaminación de contexto:** Una pregunta sobre "protección" en RRHH puede recuperar chunks de protección eléctrica REBT.
2. **El scoring heurístico se rompe:** Las reglas para ITC-BT, IP, IK, esquemas TT/TN son irrelevantes en otros dominios.
3. **Sin aislamiento de permisos:** Un usuario de RRHH puede recuperar documentos de Legal si hace la pregunta correcta.

---

### Arquitectura recomendada para multi-departamento

**Patrón: Colección por dominio + metadata filtering + router semántico**

```
Capa de routing
├── Query Router (reglas + semántica)
│   ├── domain_classifier → ["rebt", "rrhh", "legal", "finanzas"]
│   └── cross_domain_flag → bool
│
Capa RAG por dominio
├── ChromaDB / VectorDB
│   ├── collection: "corp_rebt"        # Normativa eléctrica
│   ├── collection: "corp_rrhh"        # Políticas RRHH
│   ├── collection: "corp_legal"       # Contratos, compliance
│   ├── collection: "corp_finanzas"    # Procedimientos financieros
│   └── collection: "corp_calidad"     # ISO, procedimientos
│
Control de acceso
└── user_domain_permissions (SQL)
    ├── user_id → [dominio_1, dominio_2]
    └── filtrado antes de query a VectorDB
```

**Para el router de dominio:** No uses sólo keywords. Entrena un clasificador ligero (zero-shot con un LLM pequeño, o un `LinearSVC` sobre embeddings de las queries históricas) que asigne la query a uno o varios dominios antes de hacer retrieval.

```python
def route_to_domains(question: str, user_domains: List[str]) -> List[str]:
    # 1. Zero-shot classification con el LLM
    # 2. Filtrado por permisos del usuario
    # 3. Si cross-domain, busca en todos los dominios permitidos y fusiona
    ...
```

**Para cross-domain queries** ("¿qué dice el contrato de mantenimiento sobre la normativa eléctrica?"):

- Retrieval en paralelo sobre los dominios relevantes.
- Re-ranking conjunto antes de enviar al LLM.
- Prompt indica las fuentes de cada dominio para que el LLM no mezcle.

---

### Estrategia de particionado recomendada

| Dimensión | Estrategia | Justificación |
|-----------|-----------|---------------|
| Por departamento | Colección separada | Aislamiento de permisos + scores independientes |
| Por idioma | Subcarpeta + metadata `lang` | Español/Inglés pueden coexistir con filtro |
| Por país | Colección separada si legislación distinta | REBT España ≠ NEC USA — contaminarían scores |
| Por versión | Campo `version` + `vigente: bool` en metadata | Nunca borrar, sólo marcar como obsoleto |
| Por tenant | DB ChromaDB separada por tenant si SaaS | Aislamiento fuerte; colecciones si mismo tenant |

---

### Gobernanza documental

El sistema actual no tiene:

1. **Vigencia documental:** Un PDF de 2018 puede estar desactualizado. No hay `fecha_vigencia` en metadata.
2. **Versionado:** Si se sube una nueva versión de un reglamento, los chunks del reglamento viejo en `ConocimientoValidado` siguen apuntando al documento obsoleto.
3. **Documentos contradictorios:** Si dos normas se contradicen, el sistema las mezclará en el mismo contexto sin distinguir cuál es vigente.

**Tabla `DocumentosFuente` necesaria:**

```sql
CREATE TABLE DocumentosFuente (
    Id INT IDENTITY PRIMARY KEY,
    NombreArchivo NVARCHAR(500) NOT NULL,
    FileHash VARCHAR(32) NOT NULL,
    Dominio NVARCHAR(100) NOT NULL,
    Version NVARCHAR(50) NULL,
    FechaDocumento DATE NULL,
    FechaVigenciaDesde DATE NULL,
    FechaVigenciaHasta DATE NULL,   -- NULL = vigente
    Supersedido_Por INT NULL REFERENCES DocumentosFuente(Id),
    Estado NVARCHAR(20) NOT NULL DEFAULT 'activo',  -- activo/obsoleto/revision
    FechaIndexado DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    IndexadoPor NVARCHAR(120) NULL
);
```

Con este registro, al recuperar chunks puedes filtrar por `Estado = 'activo'` y `FechaVigenciaHasta IS NULL OR FechaVigenciaHasta > GETDATE()`. Esta tabla también eliminaría la necesidad del O(n) `_get_indexed_sources()`.

---

### Escalado de ingesta documental

El `sync_documents` actual es síncrono y ejecutado en el mismo proceso FastAPI. Con 500 PDFs de 200 páginas cada uno, el sync puede tardar horas y bloquear el servidor.

**Para escalar ingesta:**

```
Admin sube PDF → Storage (Azure Blob / S3 / carpeta de red)
              → Queue (Azure Queue / RabbitMQ)
              → Worker independiente (Celery / Azure Function)
                 → Extracción, chunking, embedding, upsert a Chroma
                 → Update DocumentosFuente en SQL
              → Notificación a admin cuando listo
```

---

## 6. Arquitectura Objetivo

### Hoy (estado actual validado)

```
[Frontend HTML] → [FastAPI monolítico]
                     ├── [ChromaDB local] (1 colección)
                     ├── [SQL Server local]
                     └── [Google GenAI API]
```

### A 6 meses

```
[Frontend SPA] → [FastAPI + JWT auth]
                    ├── [ChromaDB] (N colecciones por dominio)
                    │    └── singleton client compartido
                    ├── [SQL Server] (migraciones Alembic/Flyway)
                    │    └── pool de conexiones (SQLAlchemy)
                    ├── [Google GenAI API]
                    │    ├── Flash (primary, async)
                    │    └── Pro GA (fallback, async)
                    ├── [BM25 / FTS SQL Server] (lexical search dedicado)
                    └── [Celery Worker] (ingesta asíncrona de documentos)
```

**Cambios clave:**
- JWT real en todos los endpoints.
- Modelo de embeddings con ventana ≥512 tokens.
- `TOP_K_RESULTS = 6-8`.
- Tabla `DocumentosFuente` con versionado y vigencia.
- Permisos por dominio en SQL.
- Ingesta desacoplada del proceso web.
- Async LLM calls.

### A 12 meses — Plataforma corporativa

```
[Frontend React/Vue] ──► [API Gateway + Auth (OAuth2/OIDC)]
                               │
                    ┌──────────┼──────────────┐
                    ▼          ▼              ▼
             [Chat Service] [Admin Service] [Ingesta Service]
                    │          │              │
                    ▼          ▼              ▼
            [Query Router]  [Analytics]  [Document Pipeline]
            [RAG Engine  ]  [DB SQL    ]  [Celery Workers  ]
                    │                      │
                    ▼                      ▼
            [Qdrant/Weaviate]        [Azure Blob / S3]
            (multi-tenant,           (document store)
             N collections,
             sparse+dense hybrid)
                    │
                    ▼
            [LLM Orchestration]
            [Flash → Pro → Fallback]
            [LLM-as-Judge validation]
                    │
                    ▼
            [Observability Stack]
            [Prometheus + Grafana]
            [OpenTelemetry traces]
            [RAG quality dashboard]
```

**Migración de ChromaDB a Qdrant o Weaviate** proporciona:
- API HTTP nativa (no SQLite local).
- Hybrid search (dense + sparse BM42 nativo).
- Multi-tenancy con namespaces.
- Replicación y backup.
- Scalar quantization para reducir memoria.

---

## 7. Roadmap Priorizado

### Próximos 30 días — Fixes críticos

| Día | Tarea | Verificación |
|-----|-------|-------------|
| 1-2 | Unificar ChromaDB client en singleton compartido | Sin errores `database is locked` bajo carga concurrente |
| 2-3 | Fix auth admin: JWT o token en header `X-Admin-Token` | Endpoint admin rechaza llamadas sin token |
| 3-4 | Eliminar segunda llamada a `validate_answer` en `send_message` | El confidence devuelto = el del fallback |
| 4-6 | Reducir `CHUNK_SIZE` a 450 chars + reindexar corpus completo | Embeddings sin truncamiento (verificar con tokenizer) |
| 6-7 | Añadir índices SQL: `FechaCreacion`, `Estado`, `ConversacionId` | Queries de métricas <100ms |
| 7-8 | Aumentar `TOP_K_RESULTS` a 6, table queries a 10 | Menos respuestas "no hay información suficiente" |
| 8-12 | Convertir retry a `asyncio.sleep` o llamada async al LLM | Threads no bloqueados durante reintentos |
| 12-15 | Verificar/fijar modelo secundario a modelo GA existente | Fallback funciona sin errores de modelo desconocido |
| 15-20 | `cachetools.TTLCache` para `_conversation_locks` | Memoria del proceso estable tras 24h |
| 20-25 | Eliminar almacenamiento de contexto en `InteraccionesRAG.Contexto` | Reducción de >70% en tamaño de esa tabla |
| 25-30 | Renombrar variables `OPENAI_*` a `GEMINI_*` o `LLM_*` | Sin confusión en variables de entorno |

### 30-90 días — Arquitectura base

1. **Tabla `DocumentosFuente`** con versionado, vigencia y dominio.
2. **Pool de conexiones SQL** — SQLAlchemy con `pool_size=10`.
3. **Migración de embeddings** a `intfloat/multilingual-e5-base` o `BAAI/bge-m3`. Reindexado completo.
4. **BM25 dedicado** — FTS en SQL Server o Elasticsearch, reemplazando el `$contains` de Chroma.
5. **Cross-encoder reranker** — `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (multilingüe, ~60ms/batch).
6. **Permisos por dominio** en SQL — `UsuarioDominios(UsuarioId, Dominio, FechaAlta)`.
7. **Colecciones separadas por dominio** en Chroma — `corp_rebt`, `corp_rrhh`, etc.
8. **Sistema de migraciones de BD** — Alembic o scripts versionados con rollback.

### 6 meses — Plataforma

1. **Ingesta asíncrona con Celery** — desacoplar sync de docs del proceso web.
2. **LLM-as-judge** para validación de grounding — reemplaza scoring heurístico para dominios no eléctricos.
3. **Dashboard de calidad RAG** — Grafana o similar con: retrieval recall@K, confidence distribution, escalation rate, validation rate por dominio.
4. **Golden dataset de evaluación** — mínimo 50 preguntas por dominio con respuesta esperada y fuentes esperadas. CI evalúa retrieval quality en cada cambio de parámetros RAG.
5. **Router semántico de dominio** — clasificador zero-shot para asignar query al dominio correcto antes del retrieval.
6. **Evaluación de migración a Qdrant** — test de rendimiento con corpus completo proyectado.

---

## 8. Tabla Final de Prioridades

| Iniciativa | Impacto | Esfuerzo | Riesgo mitigado | Prioridad |
|-----------|---------|----------|-----------------|-----------|
| Unificar ChromaDB PersistentClient | Crítico | Bajo (2h) | Corrupción de índice | **P0** |
| Fix auth admin (JWT/header) | Crítico | Medio (1d) | Brecha de seguridad | **P0** |
| Fix validate_answer doble llamada | Alto | Bajo (2h) | Confidence scores incorrectos | **P0** |
| Reducir CHUNK_SIZE + reindexar | Crítico | Bajo (4h) | Truncamiento embeddings silencioso | **P0** |
| Índices SQL críticos | Alto | Bajo (1h) | Full scan en producción | **P1** |
| Aumentar TOP_K_RESULTS a 6 | Alto | Bajo (30min) | Recall insuficiente en normativa | **P1** |
| Fix retry con async sleep | Alto | Medio (1d) | Thread starvation bajo carga | **P1** |
| Tabla DocumentosFuente | Alto | Medio (2d) | Gobernanza documental | **P1** |
| Pool conexiones SQL | Alto | Medio (1d) | Saturación de conexiones | **P1** |
| TTL conversation_locks | Medio | Bajo (1h) | Memory leak en proceso | **P1** |
| No guardar Contexto en SQL | Medio | Bajo (4h) | Crecimiento descontrolado de BD | **P2** |
| Migración embeddings multilingual-e5 | Alto | Alto (3d+reindex) | Calidad semántica real | **P2** |
| BM25 dedicado | Alto | Alto (3d) | Latencia O(n) lexical search | **P2** |
| Cross-encoder reranker real | Alto | Medio (2d) | Calidad retrieval para normativa ambigua | **P2** |
| Colecciones por dominio | Crítico a escala | Alto (1 semana) | Contaminación de contexto multi-dpto | **P2** |
| Permisos por dominio SQL | Crítico a escala | Medio (2d) | Aislamiento documental | **P2** |
| Ingesta asíncrona Celery | Alto | Alto (1 semana) | Bloqueo en sync masivo | **P3** |
| LLM-as-judge validation | Alto | Alto (1 semana) | Falsos positivos en confianza | **P3** |
| Golden dataset + CI eval | Alto | Alto (2 semanas) | Regresiones silenciosas en RAG | **P3** |
| Dashboard Grafana/Prometheus | Medio | Alto (1 semana) | Ceguera operacional | **P3** |
| Migración a Qdrant | Alto para enterprise | Muy alto (1 mes) | Escalabilidad vector DB real | **P4** |

---

## Apéndice: Índices SQL recomendados

```sql
-- Críticos para rendimiento inmediato
CREATE INDEX IX_InteraccionesRAG_FechaCreacion
    ON dbo.InteraccionesRAG (FechaCreacion DESC);

CREATE INDEX IX_InteraccionesRAG_Estado
    ON dbo.InteraccionesRAG (Estado)
    INCLUDE (FechaCreacion, Modelo, Confianza);

CREATE INDEX IX_Mensajes_ConversacionId
    ON dbo.Mensajes (ConversacionId)
    INCLUDE (FechaCreacion, Id);

CREATE INDEX IX_Conversaciones_UsuarioId
    ON dbo.Conversaciones (UsuarioId)
    INCLUDE (FechaCreacion, Estado);
```

---

## Apéndice: Modelo de datos objetivo

```sql
-- Permisos por dominio
CREATE TABLE UsuarioDominios (
    Id INT IDENTITY PRIMARY KEY,
    UsuarioId INT NOT NULL REFERENCES Usuarios(Id),
    Dominio NVARCHAR(100) NOT NULL,
    FechaAlta DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    AsignadoPor NVARCHAR(120) NULL,
    CONSTRAINT UQ_UsuarioDominio UNIQUE (UsuarioId, Dominio)
);

-- Registro de chunks recuperados por interacción (reemplaza Contexto NVARCHAR(MAX))
CREATE TABLE InteraccionChunks (
    Id INT IDENTITY PRIMARY KEY,
    InteraccionId INT NOT NULL REFERENCES InteraccionesRAG(Id),
    ChunkId NVARCHAR(500) NOT NULL,  -- ID en Chroma
    Score FLOAT NOT NULL,
    Rank INT NOT NULL,
    Dominio NVARCHAR(100) NULL
);

-- Feedback explícito del usuario
CREATE TABLE FeedbackUsuario (
    Id INT IDENTITY PRIMARY KEY,
    InteraccionId INT NOT NULL REFERENCES InteraccionesRAG(Id),
    UsuarioId INT NOT NULL,
    Rating TINYINT NOT NULL CHECK (Rating BETWEEN 1 AND 5),
    Comentario NVARCHAR(2000) NULL,
    FechaCreacion DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);

-- Evaluaciones de calidad (golden dataset)
CREATE TABLE EvaluacionesRAG (
    Id INT IDENTITY PRIMARY KEY,
    Pregunta NVARCHAR(MAX) NOT NULL,
    RespuestaEsperada NVARCHAR(MAX) NOT NULL,
    FuentesEsperadas NVARCHAR(MAX) NULL,  -- JSON
    Dominio NVARCHAR(100) NOT NULL,
    FechaCreacion DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    CreadoPor NVARCHAR(120) NULL
);
```

---

*Documento generado para uso interno — Regenera Energy — ChatBot RAG v1.x*
