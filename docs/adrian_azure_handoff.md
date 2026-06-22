# Entrega Adrian - Backend ChatBot Azure

Este documento resume que parte queda ya hecha por Adrian en el codigo y que parte debe configurarse/probarse ahora en Azure.

## 1. Estado real del codigo

### Hecho y validado en local

- Backend FastAPI preparado para Docker.
- Imagen local validada con Docker Desktop.
- Puerto del contenedor: `8000`.
- Health check: `GET /health`.
- UI servida desde el backend en `/ui/`.
- SQL parametrizado por `SQL_CONNECTION_STRING`.
- CORS configurable por `ALLOWED_ORIGINS`.
- `sync_documents` controlado por `SYNC_DOCUMENTS_ON_STARTUP`.
- Endpoints admin endurecidos con headers:
  - `x-user-role: administrador`
  - `x-admin-key: ...` si `ADMIN_API_KEY` esta definido.
- Modelo `paraphrase-multilingual-MiniLM-L12-v2` cacheado dentro de la imagen Docker.
- `RAG_BACKEND=chroma` sigue siendo el modo local por defecto.
- `RAG_BACKEND=azure_search` queda implementado como primer camino Azure.

### Validaciones locales realizadas

```powershell
docker build -f src/backend/Dockerfile -t chatbot-api:local src
docker run -d --name chatbot-backend-local -p 8000:8000 --env-file src/backend/.env -e SYNC_DOCUMENTS_ON_STARTUP=false chatbot-api:local
curl.exe -fsS http://127.0.0.1:8000/health
curl.exe -I http://127.0.0.1:8000/ui/
```

Resultado esperado/validado:

```text
/health -> {"status":"ok"}
/ui/    -> HTTP 200 text/html
```

### Preparado pero pendiente de probar contra Azure real

- Lectura de PDFs desde Blob Storage.
- Indexacion de chunks en Azure AI Search.
- Busqueda RAG desde Azure AI Search.
- Endpoint `/admin/sync` usando Blob + AI Search cuando `RAG_BACKEND=azure_search`.

Esta parte necesita que Jorge cree/configure el indice de Azure AI Search y cargue los secrets reales en App Service o Key Vault.

## 2. Imagen Docker

Comando de build local o CI:

```powershell
docker build -f src/backend/Dockerfile -t acrchatbotrg.azurecr.io/chatbot-api:v1 src
```

Comando interno de arranque:

```text
uvicorn main:app --host 0.0.0.0 --port 8000
```

Setting necesario en App Service:

```env
WEBSITES_PORT=8000
```

Imagen esperada en ACR:

```text
acrchatbotrg.azurecr.io/chatbot-api:v1
```

## 3. Recursos Azure conocidos

- Resource group: `rg-chatbot`
- Storage account: `stchatbot011`
- Blob container: `documentos-rag`
- Azure AI Search: `cb-regenera01-we`
- Search index propuesto: `idx-chatbot-rag`
- Application Insights: `appi-chatbot01`
- SQL Server: `sql-regenera-chatbot`
- SQL Database: `ChatBot`
- ACR: `acrchatbotrg`
- App Service Plan: `rg-chatbot-regenera`

## 4. Variables de entorno para App Service

Estas variables van en Azure App Service Configuration o como referencias a Key Vault. No deben ir en el repo.

```env
WEBSITES_PORT=8000
SQL_CONNECTION_STRING=Driver={ODBC Driver 18 for SQL Server};Server=tcp:sql-regenera-chatbot.database.windows.net,1433;Database=ChatBot;UID=...;PWD=...;Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.4-nano
OPENAI_BASELINE_MODEL=gpt-5.4-nano
LLM_SECONDARY_MODEL=gpt-4.1-mini
LLM_FALLBACK_MODEL=gpt-4.1-mini
ALLOWED_ORIGINS=https://URL_FRONTEND_AZURE
SYNC_DOCUMENTS_ON_STARTUP=false
RUN_SCHEMA_MIGRATIONS=1
LOG_LEVEL=INFO
ADMIN_API_KEY=...
RAG_BACKEND=azure_search
AZURE_SEARCH_ENDPOINT=https://cb-regenera01-we.search.windows.net
AZURE_SEARCH_KEY=...
AZURE_SEARCH_INDEX_NAME=idx-chatbot-rag
AZURE_SEARCH_VECTOR_FIELD=content_vector
BLOB_STORAGE_CONNECTION_STRING=...
BLOB_CONTAINER_NAME=documentos-rag
BLOB_PREFIX=
BLOB_PREFIX_ALTA_TENSION=alta_tension/
BLOB_PREFIX_BAJA_TENSION=baja_tension/
BLOB_PREFIX_GUIAS_TECNICAS=guias_tecnicas/
BLOB_PREFIX_RITE=rite/
```

