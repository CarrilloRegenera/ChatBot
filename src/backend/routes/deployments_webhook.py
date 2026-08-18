import logging

from fastapi import APIRouter, HTTPException, Request

from config import DEPLOY_WEBHOOK_SECRET
from deployment_service import DeploymentNotificationError, notify_run_if_needed, store_webhook_run
from models import DeploymentWebhookRequest


router = APIRouter()
logger = logging.getLogger(__name__)


def _assert_deploy_webhook(request: Request) -> None:
    if not DEPLOY_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook de despliegue no configurado")
    incoming_secret = (request.headers.get("x-deploy-webhook-secret") or "").strip()
    if incoming_secret != DEPLOY_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Webhook de despliegue denegado")


@router.post("/admin/deployments/webhook")
def admin_deployment_webhook(data: DeploymentWebhookRequest, request: Request):
    _assert_deploy_webhook(request)
    try:
        payload = data.model_dump() if hasattr(data, "model_dump") else data.dict()
        saved = store_webhook_run(payload)
        notification_sent = notify_run_if_needed(int(saved["github_run_id"]))
    except DeploymentNotificationError as exc:
        logger.exception("No se pudo confirmar el correo del despliegue")
        raise HTTPException(status_code=503, detail=f"No se pudo notificar el despliegue: {exc}") from exc
    except Exception as exc:
        logger.exception("Error procesando webhook de despliegue")
        raise HTTPException(status_code=500, detail=f"No se pudo registrar el despliegue: {exc}") from exc
    return {
        "message": "Webhook de despliegue procesado y correo confirmado por Graph",
        "run_id": saved["github_run_id"],
        "notification_sent": notification_sent,
    }
