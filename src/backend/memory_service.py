import json
import logging
import time
import re
import unicodedata
from typing import Dict, List, Optional

import chromadb

from chroma_client import get_chroma_client
from config import (
    LLM_SECONDARY_MODEL,
    OPENAI_MODEL,
    MEMORY_COLLECTION_NAME,
    MEMORY_MAX_DISTANCE,
    MEMORY_MAX_RESULTS,
    STOPWORDS,
)
from database import db_conn
from rag_service import _EF_VERSION, _embedding_fn


logger = logging.getLogger(__name__)

_MEMORY_EF_VERSION = f"memory-{_EF_VERSION}"


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


chroma_client = get_chroma_client()
memory_collection = _get_memory_collection(chroma_client, MEMORY_COLLECTION_NAME, _embedding_fn)

MODEL_PRICING_USD_PER_MILLION = {
    "gpt-4.1-mini": {
        "input": 0.40,
        "output": 1.60,
        "notes": "Tarifa estandar OpenAI gpt-4.1-mini",
    },
    "gpt-4o-mini": {
        "input": 0.15,
        "output": 0.60,
        "notes": "Tarifa estandar OpenAI gpt-4o-mini",
    },
    "gpt-4o": {
        "input": 2.50,
        "output": 10.00,
        "notes": "Tarifa estandar OpenAI gpt-4o",
    },
    "gpt-4.1": {
        "input": 2.00,
        "output": 8.00,
        "notes": "Tarifa estandar OpenAI gpt-4.1",
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


def _content_tokens(text: str) -> set:
    nfd = unicodedata.normalize("NFD", text or "")
    cleaned = "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")
    cleaned = re.sub(r"[^a-z0-9\s]", " ", cleaned.lower())
    return set(cleaned.split()) - STOPWORDS


def _has_sufficient_lexical_overlap(q1: str, q2: str, min_jaccard: float = 0.25) -> bool:
    t1 = _content_tokens(q1)
    t2 = _content_tokens(q2)
    if not t1 or not t2:
        return True
    intersection = t1 & t2
    if not intersection:
        return False
    return len(intersection) / len(t1 | t2) >= min_jaccard


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
    base_model: str = "",
    final_model: str = "",
    base_confidence: Optional[float] = None,
    final_confidence: Optional[float] = None,
    escalated: bool = False,
    escalation_reason: str = "",
    route: str = "knowledge",
    from_memory: bool = False,
    elapsed_ms: int = 0,
    router_ms: int | None = None,
    rag_ms: int | None = None,
    llm_ms: int | None = None,
    db_ms: int | None = None,
) -> int:
    final_confidence = confidence if final_confidence is None else final_confidence
    base_confidence = final_confidence if base_confidence is None else base_confidence
    final_model = final_model or model
    base_model = base_model or final_model

    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO dbo.InteraccionesRAG (
                ConversacionId, Pregunta, Respuesta, Fuentes, Contexto, Estado, Confianza,
                PromptTokens, CompletionTokens, TotalTokens, Modelo, ModeloBase, ModeloFinal,
                ConfianzaBase, ConfianzaFinal, Escalado, MotivoEscalado, Ruta, DesdeMemoria,
                TiempoRespuestaMs, RouterMs, RagMs, LlmMs, DbMs
            )
            OUTPUT INSERTED.Id
            VALUES (?, ?, ?, ?, ?, 'pendiente', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            final_model,
            base_model,
            final_model,
            base_confidence,
            final_confidence,
            1 if escalated else 0,
            (escalation_reason or "")[:80],
            route,
            1 if from_memory else 0,
            elapsed_ms,
            router_ms,
            rag_ms,
            llm_ms,
            db_ms,
        )
        interaction_id = cursor.fetchone()[0]
    return interaction_id


