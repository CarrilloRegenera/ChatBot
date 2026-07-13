from typing import Callable, Dict, List


def finalize_static_chat_reply(
    *,
    conversation_id: int,
    question: str,
    response: str,
    confidence: float,
    route: str,
    elapsed_ms: int,
    sources: List[str] | None = None,
    model: str,
    from_memory: bool,
    record_pending_interaction_safe: Callable[..., int | None],
    save_chat_message: Callable[[int, str, str, int], int],
    log_chat_event: Callable[..., None],
    router_ms: int,
) -> Dict[str, object]:
    sources = list(sources or [])
    interaction_id = record_pending_interaction_safe(
        conversation_id=conversation_id,
        question=question,
        answer=response,
        confidence=confidence,
        route=route,
        sources=sources,
        model=model,
        from_memory=from_memory,
        elapsed_ms=elapsed_ms,
    )
    db_ms = save_chat_message(conversation_id, question, response, elapsed_ms)
    log_chat_event(
        event="CHAT",
        conversation_id=conversation_id,
        route=route,
        from_memory=from_memory,
        confidence=confidence,
        sources_count=len(sources),
        elapsed_ms=elapsed_ms,
        question=question,
        extra=f"router_ms={router_ms} rag_ms=0 llm_ms=0 db_ms={db_ms}",
    )
    payload: Dict[str, object] = {
        "question": question,
        "response": response,
        "confidence": confidence,
        "from_memory": from_memory,
        "route": route,
        "interaction_id": interaction_id,
    }
    if sources:
        payload["sources"] = sources
    return payload
