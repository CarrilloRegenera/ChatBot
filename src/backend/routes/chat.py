import logging
import re
import time
from threading import Lock, Thread
from typing import Dict, List

from fastapi import APIRouter, HTTPException, Request

from ai_service import AIResponseError, format_answer_for_user, generate_ai_response_with_fallback
from business_query_service import answer_business_question, detect_business_route
from chat_conversation_service import (
    assert_conversation_owner as _assert_conversation_owner_impl,
    build_cross_mode_message as _build_cross_mode_message_impl,
    build_history_interaction_join as _build_history_interaction_join_impl,
    get_conversation_chat_mode as _get_conversation_chat_mode_impl,
    get_recent_history as _get_recent_history_impl,
    infer_chat_mode_from_title as _infer_chat_mode_from_title_impl,
    normalize_chat_mode as _normalize_chat_mode_impl,
    save_chat_message as _save_chat_message_impl,
)
from chat_document_sync_service import (
    DEFAULT_SYNC_STATUS,
    export_state as _export_document_sync_state_impl,
    import_state as _import_document_sync_state_impl,
    start_document_sync_background as _start_document_sync_background_impl,
    update_document_sync_status as _update_document_sync_status_impl,
)
from chat_technical_response_service import (
    apply_known_technical_answer_overrides as _apply_known_technical_answer_overrides_impl,
    augment_retrieval_question as _augment_retrieval_question_impl,
)
from config import CONVERSATION_LOCK_TIMEOUT_SECS, TOP_K_COMPLEX_QUERY, TOP_K_SYMBOL_QUERY
from database import db_conn
from document_inventory_service import format_document_inventory_response
from models import (
    ConversationRequest,
    MessageCancelRequest,
    MessageRequest,
)
from query_router import classify_question
from routes.auth_helpers import assert_admin, resolve_request_user_id
from routes.chat_followup import (
    derive_history_hints,
    is_abbreviation_query,
    is_symbol_definition_query,
    recover_route_from_history,
    should_apply_history_hints,
)
from routes.chat_runtime_state import (
    RequestCancelledError,
    clear_cancelled_request,
    get_conversation_lock as runtime_get_conversation_lock,
    mark_request_cancelled,
    normalize_request_id,
    q_preview,
    raise_if_request_cancelled,
    remove_conversation_lock,
)
from routing_signals import has_concrete_business_reference


router = APIRouter()
logger = logging.getLogger(__name__)
_document_sync_lock = Lock()
_document_sync_inflight = False
_document_sync_status = dict(DEFAULT_SYNC_STATUS)
_FOLLOWUP_PREFIX_RE = re.compile(
    r"^(?:y|entonces|ademas|además|tambien|también|sobre eso|sobre ello|respecto a eso|respecto a ello|en ese caso)\b"
)


def _normalize_followup_text(question: str) -> str:
    normalized = " ".join((question or "").strip().lower().split())
    return normalized.lstrip("¿?¡!.,;:()[]{}\"' ")


def _memory_service():
    import memory_service

    return memory_service


def _update_document_sync_status(**updates) -> Dict[str, object]:
    global _document_sync_status, _document_sync_inflight
    _import_document_sync_state_impl(_document_sync_status, _document_sync_inflight)
    _document_sync_status = _update_document_sync_status_impl(**updates)
    _document_sync_status, _document_sync_inflight = _export_document_sync_state_impl()
    return dict(_document_sync_status)


def _start_document_sync_background() -> Dict[str, object]:
    global _document_sync_status, _document_sync_inflight
    _import_document_sync_state_impl(_document_sync_status, _document_sync_inflight)

    def _sync_globals(status: Dict[str, object], inflight: bool) -> None:
        global _document_sync_status, _document_sync_inflight
        _document_sync_status = dict(status)
        _document_sync_inflight = bool(inflight)

    initial = _start_document_sync_background_impl(
        rag_service_factory=_rag_service,
        logger=logger,
        state_callback=_sync_globals,
    )
    _document_sync_status, _document_sync_inflight = _export_document_sync_state_impl()
    return dict(initial)


