import time
import logging
from threading import Lock

from fastapi import APIRouter
from models import ConversationRequest, MessageRequest
from database import get_connection
from rag_service import search_documents
from ai_service import get_ai_response

router = APIRouter()
logger = logging.getLogger(__name__)
_locks_guard = Lock()
_conversation_locks = {}


def _get_conversation_lock(conversation_id: int) -> Lock:
    with _locks_guard:
        if conversation_id not in _conversation_locks:
            _conversation_locks[conversation_id] = Lock()
        return _conversation_locks[conversation_id]

@router.post("/conversations")
def create_conversation(data: ConversationRequest):
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute(
        "INSERT INTO Conversaciones (UsuarioId, Titulo) OUTPUT INSERTED.Id VALUES (?, ?)",
        data.user_id, data.title
    )

    conversation_id = cursor.fetchone()[0]
    conn.commit()
    conn.close()
    
    return {
        "message": "Conversacion Creada",
        "conversation_id": conversation_id
    }

@router.post("/messages")
def send_message(data: MessageRequest):
    conversation_lock = _get_conversation_lock(data.conversation_id)

    with conversation_lock:
        conn = get_connection()
        cursor = conn.cursor()

        start = time.time()

        try:
            context, sources = search_documents(data.question)
            try:
                response = get_ai_response(data.question, context=context, sources=sources)
            except Exception:
                logger.exception("Error en generacion de respuesta para conversation_id=%s", data.conversation_id)
                response = (
                    "No he podido generar respuesta en este momento por un error temporal. "
                    "Vuelve a intentarlo en unos segundos."
                )

            elapsed = int((time.time() - start) * 1000)

            cursor.execute(
                "INSERT INTO Mensajes (ConversacionId, Pregunta, Respuesta, TiempoRespuestaMs) VALUES (?, ?, ?, ?)",
                data.conversation_id, data.question, response, elapsed
            )
            conn.commit()

            return {
                "question": data.question,
                "response": response
            }
        finally:
            conn.close()

@router.get("/conversations/{user_id}")
def list_conversations(user_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT Id, Titulo, Estado, FechaCreacion FROM Conversaciones WHERE UsuarioId = ?",
        user_id
    )
    rows = cursor.fetchall()
    conn.close()
    
    conversations = []
    for row in rows:
        conversations.append({
            "id": row[0],
            "title": row[1],
            "status": row[2],
            "date": str(row[3])
        })
    
    return {"conversations": conversations}

@router.get("/conversations/{conversation_id}/messages")
def get_history(conversation_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT Pregunta, Respuesta, FechaCreacion FROM Mensajes WHERE ConversacionId = ?",
        conversation_id
    )
    rows = cursor.fetchall()
    conn.close()
    
    messages = []
    for row in rows:
        messages.append({
            "question": row[0],
            "response": row[1],
            "date": str(row[2])
        })
    
    return {"messages": messages}

@router.put("/conversations/{conversation_id}/title")
def update_title(conversation_id: int, data: dict):
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute(
        "UPDATE Conversaciones SET Titulo = ? WHERE Id = ?",
        data["title"], conversation_id
    )
    conn.commit()
    conn.close()
    
    return {"message": "Title updated"}