def update_interaction_latency(
    interaction_id: int,
    *,
    router_ms: int,
    rag_ms: int,
    llm_ms: int,
    db_ms: int,
) -> None:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE dbo.InteraccionesRAG
            SET RouterMs = ?, RagMs = ?, LlmMs = ?, DbMs = ?
            WHERE Id = ?
            """,
            router_ms,
            rag_ms,
            llm_ms,
            db_ms,
            interaction_id,
        )


def record_knowledge_interaction_and_message(**kwargs) -> tuple[int, int]:
    """Registra interacción, mensaje y desglose usando una sola conexión SQL."""
    started = time.perf_counter()
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO dbo.InteraccionesRAG (ConversacionId,Pregunta,Respuesta,Fuentes,Contexto,Estado,Confianza,PromptTokens,CompletionTokens,TotalTokens,Modelo,ModeloBase,ModeloFinal,ConfianzaBase,ConfianzaFinal,Escalado,MotivoEscalado,Ruta,DesdeMemoria,TiempoRespuestaMs,RouterMs,RagMs,LlmMs)
            OUTPUT INSERTED.Id VALUES (?,?,?,?,?,'pendiente',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            kwargs["conversation_id"], kwargs["question"], kwargs["answer"], _to_json(kwargs["sources"]), kwargs["context"], kwargs["confidence"], kwargs["prompt_tokens"], kwargs["completion_tokens"], kwargs["total_tokens"], kwargs["final_model"], kwargs["base_model"], kwargs["final_model"], kwargs["base_confidence"], kwargs["final_confidence"], 1 if kwargs["escalated"] else 0, (kwargs["escalation_reason"] or "")[:80], "knowledge", 0, kwargs["elapsed_ms"], kwargs["router_ms"], kwargs["rag_ms"], kwargs["llm_ms"],
        )
        interaction_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO dbo.Mensajes (ConversacionId,Pregunta,Respuesta,TiempoRespuestaMs) VALUES (?,?,?,?)", kwargs["conversation_id"], kwargs["question"], kwargs["answer"], kwargs["elapsed_ms"])
        db_ms = int((time.perf_counter() - started) * 1000)
        cursor.execute("UPDATE dbo.InteraccionesRAG SET DbMs=? WHERE Id=?", db_ms, interaction_id)
    return interaction_id, db_ms


def list_pending_users() -> List[Dict]:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                c.UsuarioId,
                MAX(u.Nombre) AS Nombre,
                MAX(u.Email) AS Email,
                COUNT(*) AS PendingCount,
                MAX(i.FechaCreacion) AS LastInteractionAt
            FROM dbo.InteraccionesRAG i
            LEFT JOIN dbo.Conversaciones c ON c.Id = i.ConversacionId
            LEFT JOIN dbo.Usuarios u ON u.Id = c.UsuarioId
            WHERE i.Estado = 'pendiente'
              AND c.UsuarioId IS NOT NULL
            GROUP BY c.UsuarioId
            ORDER BY PendingCount DESC, MAX(i.FechaCreacion) DESC
            """
        )
        rows = cursor.fetchall()
    return [
        {
            "user_id": int(row[0]),
            "user_name": row[1] or "",
            "user_email": row[2] or "",
            "pending_count": int(row[3] or 0),
            "last_interaction_at": str(row[4]) if row[4] else "",
        }
        for row in rows
    ]


