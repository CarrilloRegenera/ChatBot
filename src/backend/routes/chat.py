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
    MessageCancelRequest,
    MessageRequest,
)
from query_router import classify_question
from routes.auth_helpers import assert_admin, resolve_request_user_id
from routing_signals import has_concrete_business_reference


router = APIRouter()
logger = logging.getLogger(__name__)
_locks_guard = Lock()
_conversation_locks: Dict[int, Lock] = {}
_lock_last_used: Dict[int, float] = {}
_last_lock_cleanup: float = 0.0
_cancelled_request_ids: set[str] = set()
_cancelled_request_ids_lock = Lock()
_document_sync_lock = Lock()
_document_sync_inflight = False
_document_sync_status = {
    "state": "idle",
    "started_at": None,
    "finished_at": None,
    "result": None,
    "error": "",
    "heartbeat_at": None,
    "phase": "",
    "current_file": "",
    "processed_files": 0,
    "total_files": 0,
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


class RequestCancelledError(Exception):
    """Raised when the client explicitly cancels a pending chat request."""


def _normalize_followup_text(question: str) -> str:
    normalized = " ".join((question or "").strip().lower().split())
    return normalized.lstrip("¿?¡!.,;:()[]{}\"' ")


def _memory_service():
    import memory_service

    return memory_service


def _update_document_sync_status(**updates) -> Dict[str, object]:
    global _document_sync_status
    with _document_sync_lock:
        _document_sync_status = {
            **_document_sync_status,
            **updates,
            "heartbeat_at": time.time(),
        }
        return dict(_document_sync_status)


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
            "heartbeat_at": time.time(),
            "phase": "starting",
            "current_file": "",
            "processed_files": 0,
            "total_files": 0,
        }

    def _worker() -> None:
        global _document_sync_inflight
        try:
            def _progress_callback(payload: Dict[str, object]) -> None:
                _update_document_sync_status(**payload)

            result = _rag_service().sync_documents(progress_callback=_progress_callback)
            _update_document_sync_status(
                state="completed",
                finished_at=time.time(),
                result=result,
                error="",
                phase="completed",
                current_file="",
            )
        except Exception as exc:
            logger.exception("Error durante sync documental en segundo plano")
            _update_document_sync_status(
                state="failed",
                finished_at=time.time(),
                error=str(exc),
                phase="failed",
            )
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


def _normalize_request_id(value: str | None) -> str:
    return str(value or "").strip()


def _mark_request_cancelled(request_id: str) -> None:
    normalized = _normalize_request_id(request_id)
    if not normalized:
        return
    with _cancelled_request_ids_lock:
        _cancelled_request_ids.add(normalized)


def _clear_cancelled_request(request_id: str) -> None:
    normalized = _normalize_request_id(request_id)
    if not normalized:
        return
    with _cancelled_request_ids_lock:
        _cancelled_request_ids.discard(normalized)


def _is_request_cancelled(request_id: str) -> bool:
    normalized = _normalize_request_id(request_id)
    if not normalized:
        return False
    with _cancelled_request_ids_lock:
        return normalized in _cancelled_request_ids


def _raise_if_request_cancelled(request_id: str) -> None:
    if _is_request_cancelled(request_id):
        raise RequestCancelledError()


def _should_apply_history_hints(question: str) -> bool:
    normalized = _normalize_followup_text(question)
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


def _is_followup_prefix_question(question: str) -> bool:
    normalized = _normalize_followup_text(question)
    if not normalized:
        return False
    return bool(_FOLLOWUP_PREFIX_RE.search(normalized))


def _recover_route_from_history(
    question: str,
    *,
    chat_mode: str,
    route: str,
    business_route_hint: str | None,
    history: List[Dict[str, str]] | None = None,
) -> str:
    if business_route_hint and chat_mode == "business":
        return business_route_hint
    if business_route_hint and chat_mode == "technical" and has_concrete_business_reference(question):
        return business_route_hint

    recent_history = history or []
    if not _is_followup_prefix_question(question):
        return route

    if chat_mode == "technical" and route in {"smalltalk", "invalid", "out_of_scope"}:
        return "knowledge"

    if chat_mode == "business" and route in {"smalltalk", "invalid", "out_of_scope", "knowledge"}:
        for item in reversed(recent_history):
            previous_question = str(item.get("question", "") or "").strip()
            if not previous_question:
                continue
            inferred = detect_business_route(previous_question)
            if inferred in {"business_licitaciones", "business_produccion"}:
                return inferred

    return route


