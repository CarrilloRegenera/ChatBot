import json
import logging
from typing import Dict, List, Optional

import chromadb

from config import (
    CHROMA_DB_PATH,
    GEMINI_SECONDARY_MODEL,
    OPENAI_MODEL,
    MEMORY_COLLECTION_NAME,
    MEMORY_MAX_DISTANCE,
    MEMORY_MAX_RESULTS,
)
from database import get_connection
from rag_service import _embedding_fn


logger = logging.getLogger(__name__)

_MEMORY_EF_VERSION = "multilingual-minilm-v1"


def _get_memory_collection(client: chromadb.PersistentClient, name: str, ef):
    try:
        col = client.get_or_create_collection(name=name, embedding_function=ef, metadata={"ef_version": _MEMORY_EF_VERSION})
        if (col.metadata or {}).get("ef_version") != _MEMORY_EF_VERSION:
            raise ValueError("ef_version mismatch")
        return col
    except Exception:
        logger.warning("Memory collection embedding mismatch — recreando coleccion de memoria")
        try:
            client.delete_collection(name)
        except Exception:
            pass
        return client.create_collection(name=name, embedding_function=ef, metadata={"ef_version": _MEMORY_EF_VERSION})


chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
memory_collection = _get_memory_collection(chroma_client, MEMORY_COLLECTION_NAME, _embedding_fn)

MODEL_PRICING_USD_PER_MILLION = {
    "gpt-4o-mini": {
        "input": 0.15,
        "output": 0.60,
        "notes": "Tarifa estandar OpenAI",
    },
    "gpt-5.4-mini": {
        "input": 0.15,
        "output": 0.60,
        "notes": "Tarifa pendiente confirmar OpenAI",
    },
    "gpt-4o": {
        "input": 2.50,
        "output": 10.00,
        "notes": "Tarifa estandar OpenAI",
    },
    "gemini-2.5-flash": {
        "input": 0.30,
        "output": 2.50,
        "notes": "Tarifa estandar Google Gemini 2.5 Flash",
    },
   
    "gemini-3-flash-preview": {
        "input": 0.50,
        "output": 3.00,
        "notes": "Tarifa estandar Google Gemini 3 Flash Preview",
    },
}


def _to_json(value) -> str:
    return json.dumps(value, ensure_ascii=True)


