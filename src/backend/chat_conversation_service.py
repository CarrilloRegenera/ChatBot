import time
from typing import Callable, Dict, List

from fastapi import HTTPException


def normalize_chat_mode(value: str | None) -> str:
    return "business" if (value or "").strip().lower() == "business" else "technical"


def infer_chat_mode_from_title(title: str | None) -> str:
    normalized = (title or "").strip().lower()
    return "business" if "negocio" in normalized else "technical"


def get_conversation_chat_mode(
    conversation_id: int,
    *,
    db_conn: Callable,
) -> str:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT ChatMode, Titulo FROM Conversaciones WHERE Id = ?", conversation_id)
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Conversacion no encontrada")
    return normalize_chat_mode(row[0] or infer_chat_mode_from_title(row[1]))


def build_cross_mode_message(chat_mode: str, route: str) -> str:
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


def get_recent_history(
    conversation_id: int,
    *,
    db_conn: Callable,
    format_answer_for_user: Callable,
    limit: int = 2,
) -> List[Dict]:
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
    return [
        {"question": row[0], "response": format_answer_for_user(row[1], None, question=row[0])}
        for row in rows
    ]


def save_chat_message(
    conversation_id: int,
    question: str,
    response: str,
    elapsed_ms: int,
    *,
    db_conn: Callable,
) -> int:
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


def build_history_interaction_join() -> str:
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


def assert_conversation_owner(
    conversation_id: int,
    request_user_id: int,
    *,
    db_conn: Callable,
):
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