def get_interaction_owner_user_id(interaction_id: int) -> int | None:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT c.UsuarioId
            FROM dbo.InteraccionesRAG i
            LEFT JOIN dbo.Conversaciones c ON c.Id = i.ConversacionId
            WHERE i.Id = ?
            """,
            interaction_id,
        )
        row = cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else None


def _normalize_pending_chat_mode(chat_mode: str | None) -> str | None:
    normalized = (chat_mode or "").strip().lower()
    if normalized == "business":
        return "business"
    if normalized == "technical":
        return "technical"
    return None


def list_pending_interactions(
    limit: int = 50,
    user_id: int | None = None,
    chat_mode: str | None = None,
) -> List[Dict]:
    limit = max(1, min(int(limit or 50), 200))
    normalized_chat_mode = _normalize_pending_chat_mode(chat_mode)
    with db_conn() as conn:
        cursor = conn.cursor()
        params = [limit]
        user_filter = ""
        chat_mode_filter = ""
        if user_id is not None:
            user_filter = " AND c.UsuarioId = ?"
            params.append(int(user_id))
        if normalized_chat_mode == "business":
            chat_mode_filter = (
                " AND ("
                "LOWER(LTRIM(RTRIM(ISNULL(c.ChatMode, '')))) = 'business' "
                "OR LOWER(LTRIM(RTRIM(ISNULL(i.Ruta, '')))) IN ('business_licitaciones', 'business_produccion')"
                ")"
            )
        elif normalized_chat_mode == "technical":
            chat_mode_filter = (
                " AND ("
                "("
                "LOWER(LTRIM(RTRIM(ISNULL(c.ChatMode, '')))) = 'technical' "
                "OR (ISNULL(c.ChatMode, '') = '' AND LOWER(LTRIM(RTRIM(ISNULL(i.Ruta, '')))) NOT IN ('business_licitaciones', 'business_produccion'))"
                ")"
                " OR (ISNULL(c.ChatMode, '') = '' AND LOWER(ISNULL(c.Titulo, '')) NOT LIKE '%negocio%')"
                ")"
            )
        cursor.execute(
            f"""
            SELECT TOP (?)
                i.Id,
                i.ConversacionId,
                c.UsuarioId,
                i.Pregunta,
                i.Respuesta,
                i.Fuentes,
                i.FechaCreacion,
                i.Confianza,
                i.TotalTokens,
                i.Modelo,
                u.Nombre,
                u.Email
            FROM dbo.InteraccionesRAG i
            LEFT JOIN dbo.Conversaciones c ON c.Id = i.ConversacionId
            LEFT JOIN dbo.Usuarios u ON u.Id = c.UsuarioId
            WHERE i.Estado = 'pendiente'
              {user_filter}
              {chat_mode_filter}
            ORDER BY i.FechaCreacion DESC
            """,
            *params,
        )
        rows = cursor.fetchall()
    items = []
    for row in rows:
        items.append(
            {
                "id": row[0],
                "conversation_id": row[1],
                "user_id": row[2],
                "question": row[3],
                "answer": row[4],
                "sources": _from_json(row[5], []),
                "created_at": str(row[6]),
                "confidence": row[7],
                "total_tokens": row[8] or 0,
                "model": row[9] or "",
                "user_name": row[10] or "",
                "user_email": row[11] or "",
            }
        )
    return items


def get_interaction_detail(interaction_id: int) -> Dict:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                i.Id,
                i.ConversacionId,
                c.UsuarioId,
                i.Pregunta,
                i.Respuesta,
                i.Fuentes,
                i.Contexto,
                i.Estado,
                i.FechaCreacion,
                i.Confianza,
                i.TotalTokens,
                i.Modelo,
                i.ModeloBase,
                i.ModeloFinal,
                i.ConfianzaBase,
                i.ConfianzaFinal,
                i.Escalado,
                i.MotivoEscalado,
                i.Ruta,
                i.DesdeMemoria,
                i.TiempoRespuestaMs,
                u.Nombre,
                u.Email
            FROM dbo.InteraccionesRAG i
            LEFT JOIN dbo.Conversaciones c ON c.Id = i.ConversacionId
            LEFT JOIN dbo.Usuarios u ON u.Id = c.UsuarioId
            WHERE i.Id = ?
            """,
            interaction_id,
        )
        row = cursor.fetchone()
    if not row:
        raise ValueError("Interaccion no encontrada")
    return {
        "id": row[0],
        "conversation_id": row[1],
        "user_id": row[2],
        "question": row[3],
        "answer": row[4],
        "sources": _from_json(row[5], []),
        "context": row[6] or "",
        "status": row[7],
        "created_at": str(row[8]),
        "confidence": row[9],
        "total_tokens": row[10] or 0,
        "model": row[11] or "",
        "base_model": row[12] or "",
        "final_model": row[13] or "",
        "base_confidence": row[14],
        "final_confidence": row[15],
        "escalated": bool(row[16]) if row[16] is not None else False,
        "escalation_reason": row[17] or "",
        "route": row[18] or "",
        "from_memory": bool(row[19]) if row[19] is not None else False,
        "elapsed_ms": row[20] or 0,
        "user_name": row[21] or "",
        "user_email": row[22] or "",
    }


