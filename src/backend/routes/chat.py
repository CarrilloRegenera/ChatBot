import logging
import time
from threading import Lock, Thread
from typing import Dict, List

from fastapi import APIRouter, HTTPException, Request, Response

from ai_service import AIResponseError, format_answer_for_user, generate_ai_response_with_fallback
from business_query_service import answer_business_question, detect_business_route
from config import ADMIN_API_KEY, ADMIN_PANEL_ALLOWED_EMAILS, CONVERSATION_LOCK_TIMEOUT_SECS, DEPLOY_WEBHOOK_SECRET, ENTRA_ADMIN_EMAILS, ENTRA_ENABLED
from database import db_conn
from deployment_service import (
    DeploymentConfigurationError,
    download_run_logs,
    get_notification_settings,
    list_deployments,
    notify_run_if_needed,
    register_webhook_run,
    store_webhook_run,
    trigger_full_deploy,
    update_notification_settings,
)
from entra_auth import validate_entra_token
from models import (
    ConversationRequest,
    DeploymentNotificationSettingsRequest,
    DeploymentTriggerRequest,
    DeploymentWebhookRequest,
    InteractionReviewRequest,
    MessageRequest,
)
from query_router import classify_question


router = APIRouter()
logger = logging.getLogger(__name__)
_locks_guard = Lock()
_conversation_locks: Dict[int, Lock] = {}
_lock_last_used: Dict[int, float] = {}
_last_lock_cleanup: float = 0.0
_LOCK_TTL = 1800
_LOCK_CLEANUP_INTERVAL = 300


def _memory_service():
    import memory_service

    return memory_service


def _rag_service():
    import rag_service

    return rag_service


def _normalize_chat_mode(value: str | None) -> str:
    return "business" if (value or "").strip().lower() == "business" else "technical"


def _infer_chat_mode_from_title(title: str | None) -> str:
    normalized = (title or "").strip().lower()
    return "business" if "negocio" in normalized else "technical"


def _get_conversation_chat_mode(conversation_id: int) -> str:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT ChatMode, Titulo FROM Conversaciones WHERE Id = ?", conversation_id)
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Conversacion no encontrada")
    return _normalize_chat_mode(row[0] or _infer_chat_mode_from_title(row[1]))


def _build_cross_mode_message(chat_mode: str, route: str) -> str:
    if chat_mode == "business":
        return (
            "Este chatbot de negocio solo responde consultas de Licitaciones y Produccion. "
            "Vuelve al selector y usa el chatbot reglamento tecnico para preguntas documentales o normativas."
        )
    if route in {"business_licitaciones", "business_produccion"}:
        return (
            "Este chatbot reglamento tecnico solo responde sobre normativa y documentacion tecnica. "
            "Vuelve al selector y usa el chatbot de negocio para consultar Licitaciones o Produccion."
        )
    return (
        "Este chatbot reglamento tecnico solo responde sobre normativa y documentacion tecnica. "
        "Formula una consulta tecnica relacionada con REBT, RITE, RALT o los documentos cargados."
    )


def _q_preview(text: str, size: int = 90) -> str:
    one_line = " ".join((text or "").split())
    if len(one_line) <= size:
        return one_line
    return one_line[:size] + "..."


def _log_chat_event(
    event: str,
    conversation_id: int,
    route: str,
    from_memory: bool,
    confidence: float,
    sources_count: int,
    elapsed_ms: int,
    question: str,
    extra: str = "",
) -> None:
    logger.info(
        "[%s] conv=%s route=%s memory=%s conf=%.2f sources=%s elapsed=%sms q=\"%s\" %s",
        event,
        conversation_id,
        route,
        "yes" if from_memory else "no",
        confidence,
        sources_count,
        elapsed_ms,
        _q_preview(question),
        extra,
    )


def _get_conversation_lock(conversation_id: int) -> Lock:
    global _last_lock_cleanup
    with _locks_guard:
        now = time.time()
        if now - _last_lock_cleanup > _LOCK_CLEANUP_INTERVAL:
            stale = [cid for cid, ts in _lock_last_used.items() if now - ts > _LOCK_TTL]
            for cid in stale:
                _conversation_locks.pop(cid, None)
                _lock_last_used.pop(cid, None)
            if stale:
                logger.debug("[LOCKS_CLEANUP] eliminados %d locks inactivos", len(stale))
            _last_lock_cleanup = now
        if conversation_id not in _conversation_locks:
            _conversation_locks[conversation_id] = Lock()
        _lock_last_used[conversation_id] = now
        return _conversation_locks[conversation_id]


