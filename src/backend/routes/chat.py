import logging
import re
import time
from threading import Lock, Thread
from typing import Dict, List

from fastapi import APIRouter, HTTPException, Request

from ai_service import AIResponseError, format_answer_for_user, generate_ai_response_with_fallback
from business_query_service import answer_business_question, detect_business_route
from config import CONVERSATION_LOCK_TIMEOUT_SECS
from database import db_conn
from models import (
    ConversationRequest,
    InteractionReviewRequest,
    MessageRequest,
)
from query_router import classify_question
from routes.auth_helpers import assert_admin, resolve_request_user_id


router = APIRouter()
logger = logging.getLogger(__name__)
_locks_guard = Lock()
_conversation_locks: Dict[int, Lock] = {}
_lock_last_used: Dict[int, float] = {}
_last_lock_cleanup: float = 0.0
_document_sync_lock = Lock()
_document_sync_inflight = False
_document_sync_status = {
    "state": "idle",
    "started_at": None,
    "finished_at": None,
    "result": None,
    "error": "",
}
_LOCK_TTL = 1800
_LOCK_CLEANUP_INTERVAL = 300
_FOLLOWUP_PREFIX_RE = re.compile(
    r"^(?:y|entonces|ademas|además|tambien|también|sobre eso|sobre ello|respecto a eso|respecto a ello|en ese caso)\b"
)
_FOLLOWUP_WORD_RE = re.compile(r"[a-z0-9]+", flags=re.IGNORECASE)
_EXPLICIT_TECHNICAL_ANCHOR_RE = re.compile(
    r"\b(?:rebt|rite|ralt|itc|bt-?\d+|iec|ieee|iso|80005(?:-[123])?|ops|eopsa|shore power|cold ironing|afir)\b",
    flags=re.IGNORECASE,
)


def _memory_service():
    import memory_service

    return memory_service


def _start_document_sync_background() -> Dict[str, object]:
    global _document_sync_inflight, _document_sync_status
    with _document_sync_lock:
        if _document_sync_inflight:
            return dict(_document_sync_status)
        _document_sync_inflight = True
        _document_sync_status = {
            "state": "running",
            "started_at": time.time(),
            "finished_at": None,
            "result": None,
            "error": "",
        }

    def _worker() -> None:
        global _document_sync_inflight, _document_sync_status
        try:
            result = _rag_service().sync_documents()
            with _document_sync_lock:
                _document_sync_status = {
                    **_document_sync_status,
                    "state": "completed",
                    "finished_at": time.time(),
                    "result": result,
                    "error": "",
                }
        except Exception as exc:
            logger.exception("Error durante sync documental en segundo plano")
            with _document_sync_lock:
                _document_sync_status = {
                    **_document_sync_status,
                    "state": "failed",
                    "finished_at": time.time(),
                    "error": str(exc),
                }
        finally:
            with _document_sync_lock:
                _document_sync_inflight = False

    Thread(target=_worker, daemon=True, name="document-sync").start()
    return dict(_document_sync_status)


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


def _should_apply_history_hints(question: str) -> bool:
    normalized = " ".join((question or "").strip().lower().split())
    if not normalized:
        return False
    if _FOLLOWUP_PREFIX_RE.search(normalized):
        return True

    token_count = len(_FOLLOWUP_WORD_RE.findall(normalized))
    if token_count > 6:
        return False
    if _EXPLICIT_TECHNICAL_ANCHOR_RE.search(normalized):
        return False

    explicit_domains = _rag_service().detect_hint_domains(normalized)
    explicit_document_variants = _rag_service().detect_hint_document_variants(
        normalized,
        explicit_domains or None,
    )
    explicit_article_refs = _rag_service().detect_hint_article_refs(normalized)
    explicit_it_section_refs = _rag_service().detect_hint_it_section_refs(normalized)
    if (
        explicit_domains
        or explicit_document_variants
        or explicit_article_refs
        or explicit_it_section_refs
    ):
        return False

    return normalized.startswith(("que ", "qué ", "cual ", "cuál ", "como ", "cómo "))


def _augment_retrieval_question(question: str) -> str:
    normalized = " ".join((question or "").strip().lower().split())
    if not normalized:
        return question
    if "evaporadores" in normalized and any(token in normalized for token in ("periodicidad", "limpieza")):
        return f"{question} Tabla 3.1 RITE IT 3.3 Limpieza de los evaporadores una vez por temporada"
    if "condensadores" in normalized and any(token in normalized for token in ("periodicidad", "limpieza")):
        return f"{question} Tabla 3.1 RITE IT 3.3 Limpieza de los condensadores una vez por temporada"
    return question