def get_admin_metrics(days: int = 30) -> Dict:
    with db_conn() as conn:
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

        cursor.execute(
            """
            WITH samples AS (
                SELECT RouterMs, RagMs, LlmMs, DbMs, TiempoRespuestaMs
                FROM dbo.InteraccionesRAG
                WHERE FechaCreacion >= DATEADD(day, ?, SYSUTCDATETIME())
                  AND RouterMs IS NOT NULL
            ), percentiles AS (
                SELECT
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY TiempoRespuestaMs) OVER () AS p50_ms,
                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY TiempoRespuestaMs) OVER () AS p95_ms
                FROM samples
            )
            SELECT
                COUNT(*) AS samples,
                ISNULL(AVG(CAST(RouterMs AS FLOAT)), 0) AS avg_router_ms,
                ISNULL(AVG(CAST(RagMs AS FLOAT)), 0) AS avg_rag_ms,
                ISNULL(AVG(CAST(LlmMs AS FLOAT)), 0) AS avg_llm_ms,
                ISNULL(AVG(CAST(DbMs AS FLOAT)), 0) AS avg_db_ms,
                ISNULL(MAX(p50_ms), 0) AS p50_ms,
                ISNULL(MAX(p95_ms), 0) AS p95_ms
            FROM samples
            CROSS JOIN percentiles
            """,
            -abs(days),
        )
        latency_row = cursor.fetchone()

    total_interactions = int(row[0] or 0)
    validated = int(row[1] or 0)
    pending = int(row[2] or 0)
    rejected = int(row[3] or 0)
    total_errors = int(row[4] or 0)
    total_prompt_tokens = int(row[5] or 0)
    total_completion_tokens = int(row[6] or 0)
    total_tokens = int(row[7] or 0)
    avg_latency = float(row[8] or 0.0)
    latency_breakdown = {
        "samples": int(latency_row[0] or 0),
        "avg_router_ms": round(float(latency_row[1] or 0.0), 2),
        "avg_rag_ms": round(float(latency_row[2] or 0.0), 2),
        "avg_llm_ms": round(float(latency_row[3] or 0.0), 2),
        "avg_db_ms": round(float(latency_row[4] or 0.0), 2),
        "p50_ms": round(float(latency_row[5] or 0.0), 2),
        "p95_ms": round(float(latency_row[6] or 0.0), 2),
    }

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
    secondary_model_key = (LLM_SECONDARY_MODEL or "").strip().lower()
    current_model_stats = model_index.get(primary_model_key, _empty_model_stats(OPENAI_MODEL, "base"))
    baseline_model_stats = model_index.get(secondary_model_key, _empty_model_stats(LLM_SECONDARY_MODEL, "escalado"))
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
        "latency_breakdown": latency_breakdown,
        "estimated_cost_usd": round(cost_estimated_usd, 6),
        "model": OPENAI_MODEL,
        "baseline_model": LLM_SECONDARY_MODEL,
        "model_breakdown": model_breakdown,
        "model_comparison": comparison_summary,
    }


def record_model_error_event(model: str, status_code: int, *, error_kind: str = "", origin: str = "") -> None:
    normalized_model = (model or "").strip()
    if not normalized_model or not status_code:
        return

    with db_conn() as conn:
        cursor = conn.cursor()
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