def _get_recent_history(conversation_id: int, limit: int = 2) -> List[Dict]:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT Pregunta, Respuesta FROM ("
            "  SELECT TOP (?) Pregunta, Respuesta, FechaCreacion, Id"
            "  FROM Mensajes WHERE ConversacionId = ?"
            "  ORDER BY FechaCreacion DESC, Id DESC"
            ") sub ORDER BY FechaCreacion ASC, Id ASC",
            limit,
            conversation_id,
        )
        rows = cursor.fetchall()
    return [{"question": row[0], "response": format_answer_for_user(row[1], None, question=row[0])} for row in rows]


def _save_chat_message(conversation_id: int, question: str, response: str, elapsed_ms: int) -> int:
    start = time.time()
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO Mensajes (ConversacionId, Pregunta, Respuesta, TiempoRespuestaMs) VALUES (?, ?, ?, ?)",
            conversation_id,
            question,
            response,
            elapsed_ms,
        )
    return int((time.time() - start) * 1000)


def _assert_admin(request: Request) -> None:
    auth_header = (request.headers.get("authorization") or "").strip()
    if ENTRA_ENABLED and auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        try:
            claims = validate_entra_token(token)
        except Exception as exc:
            raise HTTPException(status_code=401, detail=f"Token Entra no válido: {exc}") from exc
        email = (
            claims.get("preferred_username")
            or claims.get("email")
            or claims.get("upn")
            or ""
        ).strip().lower()
        if email and email in ADMIN_PANEL_ALLOWED_EMAILS:
            return
        raise HTTPException(status_code=403, detail="Acceso solo para administradores de Entra")

    admin_key = (request.headers.get("x-admin-key") or "").strip()
    role = (request.headers.get("x-user-role") or "").strip().lower()
    user_name = (request.headers.get("x-user-name") or "").strip().lower()
    user_email = (request.headers.get("x-user-email") or "").strip().lower()
    auth_provider = (request.headers.get("x-auth-provider") or "").strip().lower()
    is_local_admin = auth_provider == "local" and user_name == "admin"
    is_allowed_entra_email = bool(user_email and user_email in ADMIN_PANEL_ALLOWED_EMAILS)
    if role != "administrador" or not (is_local_admin or is_allowed_entra_email):
        raise HTTPException(status_code=403, detail="Acceso solo para rol Administrador")
    if admin_key and ADMIN_API_KEY and admin_key != ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Acceso admin denegado")


def _assert_deploy_webhook(request: Request) -> None:
    if not DEPLOY_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook de despliegue no configurado")
    incoming_secret = (request.headers.get("x-deploy-webhook-secret") or "").strip()
    if incoming_secret != DEPLOY_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Webhook de despliegue denegado")


def _load_user_by_id(user_id: int) -> tuple | None:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT TOP 1 Id, Nombre, Email, Rol, AuthProvider FROM Usuarios WHERE Id = ?",
            user_id,
        )
        return cursor.fetchone()


def _resolve_request_user_id(request: Request) -> int:
    auth_header = (request.headers.get("authorization") or "").strip()
    if ENTRA_ENABLED and auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        try:
            claims = validate_entra_token(token)
        except Exception as exc:
            raise HTTPException(status_code=401, detail=f"Token Entra no valido: {exc}") from exc

        email = (
            claims.get("preferred_username")
            or claims.get("email")
            or claims.get("upn")
            or ""
        ).strip().lower()
        if not email:
            raise HTTPException(status_code=401, detail="El token de Entra no contiene email")

        with db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT TOP 1 Id FROM Usuarios WHERE LOWER(Email) = ?", email)
            row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Usuario de Entra no sincronizado en el chatbot")
        return int(row[0])

    user_id_header = (request.headers.get("x-user-id") or "").strip()
    if not user_id_header.isdigit():
        raise HTTPException(status_code=401, detail="Identidad de usuario no disponible")

    user_id = int(user_id_header)
    user_row = _load_user_by_id(user_id)
    if not user_row:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")

    header_provider = (request.headers.get("x-auth-provider") or "").strip().lower()
    header_email = (request.headers.get("x-user-email") or "").strip().lower()
    if header_provider and str(user_row[4] or "").strip().lower() not in {"", header_provider}:
        raise HTTPException(status_code=403, detail="La sesion no coincide con el proveedor del usuario")
    if header_email and str(user_row[2] or "").strip().lower() not in {"", header_email}:
        raise HTTPException(status_code=403, detail="La sesion no coincide con el email del usuario")
    return user_id


