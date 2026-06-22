# ChatBot

Repositorio del chatbot tecnico y de negocio con backend FastAPI y frontend estatico.

## Backend

- Codigo: `src/backend/`
- Tests: `pytest src/backend/tests -q`
- Health local: `GET /health` o `GET /api/health`

## Frontend

- Codigo: `src/frontend/`
- El backend monta el frontend en `/ui` cuando se ejecuta en local.

## Despliegue

- Workflow principal: `.github/workflows/deploy-chatbot.yml`
- Rutas operativas de despliegue: `src/backend/routes/admin_deployments.py`
- Webhook de notificacion: `src/backend/routes/deployments_webhook.py`

## Higiene del repo

- Los artefactos locales de Chroma, logs de App Service y caches de pytest deben quedar sin versionar.
- La documentacion viva del proyecto si se versiona en `docs/`.
