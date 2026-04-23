import json
from typing import Dict, List, Optional

import chromadb

from config import (
    CHROMA_DB_PATH,
    MEMORY_COLLECTION_NAME,
    MEMORY_MAX_DISTANCE,
    MEMORY_MAX_RESULTS,
)
from database import get_connection


chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
memory_collection = chroma_client.get_or_create_collection(name=MEMORY_COLLECTION_NAME)


def _to_json(value) -> str:
    return json.dumps(value, ensure_ascii=True)


def _from_json(value: Optional[str], fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def record_interaction_pending(
    conversation_id: int,
    question: str,
    answer: str,
    sources: List[str],
    context: str,
    confidence: float,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    model: str = "",
    route: str = "knowledge",
    from_memory: bool = False,
    elapsed_ms: int = 0,
) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO dbo.InteraccionesRAG (
            ConversacionId, Pregunta, Respuesta, Fuentes, Contexto, Estado, Confianza,
            PromptTokens, CompletionTokens, TotalTokens, Modelo, Ruta, DesdeMemoria, TiempoRespuestaMs
        )
        OUTPUT INSERTED.Id
        VALUES (?, ?, ?, ?, ?, 'pendiente', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        conversation_id,
        question,
        answer,
        _to_json(sources),
        context,
        confidence,
        prompt_tokens,
        completion_tokens,
        total_tokens,
        model,
        route,
        1 if from_memory else 0,
        elapsed_ms,
    )
    interaction_id = cursor.fetchone()[0]
    conn.commit()
    conn.close()
    return interaction_id


def list_pending_interactions(limit: int = 50) -> List[Dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT TOP (?) Id, ConversacionId, Pregunta, Respuesta, Fuentes, FechaCreacion, Confianza, TotalTokens, Modelo
        FROM dbo.InteraccionesRAG
        WHERE Estado = 'pendiente'
        ORDER BY FechaCreacion DESC
        """,
        limit,
    )
    rows = cursor.fetchall()
    conn.close()
    items = []
    for row in rows:
        items.append(
            {
                "id": row[0],
                "conversation_id": row[1],
                "question": row[2],
                "answer": row[3],
                "sources": _from_json(row[4], []),
                "created_at": str(row[5]),
                "confidence": row[6],
                "total_tokens": row[7] or 0,
                "model": row[8] or "",
            }
        )
    return items


def get_admin_metrics(days: int = 30) -> Dict:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            COUNT(*) AS total_interactions,
            SUM(CASE WHEN Estado='validada' THEN 1 ELSE 0 END) AS total_validated,
            SUM(CASE WHEN Estado='pendiente' THEN 1 ELSE 0 END) AS total_pending,
            SUM(CASE WHEN Estado='rechazada' THEN 1 ELSE 0 END) AS total_rejected,
            ISNULL(SUM(TotalTokens), 0) AS total_tokens,
            ISNULL(AVG(CAST(TiempoRespuestaMs AS FLOAT)), 0) AS avg_latency_ms
        FROM dbo.InteraccionesRAG
        WHERE FechaCreacion >= DATEADD(day, ?, SYSUTCDATETIME())
        """,
        -abs(days),
    )
    row = cursor.fetchone()

    cursor.execute(
        """
        SELECT TOP 1 Modelo
        FROM dbo.InteraccionesRAG
        WHERE Modelo IS NOT NULL AND Modelo <> ''
        ORDER BY FechaCreacion DESC
        """
    )
    model_row = cursor.fetchone()
    conn.close()

    total_interactions = int(row[0] or 0)
    validated = int(row[1] or 0)
    pending = int(row[2] or 0)
    rejected = int(row[3] or 0)
    total_tokens = int(row[4] or 0)
    avg_latency = float(row[5] or 0.0)

    validation_rate = (validated / total_interactions) if total_interactions else 0.0
    return {
        "window_days": abs(days),
        "total_interactions": total_interactions,
        "total_validated": validated,
        "total_pending": pending,
        "total_rejected": rejected,
        "validation_rate": round(validation_rate, 4),
        "total_tokens": total_tokens,
        "avg_latency_ms": round(avg_latency, 2),
        "model": (model_row[0] if model_row else ""),
    }


def validate_interaction(interaction_id: int, reviewer: str = "system") -> Dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT Id, Pregunta, Respuesta, Fuentes, Contexto, Estado
        FROM dbo.InteraccionesRAG
        WHERE Id = ?
        """,
        interaction_id,
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise ValueError("Interaccion no encontrada")
    if row[5] == "validada":
        conn.close()
        return {"status": "already_validated", "interaction_id": interaction_id}

    sources = _from_json(row[3], [])
    sources_json = _to_json(sources)

    cursor.execute(
        """
        INSERT INTO dbo.ConocimientoValidado (InteraccionId, Pregunta, Respuesta, Fuentes, Contexto, ValidadoPor)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        row[0],
        row[1],
        row[2],
        sources_json,
        row[4],
        reviewer,
    )

    cursor.execute(
        """
        UPDATE dbo.InteraccionesRAG
        SET Estado = 'validada', FechaRevision = SYSUTCDATETIME(), RevisadoPor = ?
        WHERE Id = ?
        """,
        reviewer,
        interaction_id,
    )
    conn.commit()
    conn.close()

    memory_id = f"mem_{interaction_id}"
    memory_document = f"Pregunta: {row[1]}\nRespuesta: {row[2]}"
    memory_metadata = {
        "interaction_id": interaction_id,
        "question": row[1],
        "sources": sources_json,
        "reviewer": reviewer,
    }

    existing = memory_collection.get(ids=[memory_id])
    if existing.get("ids"):
        memory_collection.update(ids=[memory_id], documents=[memory_document], metadatas=[memory_metadata])
    else:
        memory_collection.add(ids=[memory_id], documents=[memory_document], metadatas=[memory_metadata])

    return {"status": "validated", "interaction_id": interaction_id}


def reject_interaction(interaction_id: int, reviewer: str = "system") -> Dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE dbo.InteraccionesRAG
        SET Estado = 'rechazada', FechaRevision = SYSUTCDATETIME(), RevisadoPor = ?
        WHERE Id = ?
        """,
        reviewer,
        interaction_id,
    )
    conn.commit()
    conn.close()
    return {"status": "rejected", "interaction_id": interaction_id}


def search_validated_memory(question: str) -> Optional[Dict]:
    if not question.strip():
        return None
    if memory_collection.count() == 0:
        return None

    results = memory_collection.query(
        query_texts=[question],
        n_results=MEMORY_MAX_RESULTS,
        include=["documents", "metadatas", "distances"],
    )

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    if not documents or not metadatas:
        return None

    best_doc = documents[0]
    best_meta = metadatas[0]
    best_distance = distances[0] if distances else 1.0

    if best_distance is None or best_distance > MEMORY_MAX_DISTANCE:
        return None

    answer = best_doc
    if "Respuesta:" in best_doc:
        answer = best_doc.split("Respuesta:", 1)[1].strip()

    sources = _from_json(best_meta.get("sources"), [])
    return {
        "answer": answer,
        "sources": sources,
        "distance": float(best_distance),
    }