def _assert_conversation_owner(conversation_id: int, request_user_id: int) -> tuple:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT Id, UsuarioId, Titulo, ChatMode FROM Conversaciones WHERE Id = ?",
            conversation_id,
        )
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Conversacion no encontrada")
    if int(row[1]) != int(request_user_id):
        raise HTTPException(status_code=403, detail="No tienes acceso a esa conversacion")
    return row


@router.post("/conversations")
def create_conversation(data: ConversationRequest, request: Request):
    request_user_id = _resolve_request_user_id(request)
    if int(data.user_id) != request_user_id:
        raise HTTPException(status_code=403, detail="No puedes crear conversaciones para otro usuario")
    chat_mode = _normalize_chat_mode(data.chat_mode)
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO Conversaciones (UsuarioId, Titulo, ChatMode) OUTPUT INSERTED.Id VALUES (?, ?, ?)",
            data.user_id,
            data.title,
            chat_mode,
        )
        conversation_id = cursor.fetchone()[0]
    return {"message": "Conversacion Creada", "conversation_id": conversation_id, "chat_mode": chat_mode}


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: int, request: Request):
    request_user_id = _resolve_request_user_id(request)
    _assert_conversation_owner(conversation_id, request_user_id)
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT Id FROM Conversaciones WHERE Id = ?", conversation_id)
        existing = cursor.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Conversacion no encontrada")

        cursor.execute("DELETE FROM Mensajes WHERE ConversacionId = ?", conversation_id)
        cursor.execute("DELETE FROM Conversaciones WHERE Id = ?", conversation_id)

    with _locks_guard:
        _conversation_locks.pop(conversation_id, None)

    return {"message": "Conversacion eliminada", "conversation_id": conversation_id}