def _from_json(value: Optional[str], fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _estimate_model_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pricing = MODEL_PRICING_USD_PER_MILLION.get((model or "").strip().lower())
    if not pricing:
        return 0.0
    prompt_cost = (max(prompt_tokens, 0) / 1_000_000) * pricing["input"]
    completion_cost = (max(completion_tokens, 0) / 1_000_000) * pricing["output"]
    return prompt_cost + completion_cost


def _empty_model_stats(model_name: str, role: str) -> Dict:
    pricing = MODEL_PRICING_USD_PER_MILLION.get((model_name or "").strip().lower(), {})
    return {
        "model": model_name,
        "role": role,
        "interactions": 0,
        "validated": 0,
        "pending": 0,
        "rejected": 0,
        "errors": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "avg_latency_ms": 0.0,
        "estimated_cost_usd": 0.0,
        "cost_per_1k_tokens_usd": 0.0,
        "validation_rate": 0.0,
        "pricing_notes": pricing.get("notes", ""),
    }


def _normalize_model_stats(row, role: str) -> Dict:
    model_name = row[0] or ""
    interactions = int(row[1] or 0)
    validated = int(row[2] or 0)
    pending = int(row[3] or 0)
    rejected = int(row[4] or 0)
    errors = int(row[5] or 0)
    prompt_tokens = int(row[6] or 0)
    completion_tokens = int(row[7] or 0)
    total_tokens = int(row[8] or 0)
    avg_latency = float(row[9] or 0.0)
    estimated_cost = _estimate_model_cost_usd(model_name, prompt_tokens, completion_tokens)
    cost_per_1k_tokens = (estimated_cost / total_tokens * 1000) if total_tokens > 0 else 0.0
    validation_rate = (validated / interactions) if interactions else 0.0
    pricing = MODEL_PRICING_USD_PER_MILLION.get((model_name or "").strip().lower(), {})

    return {
        "model": model_name,
        "role": role,
        "interactions": interactions,
        "validated": validated,
        "pending": pending,
        "rejected": rejected,
        "errors": errors,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "avg_latency_ms": round(avg_latency, 2),
        "estimated_cost_usd": round(estimated_cost, 6),
        "cost_per_1k_tokens_usd": round(cost_per_1k_tokens, 6),
        "validation_rate": round(validation_rate, 4),
        "pricing_notes": pricing.get("notes", ""),
    }


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
            SUM(CASE WHEN Respuesta LIKE 'No he podido generar respuesta en este momento por un error temporal%' THEN 1 ELSE 0 END) AS total_errors,
            ISNULL(SUM(PromptTokens), 0) AS total_prompt_tokens,
            ISNULL(SUM(CompletionTokens), 0) AS total_completion_tokens,
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
        SELECT
            Modelo,
            COUNT(*) AS interactions,
            SUM(CASE WHEN Estado='validada' THEN 1 ELSE 0 END) AS validated,
            SUM(CASE WHEN Estado='pendiente' THEN 1 ELSE 0 END) AS pending,
            SUM(CASE WHEN Estado='rechazada' THEN 1 ELSE 0 END) AS rejected,
            SUM(CASE WHEN Respuesta LIKE 'No he podido generar respuesta en este momento por un error temporal%'
                  OR Respuesta LIKE 'El modelo no ha podido responder por saturacion temporal del servicio%'
                THEN 1 ELSE 0 END) AS total_errors,
            ISNULL(SUM(PromptTokens), 0) AS prompt_tokens,
            ISNULL(SUM(CompletionTokens), 0) AS completion_tokens,
            ISNULL(SUM(TotalTokens), 0) AS total_tokens,
            ISNULL(AVG(CAST(TiempoRespuestaMs AS FLOAT)), 0) AS avg_latency_ms
        FROM dbo.InteraccionesRAG
        WHERE FechaCreacion >= DATEADD(day, ?, SYSUTCDATETIME())
          AND Modelo IS NOT NULL AND Modelo <> ''
        GROUP BY Modelo
        """,
        -abs(days),
    )
    model_rows = cursor.fetchall()
    conn.close()

    total_interactions = int(row[0] or 0)
    validated = int(row[1] or 0)
    pending = int(row[2] or 0)
    rejected = int(row[3] or 0)
    total_errors = int(row[4] or 0)
    total_prompt_tokens = int(row[5] or 0)
    total_completion_tokens = int(row[6] or 0)
    total_tokens = int(row[7] or 0)
    avg_latency = float(row[8] or 0.0)

    validation_rate = (validated / total_interactions) if total_interactions else 0.0
    cost_estimated_usd = 0.0
    model_breakdown = []
    model_index = {}
    for model_row in model_rows:
        stats = _normalize_model_stats(model_row, role="observed")
        cost_estimated_usd += stats["estimated_cost_usd"]
        model_breakdown.append(stats)
        model_index[(stats["model"] or "").strip().lower()] = stats

    primary_model_key = (OPENAI_MODEL or "").strip().lower()
    secondary_model_key = (GEMINI_SECONDARY_MODEL or "").strip().lower()
    current_model_stats = model_index.get(primary_model_key, _empty_model_stats(OPENAI_MODEL, "base"))
    baseline_model_stats = model_index.get(secondary_model_key, _empty_model_stats(GEMINI_SECONDARY_MODEL, "escalado"))
    current_model_stats["role"] = "base"
    baseline_model_stats["role"] = "escalado"

    comparison_summary = {
        "primary_model": current_model_stats,
        "secondary_model": baseline_model_stats,
        "current_vs_baseline": {
            "cost_delta_usd": round(current_model_stats["estimated_cost_usd"] - baseline_model_stats["estimated_cost_usd"], 6),
            "latency_delta_ms": round(current_model_stats["avg_latency_ms"] - baseline_model_stats["avg_latency_ms"], 2),
            "error_delta": int(current_model_stats["errors"] - baseline_model_stats["errors"]),
            "validation_rate_delta": round(current_model_stats["validation_rate"] - baseline_model_stats["validation_rate"], 4),
        },
    }

    return {
        "window_days": abs(days),
        "total_interactions": total_interactions,
        "total_validated": validated,
        "total_pending": pending,
        "total_rejected": rejected,
        "total_errors": total_errors,
        "validation_rate": round(validation_rate, 4),
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "total_tokens": total_tokens,
        "avg_latency_ms": round(avg_latency, 2),
        "estimated_cost_usd": round(cost_estimated_usd, 6),
        "model": OPENAI_MODEL,
        "baseline_model": GEMINI_SECONDARY_MODEL,
        "model_breakdown": model_breakdown,
        "model_comparison": comparison_summary,
    }


def record_model_error_event(model: str, status_code: int, *, error_kind: str = "", origin: str = "") -> None:
    normalized_model = (model or "").strip()
    if not normalized_model or not status_code:
        return

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO dbo.ModelErrorEvents (Modelo, StatusCode, ErrorKind, Origen)
            VALUES (?, ?, ?, ?)
            """,
            normalized_model,
            int(status_code),
            (error_kind or "").strip()[:40],
            (origin or "").strip()[:40],
        )
        conn.commit()
    finally:
        conn.close()


def get_admin_503_metrics(hours: int = 24) -> Dict:
    window_hours = max(1, min(abs(int(hours or 24)), 24 * 30))
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT
                Modelo,
                COUNT(*) AS total_503
            FROM dbo.ModelErrorEvents
            WHERE StatusCode = 503
              AND FechaCreacion >= DATEADD(hour, ?, SYSUTCDATETIME())
            GROUP BY Modelo
            """,
            -window_hours,
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    counts_by_model = {(row[0] or "").strip().lower(): int(row[1] or 0) for row in rows}
    primary_model = OPENAI_MODEL
    secondary_model = GEMINI_SECONDARY_MODEL
    primary_count = counts_by_model.get(primary_model.strip().lower(), 0)
    secondary_count = counts_by_model.get(secondary_model.strip().lower(), 0)

    return {
        "window_hours": window_hours,
        "models": [
            {"model": primary_model, "role": "base", "count_503": primary_count},
            {"model": secondary_model, "role": "secundario", "count_503": secondary_count},
        ],
        "comparison": {
            "base_model": primary_model,
            "secondary_model": secondary_model,
            "base_count_503": primary_count,
            "secondary_count_503": secondary_count,
            "delta_503": primary_count - secondary_count,
        },
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