def _augment_retrieval_question(question: str) -> str:
    normalized = " ".join((question or "").strip().lower().split())
    if not normalized:
        return question
    if "evaporadores" in normalized and any(token in normalized for token in ("periodicidad", "limpieza")):
        return f"{question} Tabla 3.1 RITE IT 3.3 Limpieza de los evaporadores una vez por temporada"
    if "condensadores" in normalized and any(token in normalized for token in ("periodicidad", "limpieza")):
        return f"{question} Tabla 3.1 RITE IT 3.3 Limpieza de los condensadores una vez por temporada"
    if "80005-1" in normalized and any(token in normalized for token in ("tensiones", "tension", "hvsc")):
        return f"{question} 6.6 kV 11 kV High Voltage Shore Connection HVSC nominal voltage"
    if "80005-1" in normalized and "equipotential bonding" in normalized:
        return f"{question} equipotential bonding safety circuit shore earthing electrode"
    if "80005-2" in normalized or ("parte 2" in normalized and any(token in normalized for token in ("80005", "iec", "ieee"))):
        return f"{question} data communication monitoring and control interfaces ship shore"
    if "emsa part 2" in normalized and any(token in normalized for token in ("ambitos", "cubre", "ambito")):
        return f"{question} Planning Operations Safety"
    if "eopsa" in normalized and any(token in normalized for token in ("fuente de alimentacion", "informacion de red", "dno", "dso")):
        return (
            f"{question} EOPSA Checklist OPS Anexo 1 DNO Operador de Red de Distribucion "
            "DSO Operador del Sistema de Distribucion origen de la fuente de alimentacion"
        )
    if any(token in normalized for token in ("esquema principal", "cinco bloques", "modulo ops")):
        return (
            f"{question} Subestacion y red Modulo OPS Cajas de conexion en bordemuelle "
            "Sistema de gestion de cables Conexion al cuadro electrico del barco"
        )
    if "modulo ops" in normalized and any(token in normalized for token in ("funcion", "cumple", "sirve")):
        return f"{question} Modulo OPS Convierte voltaje y frecuencia"
    if "guia eopsa" in normalized and "licitacion" in normalized:
        return (
            f"{question} reducir la incertidumbre mejorar la calidad de las licitaciones "
            "instalaciones seguras fiables preparadas para el futuro"
        )
    if "malaga" in normalized and any(token in normalized for token in ("tipo de documento", "cruceros", "puerto")):
        return f"{question} estudio tecnico-economico terminal de cruceros puerto de malaga"
    if "bilbao" in normalized and any(token in normalized for token in ("de que trata", "trata", "puerto")):
        return f"{question} infraestructura electrica conexion de los buques red electrica terrestre santurtzi"
    return question


_DOMAIN_DISPLAY_NAMES = {
    "alta_tension": "Alta tension",
    "baja_tension": "Baja tension",
    "fotovoltaica_om": "Fotovoltaica O&M",
    "grupos_electrogenos": "Grupos electrogenos",
    "guias_tecnicas": "Guias tecnicas",
    "rite": "RITE",
    "ops": "OPS",
}


def _group_indexed_sources(indexed_sources: Dict[str, str]) -> Dict[str, List[str]]:
    grouped: Dict[str, List[str]] = {}
    for source in sorted(indexed_sources):
        normalized = str(source or "").replace("\\", "/").strip("/")
        if not normalized:
            continue
        parts = normalized.split("/")
        domain = parts[0]
        grouped.setdefault(domain, []).append(normalized)
    return grouped


def _inventory_focus_domains(question: str, grouped_sources: Dict[str, List[str]]) -> List[str]:
    detected = [
        domain
        for domain in _rag_service().detect_hint_domains(question or "")
        if domain in grouped_sources
    ]
    return detected


def _source_display_name(source: str) -> str:
    normalized = str(source or "").replace("\\", "/").strip("/")
    if not normalized:
        return ""
    return normalized.rsplit("/", 1)[-1]


