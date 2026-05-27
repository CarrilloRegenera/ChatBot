# Chatbot PRO con GitHub Actions

Esta es la checklist para sustituir el despliegue manual de `C:\Users\jcanete\Desktop\Deploy_Chatbot.bat` por un primer flujo manual de `GitHub Actions`.

## Qué he dejado preparado en el repo

- Workflow inicial: `.github/workflows/deploy-chatbot.yml`
- Trigger manual: `workflow_dispatch`
- Rama por defecto de despliegue: `sandbox`
- Acciones soportadas: `full`, `backend`, `frontend`

Esta primera versión hace:

1. Login en Azure con `Service Principal` clásico.
2. Build de imagen del backend en ACR.
3. Actualización del contenedor del App Service.
4. Aplicación de app settings del backend.
5. Reinicio y `health check`.
6. Generación de `src/frontend/config.js`.
7. Deploy del frontend a Static Web Apps.
8. Validación del `config.js` publicado.

Esta primera versión no hace todavía:

- importación de histórico desde backup local
- parcheo de redirect URIs en Entra
- `POST /admin/sync` al final

La parte de `admin/sync` la moveremos después a un camino más robusto, porque ahora mismo el endpoint requiere contexto de identidad de admin y no quiero que el primer workflow dependa de eso.

## Qué debes hacer tú en Azure

### 1. Crear el Service Principal clásico

Ejecuta en tu terminal con Azure CLI autenticado:

```powershell
az ad sp create-for-rbac `
  --name "github-chatbot-deploy" `
  --role Contributor `
  --scopes /subscriptions/<TU_SUBSCRIPTION_ID>/resourceGroups/<TU_RESOURCE_GROUP> `
  --sdk-auth
```

Guarda el JSON completo. Ese JSON irá en GitHub como secreto `AZURE_CREDENTIALS`.

Si el ACR está en el mismo Resource Group y el rol `Contributor` no basta para el build, añade también:

```powershell
az role assignment create `
  --assignee <APP_ID_DEL_SP> `
  --role AcrPush `
  --scope /subscriptions/<TU_SUBSCRIPTION_ID>/resourceGroups/<TU_RESOURCE_GROUP>/providers/Microsoft.ContainerRegistry/registries/<TU_ACR_NAME>
```

### 2. Confirmar recursos reales

Necesitas verificar y tener claros estos nombres exactos:

- `AZURE_RESOURCE_GROUP`
- `AZURE_WEBAPP_NAME`
- `AZURE_ACR_NAME`
- `AZURE_ACR_LOGIN_SERVER`
- `AZURE_SEARCH_ENDPOINT`

El workflow ya espera esos valores como variables de GitHub.

### 3. Rotar secretos expuestos en Deploy_Chatbot.bat

Como el `.bat` actual contiene secretos en texto plano, conviene rotar al menos:

- `OPENAI_API_KEY`
- `SQL_CONNECTION_STRING`
- credenciales de ACR si siguen activas
- `AZURE_SEARCH_KEY`
- `BLOB_STORAGE_CONNECTION_STRING`
- `APPREGENERA_SQL_CONNECTION_STRING`
- `APPREGENERA_DEV_BYPASS_KEY`
- `ADMIN_API_KEY`
- `SWA deployment token`

## Qué debes hacer tú en GitHub

Repositorio: `CarrilloRegenera/ChatBot`

### 1. Crear environment `production`

En GitHub:

- `Settings`
- `Environments`
- `New environment`
- nombre: `production`

### 2. Crear variables del environment o del repositorio

Puedes ponerlas a nivel de repositorio o dentro del environment `production`. Recomiendo dejarlas dentro de `production`.

Variables no sensibles:

- `AZURE_RESOURCE_GROUP`
- `AZURE_WEBAPP_NAME`
- `AZURE_ACR_NAME`
- `AZURE_ACR_LOGIN_SERVER`
- `AZURE_SEARCH_ENDPOINT`

### 3. Crear secretos del environment `production`

Secretos necesarios para el workflow inicial:

- `AZURE_CREDENTIALS`
- `AZURE_ACR_USERNAME`
- `AZURE_ACR_PASSWORD`
- `SQL_CONNECTION_STRING`
- `OPENAI_API_KEY`
- `HF_TOKEN`
- `HUGGINGFACE_HUB_TOKEN`
- `ADMIN_API_KEY`
- `AZURE_SEARCH_KEY`
- `BLOB_STORAGE_CONNECTION_STRING`
- `APPREGENERA_SQL_CONNECTION_STRING`
- `APPREGENERA_DEV_BYPASS_KEY`
- `SWA_DEPLOYMENT_TOKEN`

### 4. Subir la rama que llevará el workflow

El workflow se lanza sobre la rama donde exista el archivo. Como ahora estás trabajando con `sandbox`, necesitamos que el archivo de workflow exista en la rama desde la que lo vayas a lanzar.

## Cómo hacer la primera prueba

### Primera prueba recomendada

1. Lanzar `Deploy Chatbot`.
2. Elegir rama `sandbox`.
3. Elegir acción `backend`.

Si eso funciona:

1. Lanzar `Deploy Chatbot`.
2. Elegir rama `sandbox`.
3. Elegir acción `frontend`.

Si ambos funcionan:

1. Lanzar `Deploy Chatbot`.
2. Elegir rama `sandbox`.
3. Elegir acción `full`.

## Orden recomendado de validación

1. `backend`
2. `frontend`
3. `full`

Así sabremos exactamente dónde falla si hay algún ajuste pendiente en Azure.

## Qué haré yo después

Cuando el workflow manual esté validado:

1. Añadiré la pantalla de `Despliegues` dentro de la app.
2. Añadiré endpoints backend para disparar `workflow_dispatch`.
3. Añadiré histórico de ejecuciones.
4. Añadiré seguimiento de estado y enlace al run de GitHub.
5. Después retomaremos la automatización de `admin/sync` y de las operaciones auxiliares.