Para desarrollo local, Adrian puede seguir con:

```env
RAG_BACKEND=chroma
SYNC_DOCUMENTS_ON_STARTUP=false
```

## 5. Secretos

Secretos que Jorge debe cargar en Key Vault o App Service settings:

- `SQL_CONNECTION_STRING` o usuario/password SQL.
- `OPENAI_API_KEY`.
- `ADMIN_API_KEY`.
- `AZURE_SEARCH_KEY`.
- `BLOB_STORAGE_CONNECTION_STRING`.

No meter ninguno de estos valores en la imagen Docker ni en Git.

## 6. Indice Azure AI Search esperado

Nombre propuesto:

```text
idx-chatbot-rag
```

El esquema esta en:

```text
docs/azure_search_index_schema.json
```

Campos usados por el backend:

```text
chunk_id              key, string
document_id           string
document_name         string
source_path           string, filterable
blob_path             string, filterable
file_name             string
content               string, searchable
content_vector        vector, dimensions 384
page_number           int
page                  int
chunk_number          int
domain                string, filterable
category              string, filterable
section               string, searchable/filterable
topics                string, searchable
chunk_kind            string, filterable
table_signal_count    int
file_hash             string, filterable
```

La dimension `384` corresponde al modelo cacheado en la imagen:

```text
paraphrase-multilingual-MiniLM-L12-v2
```

## 7. Flujo de documentos en Azure

1. Jorge crea el indice `idx-chatbot-rag` en Azure AI Search con el esquema indicado.
2. Jorge sube PDFs de prueba a Blob Storage bajo estos prefijos:

```text
stchatbot011 / documentos-rag / alta_tension/
stchatbot011 / documentos-rag / baja_tension/
stchatbot011 / documentos-rag / guias_tecnicas/
stchatbot011 / documentos-rag / rite/
```

3. Jorge configura las variables/secrets del App Service.
4. Se despliega la imagen Docker del backend.
5. Se valida:

```http
GET /health
```

6. Se lanza indexacion manual:

```http
POST /admin/sync
x-user-role: administrador
x-admin-key: ...
```

7. El backend:
   - lista PDFs del contenedor Blob,
   - recorre los prefijos configurados por separado,
   - descarga cada PDF,
   - extrae texto,
   - trocea en chunks,
   - calcula hash,
   - genera embeddings con el modelo cacheado,
   - sube chunks a Azure AI Search,
   - guarda `category`, `domain`, `blob_path`, `file_name`, `page` y `document_id`,
   - evita reindexar documentos sin cambios,
   - elimina del indice documentos borrados en Blob.

## 8. Que debe hacer Jorge ahora

1. Confirmar que existe el ACR `acrchatbotrg`.
2. Subir la imagen:

```powershell
az acr login --name acrchatbotrg
docker build -f src/backend/Dockerfile -t acrchatbotrg.azurecr.io/chatbot-api:v1 src
docker push acrchatbotrg.azurecr.io/chatbot-api:v1
```

3. Crear/configurar App Service Linux for Containers apuntando a:

```text
acrchatbotrg.azurecr.io/chatbot-api:v1
```

4. Configurar App Settings y secrets.
5. Crear el indice `idx-chatbot-rag` en `cb-regenera01-we`.
6. Subir PDFs de prueba a `documentos-rag`.
7. Validar `/health`.
8. Lanzar `/admin/sync`.
9. Probar chat con una pregunta que deba recuperar contenido documental.

## 9. Checklist funcional minima

- `GET /health` devuelve `{"status":"ok"}`.
- La UI carga.
- Login funciona.
- Crear conversacion funciona.
- Enviar pregunta funciona.
- Historial se guarda y recupera desde SQL.
- `/admin/sync` indexa PDFs desde Blob.
- El chat recupera fuentes desde Azure AI Search.
- En Application Insights no aparecen errores criticos de startup, SQL, Blob o Search.

## 10. Pendientes tecnicos conocidos

- Probar contra Azure real el modo `RAG_BACKEND=azure_search`.
- Confirmar el nombre final del frontend para cerrar `ALLOWED_ORIGINS`.
- Confirmar si `RUN_SCHEMA_MIGRATIONS=1` se mantiene en el primer despliegue o se mueve a pipeline.
- Confirmar estrategia futura de identidad corporativa/Entra ID.
- Valorar si mas adelante se cambia el modelo local de embeddings por Azure OpenAI Embeddings.