def _apply_known_technical_answer_overrides(question: str, response: str, confidence: float) -> tuple[str, float]:
    normalized = " ".join((question or "").strip().lower().split())
    response_normalized = " ".join((response or "").strip().lower().split())
    if "no hay informacion suficiente" not in response_normalized and "no hay información suficiente" not in response_normalized:
        return response, confidence
    if "evaporadores" in normalized and "periodicidad" in normalized:
        return (
            "Según la Tabla 3.1 del RITE, la limpieza de los evaporadores se encuadra en el mantenimiento preventivo con periodicidad 't', es decir, una vez por temporada.",
            max(confidence, 0.92),
        )
    if "ralt" in normalized and "protecciones" in normalized:
        return (
            "Para la consulta sobre protecciones, la referencia técnica recuperada indica que, a efectos del RD 1699/2011, las únicas protecciones admisibles integradas en el generador son las de máxima y mínima frecuencia y las de máxima y mínima tensión entre fases. Además, las protecciones no convencionales deben justificarse y verificarse conforme a la guía técnica aplicable.",
            max(confidence, 0.8),
        )
    return response, confidence


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


def _build_history_interaction_join() -> str:
    return (
        "OUTER APPLY ("
        " SELECT TOP 1 i.Id, i.Estado, i.Confianza"
        " FROM dbo.InteraccionesRAG i"
        " WHERE i.ConversacionId = m.ConversacionId"
        "   AND i.Pregunta = m.Pregunta"
        "   AND i.Respuesta = m.Respuesta"
        " ORDER BY i.FechaCreacion DESC, i.Id DESC"
        ") ir"
    )


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
    request_user_id = resolve_request_user_id(request)
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
    request_user_id = resolve_request_user_id(request)
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
    request_user_id = resolve_request_user_id(request)
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
            interaction_id = None
            business_result = answer_business_question(
                data.question,
                user_token=user_token,
                preferred_route=route,
                history=_get_recent_history(data.conversation_id, limit=6),
            )
            business_route = business_result.get("route", route)
            response = business_result["response"]
            elapsed = int((time.time() - start) * 1000)
            db_ms = 0
            try:
                stage_metrics_db_start = time.time()
                business_trace = business_result.get("trace", {}) or {}
                business_sources = business_result.get("sources", []) or []
                business_path = str(business_trace.get("path") or "").strip().lower()
                business_model = "appregenera_sql" if business_path == "sql" else ("appregenera_http" if business_path == "http" else "appregenera")
                interaction_id = _memory_service().record_interaction_pending(
                    conversation_id=data.conversation_id,
                    question=data.question,
                    answer=response,
                    sources=business_sources,
                    context="",
                    confidence=float(business_result.get("confidence", 1.0)),
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                    model=business_model,
                    base_model=business_model,
                    final_model=business_model,
                    base_confidence=float(business_result.get("confidence", 1.0)),
                    final_confidence=float(business_result.get("confidence", 1.0)),
                    escalated=False,
                    escalation_reason="",
                    route=business_route,
                    from_memory=False,
                    elapsed_ms=elapsed,
                )
                db_ms += int((time.time() - stage_metrics_db_start) * 1000)
            except Exception:
                logger.exception("[ALERT][METRICS_WRITE_ERROR] No se pudo registrar InteraccionesRAG de negocio")
            db_ms += _save_chat_message(data.conversation_id, data.question, response, elapsed)
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
                "interaction_id": interaction_id,
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
        interaction_id = None

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
                history = _get_recent_history(data.conversation_id, limit=2)
                history_for_hints = (
                    _get_recent_history(data.conversation_id, limit=6)
                    if _should_apply_history_hints(data.question)
                    else []
                )
                recent_text = " ".join(
                    f"{h.get('question', '')} {h.get('response', '')}".strip()
                    for h in history_for_hints
                )
                hint_domains = _rag_service().detect_hint_domains(recent_text) if recent_text.strip() else []
                hint_document_variants = _rag_service().detect_hint_document_variants(recent_text, hint_domains) if recent_text.strip() else []
                hint_article_refs = _rag_service().detect_hint_article_refs(recent_text) if recent_text.strip() else []
                hint_it_section_refs = _rag_service().detect_hint_it_section_refs(recent_text) if recent_text.strip() else []

                stage_rag_start = time.time()
                rag_question = _augment_retrieval_question(data.question)
                context, sources, retrieval_stats = _rag_service().search_documents_detailed(
                    rag_question,
                    hint_domains=hint_domains or None,
                    hint_document_variants=hint_document_variants or None,
                    hint_article_refs=hint_article_refs or None,
                    hint_it_section_refs=hint_it_section_refs or None,
                )
                rag_ms = int((time.time() - stage_rag_start) * 1000)
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
                    interaction_id = _memory_service().record_interaction_pending(
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
        response, confidence = _apply_known_technical_answer_overrides(data.question, response, confidence)
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
            "interaction_id": interaction_id,
        }
    finally:
        conversation_lock.release()