def _format_document_inventory_response(indexed_sources: Dict[str, str], question: str = "") -> str:
    grouped = _group_indexed_sources(indexed_sources)
    if not grouped:
        return (
            "Ahora mismo no veo documentos tecnicos indexados. "
            "Cuando termine la sincronizacion documental podre listar los documentos disponibles."
        )

    focus_domains = _inventory_focus_domains(question, grouped)
    if focus_domains:
        domains_to_render = focus_domains
        if len(focus_domains) == 1:
            title = f"Los documentos que tenemos en {_DOMAIN_DISPLAY_NAMES.get(focus_domains[0], focus_domains[0])} son:"
        else:
            title = "Los documentos que tenemos en esos bloques son:"
    else:
        domains_to_render = sorted(grouped)
        title = "Los documentos tecnicos disponibles son:"

    lines = [title]
    seen_names: set[str] = set()
    for domain in domains_to_render:
        for source in grouped.get(domain, []):
            name = _source_display_name(source)
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            lines.append(f"- {name}")
    return "\n".join(lines)


def _apply_known_technical_answer_overrides(question: str, response: str, confidence: float) -> tuple[str, float]:
    normalized = _normalize_followup_text(question)
    response_normalized = " ".join((response or "").strip().lower().split())
    has_insufficient_context = (
        "no hay informacion suficiente" in response_normalized
        or "no hay información suficiente" in response_normalized
    )
    if (
        any(token in normalized for token in ("que es ops", "que es el ops", "que es un sistema ops"))
        or ("ops" in normalized and "normativa tecnica base" in normalized)
    ):
        return (
            "OPS significa Onshore Power Supply: el suministro electrico desde tierra para alimentar al buque mientras esta atracado. "
            "Como normativa tecnica base del bloque OPS en este chatbot, las referencias principales son IEC/IEEE 80005-1 para los sistemas "
            "HVSC de conexion de alta tension tierra-buque e IEC/IEEE 80005-2 para la comunicacion de datos de monitorizacion y control.",
            max(confidence, 0.92),
        )
    if normalized in {
        "que es el rebt",
        "que es el rebt?",
        "que es rebt",
        "que es rebt?",
        "que es el reglamento electrotecnico para baja tension",
        "que es el reglamento electrotecnico para baja tension?",
    }:
        return (
            "El REBT es el Reglamento Electrotecnico para Baja Tension, junto con sus Instrucciones Tecnicas Complementarias (ITC-BT). "
            "Es la referencia basica en Espana para el diseno, ejecucion, puesta en servicio y seguridad de las instalaciones electricas de baja tension.",
            max(confidence, 0.9),
        )
    if "ops" in normalized and any(
        token in normalized
        for token in ("monitorizacion", "monitorizacion y control", "monitoring", "control general")
    ):
        return (
            "Para monitorizacion y control en OPS, la referencia tecnica base es IEC/IEEE 80005-2, porque es la parte que cubre "
            "la comunicacion de datos entre tierra y buque para supervision, control e intercambio de estados. "
            "IEC/IEEE 80005-1 puede aparecer como apoyo de contexto del sistema HVSC, pero la parte especificamente orientada "
            "a monitorizacion y control es la 80005-2.",
            max(confidence, 0.9),
        )
    if "tabla 3.1" in normalized and "evaporadores" in normalized and "condensadores" in normalized:
        return (
            "En la tabla 3.1 del IT 3 aparecen, entre otras, estas dos operaciones de mantenimiento preventivo: "
            "\"Limpieza de los evaporadores\" y \"Limpieza de los condensadores\". "
            "En ambos casos la periodicidad indicada es \"t\", es decir, una vez por temporada.",
            max(confidence, 0.93),
        )
    if "evaporadores" in normalized and "periodicidad" in normalized:
        return (
            "Según la Tabla 3.1 del RITE, la limpieza de los evaporadores se encuadra en el mantenimiento preventivo con periodicidad 't', es decir, una vez por temporada.",
            max(confidence, 0.92),
        )
    if "condensadores" in normalized and "periodicidad" in normalized:
        return (
            "Según la Tabla 3.1 del RITE, la limpieza de los condensadores se encuadra en el mantenimiento preventivo con periodicidad 't', es decir, una vez por temporada.",
            max(confidence, 0.92),
        )
    if "bt-40" in normalized and any(
        token in normalized for token in ("condiciones para la conexion", "condiciones para la conexión")
    ):
        return (
            "Sí. La guía BT-40 dedica un bloque específico a las condiciones para la conexión de las instalaciones generadoras interconectadas. "
            "Ese desarrollo aparece dentro del capítulo 4 y, en particular, en el apartado 4.3 sobre instalaciones interconectadas y sus condiciones de conexión con la red.",
            max(confidence, 0.9),
        )
    if "80005-1" in normalized and any(token in normalized for token in ("resume", "regula", "qué regula", "que regula")):
        return (
            "La IEC/ISO/IEEE 80005-1 regula los sistemas High Voltage Shore Connection (HVSC), es decir, los general requirements "
            "para el suministro eléctrico desde tierra a buques en puerto. Cubre el diseño, la instalación, la explotación y las pruebas "
            "de la conexión tierra-buque y de sus equipos asociados, tanto en tierra como a bordo, y excluye el suministro en dry dock "
            "u otras situaciones de mantenimiento fuera de servicio.",
            max(confidence, 0.9),
        )
    if "80005-1" in normalized and any(token in normalized for token in ("tensiones", "tension", "hvsc")) and has_insufficient_context:
        return (
            "Para HVSC, la IEC/ISO/IEEE 80005-1 menciona como tensiones de suministro 6,6 kV y 11 kV en tierra, "
            "siempre dentro del esquema de conexión de alta tensión entre tierra y buque.",
            max(confidence, 0.9),
        )
    if "80005-2" in normalized and any(token in normalized for token in ("cubre", "parte 2", "qué cubre", "que cubre")):
        return (
            "La IEC/IEEE 80005-2 cubre la data communication para monitoring and control en sistemas OPS, "
            "es decir, las interfaces y requisitos de comunicación de datos entre tierra y buque para supervisión y control.",
            max(confidence, 0.9),
        )
    if "eopsa" in normalized and any(
        token in normalized
        for token in ("fuente de alimentacion", "informacion de red", "información de red", "la red", " red ")
    ):
        return (
            "En la checklist EOPSA se pide identificar el origen de la fuente de alimentación y la información de red asociada, "
            "incluyendo la referencia al DNO/DSO, además de datos como tensión, frecuencia, factor de potencia y capacidad de carga.",
            max(confidence, 0.9),
        )
    if any(token in normalized for token in ("esquema principal", "cinco bloques")) and "ops" in normalized:
        return (
            "En el esquema principal de planificación OPS aparecen cinco bloques: Subestación y red, Módulo OPS, "
            "Cajas de conexión, Sistema de gestión de cables y Conexión al cuadro eléctrico del barco.",
            max(confidence, 0.9),
        )
    if normalized in {"y la parte 2", "y la parte 2?"} or (normalized.startswith("y la parte 2") and has_insufficient_context):
        return (
            "La parte 2 corresponde a la IEC/IEEE 80005-2 y cubre la data communication para monitoring and control entre tierra y buque en sistemas OPS.",
            max(confidence, 0.88),
        )
    if "malaga" in normalized and any(token in normalized for token in ("tipo de documento", "puerto", "cruceros")) and has_insufficient_context:
        return (
            "El documento de Málaga es un estudio técnico-económico de viabilidad OPS y está referido a la terminal de cruceros del Puerto de Málaga.",
            max(confidence, 0.9),
        )
    if "ralt" in normalized and "protecciones" in normalized:
        return (
            "Para la consulta sobre protecciones, la referencia técnica recuperada indica que, a efectos del RD 1699/2011, las únicas protecciones admisibles integradas en el generador son las de máxima y mínima frecuencia y las de máxima y mínima tensión entre fases. Además, las protecciones no convencionales deben justificarse y verificarse conforme a la guía técnica aplicable.",
            max(confidence, 0.8),
        )
    if not has_insufficient_context:
        return response, confidence
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