@router.post("/messages")
def send_message(data: MessageRequest, request: Request):
    request_user_id = _resolve_request_user_id(request)
    _assert_conversation_owner(data.conversation_id, request_user_id)
    conversation_lock = _get_conversation_lock(data.conversation_id)
    acquired = conversation_lock.acquire(timeout=CONVERSATION_LOCK_TIMEOUT_SECS)
    if not acquired:
        logger.warning(
            "[LOCK_TIMEOUT] conv=%s waited=%ss q=\"%s\"",
            data.conversation_id,
            CONVERSATION_LOCK_TIMEOUT_SECS,
            _q_preview(data.question),
        )
        raise HTTPException(
            status_code=429,
            detail=(
                "La conversacion sigue procesando una solicitud anterior. "
                "Vuelve a intentarlo en unos segundos."
            ),
        )

    try:
        start = time.time()
        chat_mode = _get_conversation_chat_mode(data.conversation_id)
        stage_router_start = time.time()
        route_info = classify_question(data.question)
        route = route_info["route"]
        business_route_hint = detect_business_route(data.question)
        if business_route_hint:
            route = business_route_hint
        router_ms = int((time.time() - stage_router_start) * 1000)

        if chat_mode == "business":
            if route not in {"business_licitaciones", "business_produccion"}:
                response = _build_cross_mode_message(chat_mode, route)
                elapsed = int((time.time() - start) * 1000)
                db_ms = _save_chat_message(data.conversation_id, data.question, response, elapsed)
                _log_chat_event(
                    event="CHAT",
                    conversation_id=data.conversation_id,
                    route="business_scope_mismatch",
                    from_memory=False,
                    confidence=1.0,
                    sources_count=0,
                    elapsed_ms=elapsed,
                    question=data.question,
                    extra=f"router_ms={router_ms} rag_ms=0 llm_ms=0 db_ms={db_ms}",
                )
                return {
                    "question": data.question,
                    "response": response,
                    "confidence": 1.0,
                    "from_memory": False,
                    "route": "business_scope_mismatch",
                }
        else:
            if route in {"business_licitaciones", "business_produccion"}:
                response = _build_cross_mode_message(chat_mode, route)
                elapsed = int((time.time() - start) * 1000)
                db_ms = _save_chat_message(data.conversation_id, data.question, response, elapsed)
                _log_chat_event(
                    event="CHAT",
                    conversation_id=data.conversation_id,
                    route="technical_scope_mismatch",
                    from_memory=False,
                    confidence=1.0,
                    sources_count=0,
                    elapsed_ms=elapsed,
                    question=data.question,
                    extra=f"router_ms={router_ms} rag_ms=0 llm_ms=0 db_ms={db_ms}",
                )
                return {
                    "question": data.question,
                    "response": response,
                    "confidence": 1.0,
                    "from_memory": False,
                    "route": "technical_scope_mismatch",
                }

        if route in {"invalid", "smalltalk", "out_of_scope"}:
            response = route_info["message"]
            elapsed = int((time.time() - start) * 1000)
            db_ms = _save_chat_message(data.conversation_id, data.question, response, elapsed)
            _log_chat_event(
                event="CHAT",
                conversation_id=data.conversation_id,
                route=route,
                from_memory=False,
                confidence=(1.0 if route in {"invalid", "smalltalk"} else 0.9),
                sources_count=0,
                elapsed_ms=elapsed,
                question=data.question,
                extra=f"router_ms={router_ms} rag_ms=0 llm_ms=0 db_ms={db_ms}",
            )
            return {
                "question": data.question,
                "response": response,
                "confidence": 1.0 if route in {"invalid", "smalltalk"} else 0.9,
                "from_memory": False,
                "route": route,
            }

        if route in {"business_licitaciones", "business_produccion"}:
            auth_header = (request.headers.get("authorization") or "").strip()
            user_token = auth_header.split(" ", 1)[1].strip() if auth_header.lower().startswith("bearer ") else None
            business_result = answer_business_question(
                data.question,
                user_token=user_token,
                preferred_route=route,
                history=_get_recent_history(data.conversation_id, limit=6),
            )
            business_route = business_result.get("route", route)
            response = business_result["response"]
            elapsed = int((time.time() - start) * 1000)
            db_ms = _save_chat_message(data.conversation_id, data.question, response, elapsed)
            _log_chat_event(
                event="CHAT",
                conversation_id=data.conversation_id,
                route=business_route,
                from_memory=False,
                confidence=float(business_result.get("confidence", 1.0)),
                sources_count=len(business_result.get("sources", [])),
                elapsed_ms=elapsed,
                question=data.question,
                extra=f"router_ms={router_ms} rag_ms=0 llm_ms=0 db_ms={db_ms}",
            )
            return {
                "question": data.question,
                "response": response,
                "confidence": float(business_result.get("confidence", 1.0)),
                "from_memory": False,
                "sources": business_result.get("sources", []),
                "route": business_route,
                "trace": business_result.get("trace", {}),
            }

        context = ""
        sources = []
        retrieval_stats = {}
        confidence = 0.0
        from_memory = False
        trace = {}
        rag_ms = 0
        llm_ms = 0
        db_ms = 0
        llm_retries = 0

        try:
            memory_hit = _memory_service().search_validated_memory(data.question)
            if memory_hit:
                response = memory_hit["answer"]
                sources = memory_hit.get("sources", [])
                confidence = max(0.9, 1.0 - memory_hit.get("distance", 0.0))
                response = format_answer_for_user(response, sources, question=data.question)
                from_memory = True
                _log_chat_event(
                    event="MEMORY_HIT",
                    conversation_id=data.conversation_id,
                    route="knowledge",
                    from_memory=True,
                    confidence=confidence,
                    sources_count=len(sources),
                    elapsed_ms=int((time.time() - start) * 1000),
                    question=data.question,
                    extra=f"distance={float(memory_hit.get('distance', 0.0)):.4f}",
                )
            else:
                stage_rag_start = time.time()
                context, sources, retrieval_stats = _rag_service().search_documents_detailed(data.question)
                rag_ms = int((time.time() - stage_rag_start) * 1000)

                history = _get_recent_history(data.conversation_id, limit=2)
                stage_llm_start = time.time()
                try:
                    generated = generate_ai_response_with_fallback(
                        data.question,
                        context=context,
                        sources=sources,
                        history=history,
                        retrieval_stats=retrieval_stats,
                    )
                finally:
                    llm_ms = int((time.time() - stage_llm_start) * 1000)
                response = generated["text"]
                llm_retries = int(generated.get("retries", 0))
                confidence = generated.get("confidence", 0.0)
                trace = {
                    "base_model": generated.get("base_model", ""),
                    "final_model": generated.get("final_model") or generated.get("model", ""),
                    "base_confidence": generated.get("base_confidence"),
                    "final_confidence": generated.get("final_confidence", confidence),
                    "escalated": bool(generated.get("escalated", False)),
                    "escalation_reason": generated.get("escalation_reason", ""),
                }
                response = format_answer_for_user(response, sources, question=data.question)
                elapsed_partial = int((time.time() - start) * 1000)
                try:
                    stage_metrics_db_start = time.time()
                    _memory_service().record_interaction_pending(
                        conversation_id=data.conversation_id,
                        question=data.question,
                        answer=response,
                        sources=sources,
                        context=context,
                        confidence=confidence,
                        prompt_tokens=generated["usage"]["prompt_tokens"],
                        completion_tokens=generated["usage"]["completion_tokens"],
                        total_tokens=generated["usage"]["total_tokens"],
                        model=trace["final_model"],
                        base_model=trace["base_model"],
                        final_model=trace["final_model"],
                        base_confidence=trace["base_confidence"],
                        final_confidence=trace["final_confidence"],
                        escalated=trace["escalated"],
                        escalation_reason=trace["escalation_reason"],
                        route="knowledge",
                        from_memory=False,
                        elapsed_ms=elapsed_partial,
                    )
                    db_ms += int((time.time() - stage_metrics_db_start) * 1000)
                except Exception:
                    logger.exception("[ALERT][METRICS_WRITE_ERROR] No se pudo registrar InteraccionesRAG")
        except AIResponseError as exc:
            llm_retries = max(llm_retries, int(getattr(exc, "retries", 0) or 0))
            logger.exception(
                "[ALERT][CHAT_ERROR] Error LLM en /messages status=%s transient=%s retries=%s",
                getattr(exc, "status_code", None),
                "yes" if getattr(exc, "transient", False) else "no",
                llm_retries,
            )
            if getattr(exc, "transient", False):
                response = (
                    "El modelo no ha podido responder por saturacion temporal del servicio. "
                    "Vuelve a intentarlo en unos segundos."
                )
            else:
                response = (
                    "No he podido generar respuesta en este momento por un error del modelo. "
                    "Vuelve a intentarlo en unos segundos."
                )
            confidence = 0.0
        except Exception:
            logger.exception("[ALERT][CHAT_ERROR] Error en procesamiento de /messages")
            response = (
                "No he podido generar respuesta en este momento por un error temporal. "
                "Vuelve a intentarlo en unos segundos."
            )
            confidence = 0.0

        elapsed = int((time.time() - start) * 1000)
        db_ms += _save_chat_message(data.conversation_id, data.question, response, elapsed)

        if llm_retries > 0:
            logger.warning("[ALERT][LLM_RETRY] conv=%s retries=%s q=\"%s\"", data.conversation_id, llm_retries, _q_preview(data.question))
        if elapsed > 8000:
            logger.warning(
                "[ALERT][SLOW_REQUEST] conv=%s elapsed=%sms router_ms=%s rag_ms=%s llm_ms=%s db_ms=%s",
                data.conversation_id, elapsed, router_ms, rag_ms, llm_ms, db_ms
            )

        _log_chat_event(
            event="CHAT",
            conversation_id=data.conversation_id,
            route="knowledge",
            from_memory=from_memory,
            confidence=confidence,
            sources_count=len(sources),
            elapsed_ms=elapsed,
            question=data.question,
            extra=f"router_ms={router_ms} rag_ms={rag_ms} llm_ms={llm_ms} db_ms={db_ms} retries={llm_retries}",
        )

        return {
            "question": data.question,
            "response": response,
            "confidence": confidence,
            "from_memory": from_memory,
            "sources": sources,
            "route": "knowledge",
            "trace": trace,
        }
    finally:
        conversation_lock.release()


