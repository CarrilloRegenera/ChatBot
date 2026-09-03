import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import memory_service


def test_record_interaction_pending_persists_latency_breakdown():
    cursor = mock.Mock()
    cursor.fetchone.return_value = [321]
    connection = mock.Mock()
    connection.cursor.return_value = cursor

    with mock.patch("memory_service.db_conn") as db_conn:
        db_conn.return_value.__enter__.return_value = connection
        interaction_id = memory_service.record_interaction_pending(
            7, "pregunta", "respuesta", [], "", 0.8,
            router_ms=12, rag_ms=130, llm_ms=900, db_ms=8,
            reasoning_effort="low",
        )

    assert interaction_id == 321
    assert "RouterMs, RagMs, LlmMs, DbMs, EsfuerzoRazonamiento" in cursor.execute.call_args.args[0]
    assert cursor.execute.call_args.args[-5:] == (12, 130, 900, 8, "low")


def test_update_interaction_latency_writes_all_stages():
    cursor = mock.Mock()
    connection = mock.Mock()
    connection.cursor.return_value = cursor

    with mock.patch("memory_service.db_conn") as db_conn:
        db_conn.return_value.__enter__.return_value = connection
        memory_service.update_interaction_latency(321, router_ms=12, rag_ms=130, llm_ms=900, db_ms=8)

    assert "UPDATE dbo.InteraccionesRAG" in cursor.execute.call_args.args[0]
    assert cursor.execute.call_args.args[1:] == (12, 130, 900, 8, 321)