def get_admin_503_metrics(hours: int = 24) -> Dict:
    window_hours = max(1, min(abs(int(hours or 24)), 24 * 30))
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                Modelo,
                COUNT(*) AS total_avail_errors
            FROM dbo.ModelErrorEvents
            WHERE StatusCode IN (429, 503)
              AND FechaCreacion >= DATEADD(hour, ?, SYSUTCDATETIME())
            GROUP BY Modelo
            """,
            -window_hours,
        )
        rows = cursor.fetchall()

    counts_by_model = {(row[0] or "").strip().lower(): int(row[1] or 0) for row in rows}
    primary_model = OPENAI_MODEL
    secondary_model = LLM_SECONDARY_MODEL
    primary_count = counts_by_model.get(primary_model.strip().lower(), 0)
    secondary_count = counts_by_model.get(secondary_model.strip().lower(), 0)

    return {
        "window_hours": window_hours,
        "models": [
            {"model": primary_model, "role": "base", "count_avail_errors": primary_count},
            {"model": secondary_model, "role": "secundario", "count_avail_errors": secondary_count},
        ],
        "comparison": {
            "base_model": primary_model,
            "secondary_model": secondary_model,
            "base_count_avail_errors": primary_count,
            "secondary_count_avail_errors": secondary_count,
            "delta_avail_errors": primary_count - secondary_count,
        },
    }


def validate_interaction(interaction_id: int, reviewer: str = "system") -> Dict:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT Id, Pregunta, Respuesta, Fuentes, Contexto, Estado, Ruta
            FROM dbo.InteraccionesRAG
            WHERE Id = ?
            """,
            interaction_id,
        )
        row = cursor.fetchone()
        if not row:
            raise ValueError("Interaccion no encontrada")
        if row[5] == "validada":
            return {"status": "already_validated", "interaction_id": interaction_id}

        sources = _from_json(row[3], [])
        sources_json = _to_json(sources)
        route = row[6] or ""

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

    if route == "document_inventory":
        return {"status": "validated", "interaction_id": interaction_id}

    memory_id = f"mem_{interaction_id}"
    memory_document = f"Pregunta: {row[1]}\nRespuesta: {row[2]}"
    memory_metadata = {
        "interaction_id": interaction_id,
        "question": row[1],
        "sources": sources_json,
        "reviewer": reviewer,
        "route": route,
    }

    existing = memory_collection.get(ids=[memory_id])
    if existing.get("ids"):
        memory_collection.update(ids=[memory_id], documents=[memory_document], metadatas=[memory_metadata])
    else:
        memory_collection.add(ids=[memory_id], documents=[memory_document], metadatas=[memory_metadata])

    return {"status": "validated", "interaction_id": interaction_id}


def reject_interaction(interaction_id: int, reviewer: str = "system") -> Dict:
    with db_conn() as conn:
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
    memory_id = f"mem_{interaction_id}"
    try:
        existing = memory_collection.get(ids=[memory_id])
        if existing.get("ids"):
            memory_collection.delete(ids=[memory_id])
    except Exception:
        logger.warning("reject_interaction: no se pudo eliminar %s de Chroma", memory_id)
    return {"status": "rejected", "interaction_id": interaction_id}


def search_validated_memory(question: str) -> Optional[Dict]:
    if not question.strip():
        return None
    try:
        collection_size = memory_collection.count()
    except Exception:
        logger.exception("search_validated_memory: no se pudo consultar el tamano de la coleccion")
        return None

    if collection_size == 0:
        return None

    try:
        results = memory_collection.query(
            query_texts=[question],
            n_results=MEMORY_MAX_RESULTS,
            include=["documents", "metadatas", "distances"],
        )
    except Exception:
        logger.exception("search_validated_memory: fallo consultando memoria validada")
        return None

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

    if (best_meta.get("route") or "") == "document_inventory":
        logger.info("search_validated_memory: descartado hit de ruta document_inventory (dist=%.4f)", best_distance)
        return None

    stored_question = best_meta.get("question") or ""
    if stored_question and not _has_sufficient_lexical_overlap(question, stored_question):
        logger.info("search_validated_memory: descartado por solapamiento lexico insuficiente (dist=%.4f)", best_distance)
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


