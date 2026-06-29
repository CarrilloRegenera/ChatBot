from typing import Callable, List


def record_pending_interaction_safe(
    *,
    memory_service_factory: Callable[[], object],
    logger,
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
        return memory_service_factory().record_interaction_pending(
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
