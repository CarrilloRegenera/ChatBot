import logging
from threading import Lock, Thread

from fastapi import APIRouter, HTTPException, Request, Response

from deployment_service import (
    DeploymentConfigurationError,
    download_run_logs,
    get_notification_settings,
    list_deployments,
    sync_recent_deployments,
    trigger_full_deploy,
    update_notification_settings,
)
from models import DeploymentNotificationSettingsRequest, DeploymentTriggerRequest
from routes.auth_helpers import assert_admin, load_user_by_id, resolve_request_user_id


router = APIRouter()
logger = logging.getLogger(__name__)
_deploy_sync_lock = Lock()
_deploy_sync_inflight = False


def _sync_recent_deployments_background(limit: int, page: int) -> None:
    global _deploy_sync_inflight
    with _deploy_sync_lock:
        if _deploy_sync_inflight:
            return
        _deploy_sync_inflight = True

    def _worker() -> None:
        global _deploy_sync_inflight
        try:
            sync_recent_deployments(limit=limit, page=page)
        except Exception:
            logger.exception("No se pudo actualizar el historico de despliegues en segundo plano")
        finally:
            with _deploy_sync_lock:
                _deploy_sync_inflight = False

    Thread(target=_worker, daemon=True, name=f"deploy-history-sync-p{page}").start()


@router.get("/admin/deployments")
def admin_list_deployments(request: Request, page: int = 1, page_size: int = 25):
    assert_admin(request)
    current_page = max(1, int(page or 1))
    size = max(1, min(int(page_size or 25), 50))
    page_data = list_deployments(page=current_page, page_size=size)
    page_data["settings"] = get_notification_settings()
    _sync_recent_deployments_background(limit=size, page=current_page)
    return page_data


@router.post("/admin/deployments/run")
def admin_run_deployment(data: DeploymentTriggerRequest, request: Request):
    assert_admin(request)
    request_user_id = resolve_request_user_id(request)
    user_row = load_user_by_id(request_user_id)
    requested_by_name = str(user_row[1] or "").strip() if user_row else ""
    requested_by_email = str(user_row[2] or "").strip().lower() if user_row else ""
    try:
        return trigger_full_deploy(
            requested_by_email=requested_by_email,
            requested_by_name=requested_by_name,
            branch=data.branch,
        )
    except DeploymentConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"No se pudo lanzar el despliegue: {exc}") from exc


@router.get("/admin/deployments/settings")
def admin_get_deployment_settings(request: Request):
    assert_admin(request)
    return get_notification_settings()


@router.put("/admin/deployments/settings")
def admin_update_deployment_settings(data: DeploymentNotificationSettingsRequest, request: Request):
    assert_admin(request)
    request_user_id = resolve_request_user_id(request)
    user_row = load_user_by_id(request_user_id)
    updated_by = str(user_row[2] or user_row[1] or "admin") if user_row else "admin"
    try:
        return update_notification_settings(data.recipients, updated_by=updated_by)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/admin/deployments/{run_id}/logs")
def admin_download_deployment_logs(run_id: int, request: Request):
    assert_admin(request)
    try:
        archive, filename = download_run_logs(run_id)
    except DeploymentConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo descargar el log completo: {exc}") from exc
    return Response(
        content=archive,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
