import logging
import time
from threading import Lock

from fastapi import APIRouter, HTTPException

from ai_service import get_ai_response, validate_answer
from database import get_connection
from memory_service import (
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
from rag_service import search_documents


router = APIRouter()
logger = logging.getLogger(__name__)
_locks_guard = Lock()
_conversation_locks = {}


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
    with _locks_guard:
        if conversation_id not in _conversation_locks:
            _conversation_locks[conversation_id] = Lock()
        return _conversation_locks[conversation_id]


def _save_chat_message(conversation_id: int, question: str, response: str, elapsed_ms: int) -> None:
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


@router.post("/messages")
def send_message(data: MessageRequest):
    conversation_lock = _get_conversation_lock(data.conversation_id)

    with conversation_lock:
        start = time.time()
        route_info = classify_question(data.question)
        route = route_info["route"]

        if route in {"invalid", "smalltalk", "out_of_scope"}:
            response = route_info["message"]
            elapsed = int((time.time() - start) * 1000)
            _save_chat_message(data.conversation_id, data.question, response, elapsed)
            _log_chat_event(
                event="CHAT",
                conversation_id=data.conversation_id,
                route=route,
                from_memory=False,
                confidence=(1.0 if route in {"invalid", "smalltalk"} else 0.9),
                sources_count=0,
                elapsed_ms=elapsed,
                question=data.question,
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
        confidence = 0.0
        from_memory = False

        try:
            memory_hit = search_validated_memory(data.question)
            if memory_hit:
                response = memory_hit["answer"]
                sources = memory_hit.get("sources", [])
                confidence = max(0.9, 1.0 - memory_hit.get("distance", 0.0))
                if sources and "Fuentes:" not in response:
                    response = f"{response}\nFuentes: {', '.join(sources)}"
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
                context, sources = search_documents(data.question)
                response = get_ai_response(data.question, context=context, sources=sources)
                response, confidence = validate_answer(response, context=context, sources=sources)
                record_interaction_pending(
                    conversation_id=data.conversation_id,
                    question=data.question,
                    answer=response,
                    sources=sources,
                    context=context,
                    confidence=confidence,
                )
        except Exception:
            logger.exception("Error en procesamiento de /messages")
            response = (
                "No he podido generar respuesta en este momento por un error temporal. "
                "Vuelve a intentarlo en unos segundos."
            )
            confidence = 0.0

        elapsed = int((time.time() - start) * 1000)
        _save_chat_message(data.conversation_id, data.question, response, elapsed)
        _log_chat_event(
            event="CHAT",
            conversation_id=data.conversation_id,
            route="knowledge",
            from_memory=from_memory,
            confidence=confidence,
            sources_count=len(sources),
            elapsed_ms=elapsed,
            question=data.question,
        )

        return {
            "question": data.question,
            "response": response,
            "confidence": confidence,
            "from_memory": from_memory,
            "sources": sources,
            "route": "knowledge",
        }


@router.get("/knowledge/pending")
def get_pending_knowledge(limit: int = 50):
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
        "SELECT Pregunta, Respuesta, FechaCreacion FROM Mensajes WHERE ConversacionId = ?",
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
