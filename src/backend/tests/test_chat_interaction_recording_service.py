import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chat_interaction_recording_service import record_pending_interaction_safe


def test_record_pending_interaction_safe_uses_static_defaults():
    fake_memory = mock.Mock()
    fake_memory.record_interaction_pending.return_value = 123
    logger = mock.Mock()

    interaction_id = record_pending_interaction_safe(
        memory_service_factory=lambda: fake_memory,
        logger=logger,
        conversation_id=7,
        question="Que documentos tenemos de OPS?",
        answer="Respuesta",
        confidence=0.95,
        route="document_inventory",
        sources=["ops/demo.pdf"],
        elapsed_ms=10,
    )

    assert interaction_id == 123
    fake_memory.record_interaction_pending.assert_called_once_with(
        conversation_id=7,
        question="Que documentos tenemos de OPS?",
        answer="Respuesta",
        sources=["ops/demo.pdf"],
        context="",
        confidence=0.95,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        model="router_static",
        base_model="router_static",
        final_model="router_static",
        base_confidence=0.95,
        final_confidence=0.95,
        escalated=False,
        escalation_reason="",
        route="document_inventory",
        from_memory=False,
        elapsed_ms=10,
    )
    logger.exception.assert_not_called()


def test_record_pending_interaction_safe_logs_and_returns_none_on_failure():
    fake_memory = mock.Mock()
    fake_memory.record_interaction_pending.side_effect = RuntimeError("boom")
    logger = mock.Mock()

    interaction_id = record_pending_interaction_safe(
        memory_service_factory=lambda: fake_memory,
        logger=logger,
        conversation_id=7,
        question="Que documentos tenemos de OPS?",
        answer="Respuesta",
        confidence=0.95,
        route="document_inventory",
    )

    assert interaction_id is None
    logger.exception.assert_called_once()