def _rag_service():
    import rag_service

    return rag_service


def _normalize_chat_mode(value: str | None) -> str:
    return _normalize_chat_mode_impl(value)


def _infer_chat_mode_from_title(title: str | None) -> str:
    return _infer_chat_mode_from_title_impl(title)


def _get_conversation_chat_mode(conversation_id: int) -> str:
    return _get_conversation_chat_mode_impl(
        conversation_id,
        db_conn=db_conn,
    )


def _build_cross_mode_message(chat_mode: str, route: str) -> str:
    return _build_cross_mode_message_impl(chat_mode, route)


def _should_apply_history_hints(question: str) -> bool:
    return should_apply_history_hints(
        question,
        rag_service=_rag_service(),
    )


def _augment_if_symbol_query(
    rag_question: str,
    original_question: str,
    hint_domains: List[str],
    hint_it_section_refs: List[str],
) -> str:
    """Expande queries de resolución de símbolo/abreviatura con contexto heredado.

    Sin este paso, 'qué significa la t' busca 't' en todos los dominios.
    Con el dominio y sección heredados, el retrieval se focaliza en los chunks
    de leyenda correctos.
    """
    if not (
        is_symbol_definition_query(original_question)
        or is_abbreviation_query(original_question)
    ):
        return rag_question
    parts = [rag_question, "leyenda definicion abreviatura simbolo periodicidad tabla"]
    if hint_it_section_refs:
        parts.extend(hint_it_section_refs[:2])
    if hint_domains:
        parts.extend(hint_domains[:2])
    return " ".join(parts)


def _maybe_inherit_inventory_route(question: str, history: List[Dict]) -> str | None:
    """Si la pregunta es un follow-up y el turno anterior fue inventario, hereda la ruta."""
    normalized = _normalize_followup_text(question)
    if not _FOLLOWUP_PREFIX_RE.search(normalized):
        return None
    for item in reversed((history or [])[-3:]):
        prev_q = str(item.get("question", "")).strip()
        if prev_q and classify_question(prev_q).get("route") == "document_inventory":
            return "document_inventory"
    return None


def _recover_route_from_history(
    question: str,
    *,
    chat_mode: str,
    route: str,
    business_route_hint: str | None,
    history: List[Dict[str, str]] | None = None,
) -> str:
    return recover_route_from_history(
        question,
        chat_mode=chat_mode,
        route=route,
        business_route_hint=business_route_hint,
        history=history,
        detect_business_route=detect_business_route,
        has_concrete_business_reference=has_concrete_business_reference,
    )


def _augment_retrieval_question(question: str) -> str:
    return _augment_retrieval_question_impl(question)


def _apply_known_technical_answer_overrides(question: str, response: str, confidence: float) -> tuple[str, float]:
    return _apply_known_technical_answer_overrides_impl(question, response, confidence)


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
        q_preview(question),
        extra,
    )


def get_conversation_lock(conversation_id: int) -> Lock:
    return runtime_get_conversation_lock(conversation_id)


def _get_recent_history(conversation_id: int, limit: int = 2) -> List[Dict]:
    return _get_recent_history_impl(
        conversation_id,
        db_conn=db_conn,
        format_answer_for_user=format_answer_for_user,
        limit=limit,
    )


def _save_chat_message(conversation_id: int, question: str, response: str, elapsed_ms: int) -> int:
    return _save_chat_message_impl(
        conversation_id,
        question,
        response,
        elapsed_ms,
        db_conn=db_conn,
    )


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
    return _build_history_interaction_join_impl()


def _assert_conversation_owner(conversation_id: int, request_user_id: int) -> tuple:
    return _assert_conversation_owner_impl(
        conversation_id,
        request_user_id,
        db_conn=db_conn,
    )


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

    remove_conversation_lock(conversation_id)

    return {"message": "Conversacion eliminada", "conversation_id": conversation_id}


