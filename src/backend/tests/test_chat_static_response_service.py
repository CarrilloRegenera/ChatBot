import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chat_business_response_service import finalize_business_chat_reply
from chat_static_response_service import finalize_static_chat_reply


def test_finalize_static_chat_reply_records_and_formats_payload():
    captured = {}

    def record_pending_interaction_safe(**kwargs):
        captured["record"] = kwargs
        return 123

    def save_chat_message(conversation_id: int, question: str, response: str, elapsed_ms: int) -> int:
        captured["save"] = {
            "conversation_id": conversation_id,
            "question": question,
            "response": response,
            "elapsed_ms": elapsed_ms,
        }
        return 7

    def log_chat_event(**kwargs):
        captured["log"] = kwargs

    result = finalize_static_chat_reply(
        conversation_id=5,
        question="hola",
        response="respuesta",
        confidence=1.0,
        route="smalltalk",
        elapsed_ms=42,
        sources=["a.pdf"],
        model="router_static",
        from_memory=False,
        record_pending_interaction_safe=record_pending_interaction_safe,
        save_chat_message=save_chat_message,
        log_chat_event=log_chat_event,
        router_ms=3,
    )

    assert result["interaction_id"] == 123
    assert result["route"] == "smalltalk"
    assert result["sources"] == ["a.pdf"]
    assert captured["record"]["model"] == "router_static"
    assert captured["save"]["conversation_id"] == 5
    assert captured["log"]["sources_count"] == 1


def test_finalize_business_chat_reply_records_metrics_and_returns_trace():
    captured = {}

    class FakeMemoryService:
        def record_interaction_pending(self, **kwargs):
            captured["record"] = kwargs
            return 456

    class FakeLogger:
        def exception(self, message: str):
            captured["logger"] = message

    def save_chat_message(conversation_id: int, question: str, response: str, elapsed_ms: int) -> int:
        captured["save"] = {
            "conversation_id": conversation_id,
            "question": question,
            "response": response,
            "elapsed_ms": elapsed_ms,
        }
        return 11

    def log_chat_event(**kwargs):
        captured["log"] = kwargs

    result = finalize_business_chat_reply(
        conversation_id=8,
        question="Que cliente tiene el proyecto 26001",
        business_result={
            "response": "Cliente = ESAMUR",
            "route": "business_produccion",
            "confidence": 0.95,
            "sources": ["sql:produccion"],
            "trace": {"path": "sql", "module": "produccion"},
        },
        fallback_route="business_produccion",
        elapsed_ms=88,
        memory_service=FakeMemoryService(),
        save_chat_message=save_chat_message,
        log_chat_event=log_chat_event,
        logger=FakeLogger(),
        router_ms=6,
    )

    assert result["interaction_id"] == 456
    assert result["route"] == "business_produccion"
    assert result["trace"]["path"] == "sql"
    assert captured["record"]["model"] == "appregenera_sql"
    assert captured["save"]["response"] == "Cliente = ESAMUR"
    assert captured["log"]["sources_count"] == 1