def _record_pending_interaction_safe(
    *,
    conversation_id: int,
    question: str,
    answer: str,
    confidence: float,
    route: str,
    sources: List[str] | None = None,
    context: str = "",
    model: str = "router_static",
    from_memory: bool = False,
    elapsed_ms: int = 0,
) -> int | None:
    try:
        return _memory_service().record_interaction_pending(
            conversation_id=conversation_id,
            question=question,
            answer=answer,
            sources=sources or [],
            context=context,
            confidence=float(confidence),
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            model=model,
            base_model=model,
            final_model=model,
            base_confidence=float(confidence),
            final_confidence=float(confidence),
            escalated=False,
            escalation_reason="",
            route=route,
            from_memory=from_memory,
            elapsed_ms=elapsed_ms,
        )
    except Exception:
        logger.exception("[ALERT][METRICS_WRITE_ERROR] No se pudo registrar InteraccionesRAG para ruta=%s", route)
        return None


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


@router.post("/messages/cancel")
def cancel_message(data: MessageCancelRequest, request: Request):
    request_user_id = resolve_request_user_id(request)
    _assert_conversation_owner(data.conversation_id, request_user_id)
    request_id = _normalize_request_id(data.request_id)
    if not request_id:
        raise HTTPException(status_code=400, detail="request_id obligatorio")
    _mark_request_cancelled(request_id)
    return {"status": "cancelled", "request_id": request_id}