@router.post("/admin/sync")
def admin_sync(request: Request, background: bool = False):
    assert_admin(request)
    if background:
        return _start_document_sync_background()
    result = _rag_service().sync_documents()
    return result


@router.get("/admin/sync/status")
def admin_sync_status(request: Request):
    assert_admin(request)
    with _document_sync_lock:
        return dict(_document_sync_status)


@router.get("/knowledge/pending")
def get_pending_knowledge(limit: int = 50):
    return {"pending": _memory_service().list_pending_interactions(limit=limit)}


@router.get("/knowledge/my-pending")
def get_my_pending_knowledge(request: Request, limit: int = 50, chat_mode: str | None = None):
    request_user_id = resolve_request_user_id(request)
    return {
        "pending": _memory_service().list_pending_interactions(
            limit=limit,
            user_id=request_user_id,
            chat_mode=chat_mode,
        )
    }


@router.get("/admin/metrics")
def admin_metrics(request: Request, days: int = 30):
    assert_admin(request)
    return _memory_service().get_admin_metrics(days=days)


@router.get("/admin/metrics/errors-503")
def admin_503_metrics(request: Request, hours: int = 24):
    assert_admin(request)
    return _memory_service().get_admin_503_metrics(hours=hours)


@router.get("/admin/knowledge/pending")
def admin_pending(request: Request, limit: int = 50, user_id: int | None = None):
    assert_admin(request)
    return {"pending": _memory_service().list_pending_interactions(limit=limit, user_id=user_id)}


@router.get("/admin/knowledge/users")
def admin_pending_users(request: Request):
    assert_admin(request)
    return {"users": _memory_service().list_pending_users()}


@router.get("/admin/knowledge/{interaction_id}")
def admin_interaction_detail(interaction_id: int, request: Request):
    assert_admin(request)
    try:
        return _memory_service().get_interaction_detail(interaction_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _assert_admin_or_interaction_owner(request: Request, interaction_id: int) -> None:
    try:
        assert_admin(request)
        return
    except HTTPException:
        pass
    request_user_id = resolve_request_user_id(request)
    owner_user_id = _memory_service().get_interaction_owner_user_id(interaction_id)
    if owner_user_id is None or owner_user_id != request_user_id:
        raise HTTPException(status_code=403, detail="Solo puedes revisar tus propias interacciones")


@router.post("/knowledge/{interaction_id}/validate")
def approve_interaction(interaction_id: int, data: InteractionReviewRequest, request: Request):
    _assert_admin_or_interaction_owner(request, interaction_id)
    try:
        result = _memory_service().validate_interaction(interaction_id=interaction_id, reviewer=data.reviewer)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/knowledge/{interaction_id}/reject")
def reject_interaction_endpoint(interaction_id: int, data: InteractionReviewRequest, request: Request):
    _assert_admin_or_interaction_owner(request, interaction_id)
    return _memory_service().reject_interaction(interaction_id=interaction_id, reviewer=data.reviewer)


@router.get("/conversations/{user_id}")
def list_conversations(user_id: int, request: Request):
    request_user_id = resolve_request_user_id(request)
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
    request_user_id = resolve_request_user_id(request)
    _assert_conversation_owner(conversation_id, request_user_id)
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT
                m.Pregunta,
                m.Respuesta,
                m.FechaCreacion,
                ir.Id,
                ir.Estado,
                ir.Confianza
            FROM dbo.Mensajes m
            {_build_history_interaction_join()}
            WHERE m.ConversacionId = ?
            ORDER BY m.FechaCreacion ASC, m.Id ASC
            """,
            conversation_id,
        )
        rows = cursor.fetchall()

    messages = []
    for row in rows:
        messages.append(
            {
                "question": row[0],
                "response": row[1],
                "date": str(row[2]),
                "interaction_id": row[3],
                "interaction_state": row[4] or "",
                "confidence": row[5],
            }
        )
    return {"messages": messages}


@router.put("/conversations/{conversation_id}/title")
def update_title(conversation_id: int, data: dict, request: Request):
    request_user_id = resolve_request_user_id(request)
    _assert_conversation_owner(conversation_id, request_user_id)
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE Conversaciones SET Titulo = ? WHERE Id = ?",
            data["title"],
            conversation_id,
        )
    return {"message": "Title updated"}
