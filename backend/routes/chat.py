from fastapi import APIRouter, HTTPException
from models import ConversationRequest, MessageRequest
from database import get_connection

router = APIRouter()

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
    conn = get_connection()
    cursor = conn.cursor()
    
    response = "Hola, no estoy conectado todavia"
    
    cursor.execute(
        "INSERT INTO Mensajes (ConversacionId, Pregunta, Respuesta, TiempoRespuestaMs) VALUES (?, ?, ?, ?)",
        data.conversation_id, data.question, response, 0
    )
    conn.commit()
    conn.close()
    
    return {
        "question": data.question,
        "response": response
    }

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