@router.post("/admin/sync")
def admin_sync(request: Request):
    _assert_admin(request)
    result = _rag_service().sync_documents()
    return result


@router.get("/knowledge/pending")
def get_pending_knowledge(limit: int = 50):
    return {"pending": _memory_service().list_pending_interactions(limit=limit)}


@router.get("/admin/metrics")
def admin_metrics(request: Request, days: int = 30):
    _assert_admin(request)
    return _memory_service().get_admin_metrics(days=days)


@router.get("/admin/metrics/errors-503")
def admin_503_metrics(request: Request, hours: int = 24):
    _assert_admin(request)
    return _memory_service().get_admin_503_metrics(hours=hours)


@router.get("/admin/knowledge/pending")
def admin_pending(request: Request, limit: int = 50):
    _assert_admin(request)
    return {"pending": _memory_service().list_pending_interactions(limit=limit)}


@router.get("/admin/deployments")
def admin_list_deployments(request: Request, limit: int = 40):
    _assert_admin(request)
    return {
        "deployments": list_deployments(limit=limit),
        "settings": get_notification_settings(),
    }


@router.post("/admin/deployments/run")
def admin_run_deployment(data: DeploymentTriggerRequest, request: Request):
    _assert_admin(request)
    request_user_id = _resolve_request_user_id(request)
    user_row = _load_user_by_id(request_user_id)
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
    _assert_admin(request)
    return get_notification_settings()