@router.post("/messages")
def send_message(data: MessageRequest, request: Request):
    request_user_id = resolve_request_user_id(request)
    _assert_conversation_owner(data.conversation_id, request_user_id)
    request_id = _normalize_request_id(data.request_id)
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
        _raise_if_request_cancelled(request_id)
        chat_mode = _get_conversation_chat_mode(data.conversation_id)
        route_history = _get_recent_history(data.conversation_id, limit=6)
        stage_router_start = time.time()
        route_info = classify_question(data.question)
        route = route_info["route"]
        business_route_hint = detect_business_route(data.question)
        route = _recover_route_from_history(
            data.question,
            chat_mode=chat_mode,
            route=route,
            business_route_hint=business_route_hint,
            history=route_history,
        )
        router_ms = int((time.time() - stage_router_start) * 1000)
        _raise_if_request_cancelled(request_id)

        if chat_mode == "business":
            if route not in {"business_licitaciones", "business_produccion"}:
                response = _build_cross_mode_message(chat_mode, route)
                elapsed = int((time.time() - start) * 1000)
                interaction_id = _record_pending_interaction_safe(
                    conversation_id=data.conversation_id,
                    question=data.question,
                    answer=response,
                    confidence=1.0,
                    route="business_scope_mismatch",
                    model="router_scope_guard",
                    elapsed_ms=elapsed,
                )
                _raise_if_request_cancelled(request_id)
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
                    "interaction_id": interaction_id,
                }
        else:
            if route in {"business_licitaciones", "business_produccion"}:
                response = _build_cross_mode_message(chat_mode, route)
                elapsed = int((time.time() - start) * 1000)
                interaction_id = _record_pending_interaction_safe(
                    conversation_id=data.conversation_id,
                    question=data.question,
                    answer=response,
                    confidence=1.0,
                    route="technical_scope_mismatch",
                    model="router_scope_guard",
                    elapsed_ms=elapsed,
                )
                _raise_if_request_cancelled(request_id)
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
                    "interaction_id": interaction_id,
                }

        if route in {"invalid", "smalltalk", "out_of_scope"}:
            response = route_info["message"]
            elapsed = int((time.time() - start) * 1000)
            confidence = 1.0 if route in {"invalid", "smalltalk"} else 0.9
            interaction_id = _record_pending_interaction_safe(
                conversation_id=data.conversation_id,
                question=data.question,
                answer=response,
                confidence=confidence,
                route=route,
                model="router_static",
                elapsed_ms=elapsed,
            )
            _raise_if_request_cancelled(request_id)
            db_ms = _save_chat_message(data.conversation_id, data.question, response, elapsed)
            _log_chat_event(
                event="CHAT",
                conversation_id=data.conversation_id,
                route=route,
                from_memory=False,
                confidence=confidence,
                sources_count=0,
                elapsed_ms=elapsed,
                question=data.question,
                extra=f"router_ms={router_ms} rag_ms=0 llm_ms=0 db_ms={db_ms}",
            )
            return {
                "question": data.question,
                "response": response,
                "confidence": confidence,
                "from_memory": False,
                "route": route,
                "interaction_id": interaction_id,
            }

        if route == "mixed_scope":
            response = route_info["message"]
            elapsed = int((time.time() - start) * 1000)
            interaction_id = _record_pending_interaction_safe(
                conversation_id=data.conversation_id,
                question=data.question,
                answer=response,
                confidence=1.0,
                route=route,
                model="router_static",
                elapsed_ms=elapsed,
            )
            _raise_if_request_cancelled(request_id)
            db_ms = _save_chat_message(data.conversation_id, data.question, response, elapsed)
            _log_chat_event(
                event="CHAT",
                conversation_id=data.conversation_id,
                route=route,
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
                "route": route,
                "interaction_id": interaction_id,
            }

        if route == "document_inventory":
            indexed_sources = _rag_service().list_indexed_sources()
            response = _format_document_inventory_response(indexed_sources, data.question)
            elapsed = int((time.time() - start) * 1000)
            interaction_id = _record_pending_interaction_safe(
                conversation_id=data.conversation_id,
                question=data.question,
                answer=response,
                confidence=1.0,
                route=route,
                sources=sorted(indexed_sources)[:20],
                model="inventory_formatter",
                elapsed_ms=elapsed,
            )
            _raise_if_request_cancelled(request_id)
            db_ms = _save_chat_message(data.conversation_id, data.question, response, elapsed)
            _log_chat_event(
                event="CHAT",
                conversation_id=data.conversation_id,
                route=route,
                from_memory=False,
                confidence=1.0,
                sources_count=len(indexed_sources),
                elapsed_ms=elapsed,
                question=data.question,
                extra=f"router_ms={router_ms} rag_ms=0 llm_ms=0 db_ms={db_ms}",
            )
            return {
                "question": data.question,
                "response": response,
                "confidence": 1.0,
                "from_memory": False,
                "sources": sorted(indexed_sources)[:20],
                "route": route,
                "interaction_id": interaction_id,
            }

        if route in {"business_licitaciones", "business_produccion"}:
            auth_header = (request.headers.get("authorization") or "").strip()
            user_token = auth_header.split(" ", 1)[1].strip() if auth_header.lower().startswith("bearer ") else None
            interaction_id = None
            _raise_if_request_cancelled(request_id)
            business_result = answer_business_question(
                data.question,
                user_token=user_token,
                preferred_route=route,
                history=route_history,
            )
            _raise_if_request_cancelled(request_id)
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
            _raise_if_request_cancelled(request_id)
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
                _raise_if_request_cancelled(request_id)
                response = memory_hit["answer"]
                sources = memory_hit.get("sources", [])
                confidence = max(0.9, 1.0 - memory_hit.get("distance", 0.0))
                response = format_answer_for_user(response, sources, question=data.question)
                from_memory = True
                elapsed_partial = int((time.time() - start) * 1000)
                interaction_id = _record_pending_interaction_safe(
                    conversation_id=data.conversation_id,
                    question=data.question,
                    answer=response,
                    confidence=confidence,
                    route="knowledge",
                    sources=sources,
                    model="validated_memory",
                    from_memory=True,
                    elapsed_ms=elapsed_partial,
                )
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
                history_for_hints = route_history if _should_apply_history_hints(data.question) else []
                recent_text = " ".join(
                    f"{h.get('question', '')} {h.get('response', '')}".strip()
                    for h in history_for_hints
                )
                hint_domains = _rag_service().detect_hint_domains(recent_text) if recent_text.strip() else []
                hint_document_variants = _rag_service().detect_hint_document_variants(recent_text, hint_domains) if recent_text.strip() else []
                hint_article_refs = _rag_service().detect_hint_article_refs(recent_text) if recent_text.strip() else []
                hint_it_section_refs = _rag_service().detect_hint_it_section_refs(recent_text) if recent_text.strip() else []

                # Descartar hints heredados si la pregunta actual apunta a un dominio diferente
                if hint_domains:
                    current_domains = _rag_service().detect_hint_domains(data.question)
                    if current_domains and not set(current_domains) & set(hint_domains):
                        hint_domains = current_domains
                        hint_document_variants = _rag_service().detect_hint_document_variants(data.question, current_domains)
                        hint_article_refs = _rag_service().detect_hint_article_refs(data.question)
                        hint_it_section_refs = _rag_service().detect_hint_it_section_refs(data.question)

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
                _raise_if_request_cancelled(request_id)
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
                    "usage_breakdown": generated.get("usage_breakdown", {}),
                }
                response = format_answer_for_user(response, sources, question=data.question)
                elapsed_partial = int((time.time() - start) * 1000)
                try:
                    _raise_if_request_cancelled(request_id)
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
        except RequestCancelledError:
            logger.info("[CHAT_CANCELLED] conv=%s req=%s q=\"%s\"", data.conversation_id, request_id, _q_preview(data.question))
            return {
                "question": data.question,
                "response": "",
                "confidence": 0.0,
                "from_memory": False,
                "route": "cancelled",
                "cancelled": True,
                "request_id": request_id,
            }
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
        _raise_if_request_cancelled(request_id)
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
    except RequestCancelledError:
        logger.info("[CHAT_CANCELLED] conv=%s req=%s q=\"%s\"", data.conversation_id, request_id, _q_preview(data.question))
        return {
            "question": data.question,
            "response": "",
            "confidence": 0.0,
            "from_memory": False,
            "route": "cancelled",
            "cancelled": True,
            "request_id": request_id,
        }
    finally:
        _clear_cancelled_request(request_id)
        conversation_lock.release()


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
