import logging
import time
from threading import Lock
from typing import Dict, List

from fastapi import APIRouter, HTTPException

from ai_service import AIResponseError, format_answer_for_user, generate_ai_response_with_fallback
from config import CONVERSATION_LOCK_TIMEOUT_SECS
from database import get_connection
from memory_service import (
    get_admin_metrics,
    get_admin_503_metrics,
    list_pending_interactions,
    record_interaction_pending,
    reject_interaction,
    search_validated_memory,
    validate_interaction,
)
from models import (
    ConversationRequest,
    InteractionReviewRequest,
    MessageRequest,
)
from query_router import classify_question
from rag_service import search_documents_detailed, sync_documents


router = APIRouter()
logger = logging.getLogger(__name__)
_locks_guard = Lock()
_conversation_locks: Dict[int, Lock] = {}
_lock_last_used: Dict[int, float] = {}
_last_lock_cleanup: float = 0.0
_LOCK_TTL = 1800
_LOCK_CLEANUP_INTERVAL = 300


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
    conn = get_connection()
    cursor = conn.cursor()
    try:
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
    finally:
        conn.close()
    return [{"question": row[0], "response": format_answer_for_user(row[1], None, question=row[0])} for row in rows]


def _save_chat_message(conversation_id: int, question: str, response: str, elapsed_ms: int) -> int:
    start = time.time()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO Mensajes (ConversacionId, Pregunta, Respuesta, TiempoRespuestaMs) VALUES (?, ?, ?, ?)",
            conversation_id,
            question,
            response,
            elapsed_ms,
        )
        conn.commit()
    finally:
        conn.close()
    return int((time.time() - start) * 1000)


def _assert_admin(role: str) -> None:
    if (role or "").strip().lower() != "administrador":
        raise HTTPException(status_code=403, detail="Acceso solo para rol Administrador")


@router.post("/conversations")
def create_conversation(data: ConversationRequest):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO Conversaciones (UsuarioId, Titulo) OUTPUT INSERTED.Id VALUES (?, ?)",
        data.user_id,
        data.title,
    )
    conversation_id = cursor.fetchone()[0]
    conn.commit()
    conn.close()
    return {"message": "Conversacion Creada", "conversation_id": conversation_id}


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT Id FROM Conversaciones WHERE Id = ?", conversation_id)
        existing = cursor.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Conversacion no encontrada")

        cursor.execute("DELETE FROM Mensajes WHERE ConversacionId = ?", conversation_id)
        cursor.execute("DELETE FROM Conversaciones WHERE Id = ?", conversation_id)
        conn.commit()
    finally:
        conn.close()

    with _locks_guard:
        _conversation_locks.pop(conversation_id, None)

    return {"message": "Conversacion eliminada", "conversation_id": conversation_id}


@router.post("/messages")
def send_message(data: MessageRequest):
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
        stage_router_start = time.time()
        route_info = classify_question(data.question)
        route = route_info["route"]
        router_ms = int((time.time() - stage_router_start) * 1000)

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
            memory_hit = search_validated_memory(data.question)
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
                context, sources, retrieval_stats = search_documents_detailed(data.question)
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
                    record_interaction_pending(
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
def admin_sync(role: str):
    _assert_admin(role)
    result = sync_documents()
    return result


@router.get("/knowledge/pending")
def get_pending_knowledge(limit: int = 50):
    return {"pending": list_pending_interactions(limit=limit)}


@router.get("/admin/metrics")
def admin_metrics(role: str, days: int = 30):
    _assert_admin(role)
    return get_admin_metrics(days=days)


@router.get("/admin/metrics/errors-503")
def admin_503_metrics(role: str, hours: int = 24):
    _assert_admin(role)
    return get_admin_503_metrics(hours=hours)


@router.get("/admin/knowledge/pending")
def admin_pending(role: str, limit: int = 50):
    _assert_admin(role)
    return {"pending": list_pending_interactions(limit=limit)}


@router.post("/knowledge/{interaction_id}/validate")
def approve_interaction(interaction_id: int, data: InteractionReviewRequest):
    try:
        result = validate_interaction(interaction_id=interaction_id, reviewer=data.reviewer)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/knowledge/{interaction_id}/reject")
def reject_interaction_endpoint(interaction_id: int, data: InteractionReviewRequest):
    return reject_interaction(interaction_id=interaction_id, reviewer=data.reviewer)


@router.get("/conversations/{user_id}")
def list_conversations(user_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT Id, Titulo, Estado, FechaCreacion FROM Conversaciones WHERE UsuarioId = ?",
        user_id,
    )
    rows = cursor.fetchall()
    conn.close()

    conversations = []
    for row in rows:
        conversations.append(
            {
                "id": row[0],
                "title": row[1],
                "status": row[2],
                "date": str(row[3]),
            }
        )
    return {"conversations": conversations}


@router.get("/conversations/{conversation_id}/messages")
def get_history(conversation_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT Pregunta, Respuesta, FechaCreacion FROM Mensajes WHERE ConversacionId = ? ORDER BY FechaCreacion ASC, Id ASC",
        conversation_id,
    )
    rows = cursor.fetchall()
    conn.close()

    messages = []
    for row in rows:
        messages.append({"question": row[0], "response": row[1], "date": str(row[2])})
    return {"messages": messages}


@router.put("/conversations/{conversation_id}/title")
def update_title(conversation_id: int, data: dict):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE Conversaciones SET Titulo = ? WHERE Id = ?",
        data["title"],
        conversation_id,
    )
    conn.commit()
    conn.close()
    return {"message": "Title updated"}