@router.post("/messages/cancel")
def cancel_message(data: MessageCancelRequest, request: Request):
    request_user_id = resolve_request_user_id(request)
    _assert_conversation_owner(data.conversation_id, request_user_id)
    request_id = normalize_request_id(data.request_id)
    if not request_id:
        raise HTTPException(status_code=400, detail="request_id obligatorio")
    mark_request_cancelled(request_id)
    return {"status": "cancelled", "request_id": request_id}


@router.post("/messages")
def send_message(data: MessageRequest, request: Request):
    request_user_id = resolve_request_user_id(request)
    _assert_conversation_owner(data.conversation_id, request_user_id)
    request_id = normalize_request_id(data.request_id)
    conversation_lock = get_conversation_lock(data.conversation_id)
    acquired = conversation_lock.acquire(timeout=CONVERSATION_LOCK_TIMEOUT_SECS)
    if not acquired:
        logger.warning(
            "[LOCK_TIMEOUT] conv=%s waited=%ss q=\"%s\"",
            data.conversation_id,
            CONVERSATION_LOCK_TIMEOUT_SECS,
            q_preview(data.question),
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
        raise_if_request_cancelled(request_id)
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
        if route != "document_inventory":
            inherited = _maybe_inherit_inventory_route(data.question, route_history)
            if inherited:
                route = inherited
        router_ms = int((time.time() - stage_router_start) * 1000)
        raise_if_request_cancelled(request_id)

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
                raise_if_request_cancelled(request_id)
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
                raise_if_request_cancelled(request_id)
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
            raise_if_request_cancelled(request_id)
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
            raise_if_request_cancelled(request_id)
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
            response = format_document_inventory_response(
                indexed_sources,
                data.question,
                detect_hint_domains=_rag_service().detect_hint_domains,
            )
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
            raise_if_request_cancelled(request_id)
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
            raise_if_request_cancelled(request_id)
            business_result = answer_business_question(
                data.question,
                user_token=user_token,
                preferred_route=route,
                history=route_history,
            )
            raise_if_request_cancelled(request_id)
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
            raise_if_request_cancelled(request_id)
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
                raise_if_request_cancelled(request_id)
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
                history_focus = derive_history_hints(
                    history_for_hints,
                    rag_service=_rag_service(),
                )
                hint_domains = history_focus["domains"]
                hint_document_variants = history_focus["document_variants"]
                hint_article_refs = history_focus["article_refs"]
                hint_it_section_refs = history_focus["it_section_refs"]

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
                rag_question = _augment_if_symbol_query(
                    rag_question, data.question, hint_domains, hint_it_section_refs
                )
                n_results = (
                    TOP_K_SYMBOL_QUERY
                    if (is_symbol_definition_query(data.question) or is_abbreviation_query(data.question))
                    else TOP_K_COMPLEX_QUERY
                )
                context, sources, retrieval_stats = _rag_service().search_documents_detailed(
                    rag_question,
                    n_results=n_results,
                    hint_domains=hint_domains or None,
                    hint_document_variants=hint_document_variants or None,
                    hint_article_refs=hint_article_refs or None,
                    hint_it_section_refs=hint_it_section_refs or None,
                )
                rag_ms = int((time.time() - stage_rag_start) * 1000)
                raise_if_request_cancelled(request_id)
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
                    raise_if_request_cancelled(request_id)
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
            logger.info("[CHAT_CANCELLED] conv=%s req=%s q=\"%s\"", data.conversation_id, request_id, q_preview(data.question))
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
        raise_if_request_cancelled(request_id)
        db_ms += _save_chat_message(data.conversation_id, data.question, response, elapsed)

        if llm_retries > 0:
            logger.warning("[ALERT][LLM_RETRY] conv=%s retries=%s q=\"%s\"", data.conversation_id, llm_retries, q_preview(data.question))
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
        logger.info("[CHAT_CANCELLED] conv=%s req=%s q=\"%s\"", data.conversation_id, request_id, q_preview(data.question))
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
        clear_cancelled_request(request_id)
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
