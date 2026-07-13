from typing import Callable, Dict


def finalize_business_chat_reply(
    *,
    conversation_id: int,
    question: str,
    business_result: Dict[str, object],
    fallback_route: str,
    elapsed_ms: int,
    memory_service,
    save_chat_message: Callable[[int, str, str, int], int],
    log_chat_event: Callable[..., None],
    logger,
    router_ms: int,
) -> Dict[str, object]:
    business_route = str(business_result.get("route", fallback_route))
    response = str(business_result["response"])
    confidence = float(business_result.get("confidence", 1.0))
    business_trace = business_result.get("trace", {}) or {}
    business_sources = business_result.get("sources", []) or []

    interaction_id = None
    db_ms = 0
    try:
        business_path = str(business_trace.get("path") or "").strip().lower()
        business_model = "appregenera_sql" if business_path == "sql" else ("appregenera_http" if business_path == "http" else "appregenera")
        interaction_id = memory_service.record_interaction_pending(
            conversation_id=conversation_id,
            question=question,
            answer=response,
            sources=business_sources,
            context="",
            confidence=confidence,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            model=business_model,
            base_model=business_model,
            final_model=business_model,
            base_confidence=confidence,
            final_confidence=confidence,
            escalated=False,
            escalation_reason="",
            route=business_route,
            from_memory=False,
            elapsed_ms=elapsed_ms,
        )
    except Exception:
        logger.exception("[ALERT][METRICS_WRITE_ERROR] No se pudo registrar InteraccionesRAG de negocio")

    db_ms += save_chat_message(conversation_id, question, response, elapsed_ms)
    log_chat_event(
        event="CHAT",
        conversation_id=conversation_id,
        route=business_route,
        from_memory=False,
        confidence=confidence,
        sources_count=len(business_sources),
        elapsed_ms=elapsed_ms,
        question=question,
        extra=f"router_ms={router_ms} rag_ms=0 llm_ms=0 db_ms={db_ms}",
    )
    return {
        "question": question,
        "response": response,
        "confidence": confidence,
        "from_memory": False,
        "sources": business_sources,
        "route": business_route,
        "trace": business_trace,
        "interaction_id": interaction_id,
    }