@router.put("/admin/deployments/settings")
def admin_update_deployment_settings(data: DeploymentNotificationSettingsRequest, request: Request):
    _assert_admin(request)
    request_user_id = _resolve_request_user_id(request)
    user_row = _load_user_by_id(request_user_id)
    updated_by = str(user_row[2] or user_row[1] or "admin") if user_row else "admin"
    try:
        return update_notification_settings(data.recipients, updated_by=updated_by)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/admin/deployments/{run_id}/logs")
def admin_download_deployment_logs(run_id: int, request: Request):
    _assert_admin(request)
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


@router.post("/admin/deployments/webhook")
def admin_deployment_webhook(data: DeploymentWebhookRequest, request: Request):
    _assert_deploy_webhook(request)
    try:
        saved = store_webhook_run(data.model_dump())
        Thread(
            target=notify_run_if_needed,
            args=(int(saved["github_run_id"]),),
            daemon=True,
            name=f"deploy-notify-{saved['github_run_id']}",
        ).start()
    except Exception as exc:
        logger.exception("Error procesando webhook de despliegue")
        raise HTTPException(status_code=500, detail=f"No se pudo registrar el despliegue: {exc}") from exc
    return {"message": "Webhook de despliegue procesado", "run_id": saved["github_run_id"]}


@router.post("/knowledge/{interaction_id}/validate")
def approve_interaction(interaction_id: int, data: InteractionReviewRequest, request: Request):
    _assert_admin(request)
    try:
        result = _memory_service().validate_interaction(interaction_id=interaction_id, reviewer=data.reviewer)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/knowledge/{interaction_id}/reject")
def reject_interaction_endpoint(interaction_id: int, data: InteractionReviewRequest, request: Request):
    _assert_admin(request)
    return _memory_service().reject_interaction(interaction_id=interaction_id, reviewer=data.reviewer)


@router.get("/conversations/{user_id}")
def list_conversations(user_id: int, request: Request):
    request_user_id = _resolve_request_user_id(request)
    if int(user_id) != request_user_id:
        raise HTTPException(status_code=403, detail="No puedes consultar conversaciones de otro usuario")
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT Id, Titulo, Estado, FechaCreacion, ChatMode FROM Conversaciones WHERE UsuarioId = ? ORDER BY FechaCreacion DESC, Id DESC",
            user_id,
        )
        rows = cursor.fetchall()

    conversations = []
    for row in rows:
        conversations.append(
            {
                "id": row[0],
                "title": row[1],
                "status": row[2],
                "date": str(row[3]),
                "mode": _normalize_chat_mode(row[4] or _infer_chat_mode_from_title(row[1])),
            }
        )
    return {"conversations": conversations}


@router.get("/conversations/{conversation_id}/messages")
def get_history(conversation_id: int, request: Request):
    request_user_id = _resolve_request_user_id(request)
    _assert_conversation_owner(conversation_id, request_user_id)
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT Pregunta, Respuesta, FechaCreacion FROM Mensajes WHERE ConversacionId = ? ORDER BY FechaCreacion ASC, Id ASC",
            conversation_id,
        )
        rows = cursor.fetchall()

    messages = []
    for row in rows:
        messages.append({"question": row[0], "response": row[1], "date": str(row[2])})
    return {"messages": messages}


@router.put("/conversations/{conversation_id}/title")
def update_title(conversation_id: int, data: dict, request: Request):
    request_user_id = _resolve_request_user_id(request)
    _assert_conversation_owner(conversation_id, request_user_id)
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE Conversaciones SET Titulo = ? WHERE Id = ?",
            data["title"],
            conversation_id,
        )
    return {"message": "Title updated"}