def list_validated_interactions(
    limit: int = 50,
    user_id: int | None = None,
) -> List[Dict]:
    limit = max(1, min(int(limit or 50), 200))
    with db_conn() as conn:
        cursor = conn.cursor()
        params = [limit]
        user_filter = ""
        if user_id is not None:
            user_filter = " AND c.UsuarioId = ?"
            params.append(int(user_id))
        cursor.execute(
            f"""
            SELECT TOP (?)
                i.Id,
                i.ConversacionId,
                c.UsuarioId,
                i.Pregunta,
                i.Respuesta,
                i.Fuentes,
                i.FechaCreacion,
                i.FechaRevision,
                i.Confianza,
                i.TotalTokens,
                i.Modelo,
                i.Ruta,
                i.Estado,
                i.RevisadoPor,
                u.Nombre,
                u.Email
            FROM dbo.InteraccionesRAG i
            LEFT JOIN dbo.Conversaciones c ON c.Id = i.ConversacionId
            LEFT JOIN dbo.Usuarios u ON u.Id = c.UsuarioId
            WHERE i.Estado IN ('validada', 'rechazada')
              {user_filter}
            ORDER BY COALESCE(i.FechaRevision, i.FechaCreacion) DESC
            """,
            *params,
        )
        rows = cursor.fetchall()
    return [
        {
            "id": row[0],
            "conversation_id": row[1],
            "user_id": row[2],
            "question": row[3],
            "answer": row[4],
            "sources": _from_json(row[5], []),
            "created_at": str(row[6]),
            "reviewed_at": str(row[7]) if row[7] else "",
            "confidence": row[8],
            "total_tokens": row[9] or 0,
            "model": row[10] or "",
            "route": row[11] or "",
            "status": row[12] or "",
            "reviewed_by": row[13] or "",
            "user_name": row[14] or "",
            "user_email": row[15] or "",
        }
        for row in rows
    ]


def list_validated_users() -> List[Dict]:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                c.UsuarioId,
                MAX(u.Nombre) AS Nombre,
                MAX(u.Email) AS Email,
                COUNT(*) AS TotalCount,
                SUM(CASE WHEN i.Estado = 'validada' THEN 1 ELSE 0 END) AS ValidatedCount,
                SUM(CASE WHEN i.Estado = 'rechazada' THEN 1 ELSE 0 END) AS RejectedCount
            FROM dbo.InteraccionesRAG i
            LEFT JOIN dbo.Conversaciones c ON c.Id = i.ConversacionId
            LEFT JOIN dbo.Usuarios u ON u.Id = c.UsuarioId
            WHERE i.Estado IN ('validada', 'rechazada')
              AND c.UsuarioId IS NOT NULL
            GROUP BY c.UsuarioId
            ORDER BY COUNT(*) DESC
            """
        )
        rows = cursor.fetchall()
    return [
        {
            "user_id": int(row[0]),
            "user_name": row[1] or "",
            "user_email": row[2] or "",
            "total_count": int(row[3] or 0),
            "validated_count": int(row[4] or 0),
            "rejected_count": int(row[5] or 0),
        }
        for row in rows
    ]


def purge_document_inventory_memories(dry_run: bool = True) -> Dict:
    """Identifica y opcionalmente elimina de Chroma las memorias contaminadas
    (interacciones validadas con ruta document_inventory). Siempre idempotente."""
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT Id FROM dbo.InteraccionesRAG WHERE Estado = 'validada' AND Ruta = 'document_inventory'"
        )
        rows = cursor.fetchall()
    candidates = [f"mem_{row[0]}" for row in rows]
    found = []
    for mem_id in candidates:
        try:
            existing = memory_collection.get(ids=[mem_id])
            if existing.get("ids"):
                found.append(mem_id)
        except Exception:
            pass
    if not dry_run:
        for mem_id in found:
            try:
                memory_collection.delete(ids=[mem_id])
            except Exception:
                logger.warning("purge_document_inventory_memories: no se pudo eliminar %s", mem_id)
    return {
        "dry_run": dry_run,
        "candidates": len(candidates),
        "found_in_chroma": len(found),
        "purged": 0 if dry_run else len(found),
        "ids": found,
    }
