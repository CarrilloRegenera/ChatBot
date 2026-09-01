import sys
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from document_source_registry import build_source_record, sync_document_source_registry


def _profile(source_path: str):
    assert source_path == "ops/manual.pdf"
    return {
        "department": "ingenieria",
        "domain": "ops",
        "document_type": "guia_tecnica",
        "document_layer": "guia_oficial",
    }


def test_build_source_record_normalizes_path_and_keeps_rag_profile():
    record = build_source_record(
        "\\ops\\manual.pdf",
        "hash-1",
        backend="Azure_Search",
        profile_resolver=_profile,
    )

    assert record["backend"] == "azure_search"
    assert record["source_path"] == "ops/manual.pdf"
    assert record["document_name"] == "manual.pdf"
    assert record["domain"] == "ops"
    assert len(record["source_key"]) == 64


def test_sync_registry_marks_missing_documents_retired_and_upserts_current_ones():
    cursor = mock.Mock()
    connection = mock.Mock()
    connection.cursor.return_value = cursor

    @contextmanager
    def fake_db_conn():
        yield connection

    with mock.patch("document_source_registry.db_conn", fake_db_conn):
        count = sync_document_source_registry(
            {"ops/manual.pdf": "hash-1"},
            backend="azure_search",
            profile_resolver=_profile,
        )

    assert count == 1
    assert cursor.execute.call_count == 2
    cleanup_sql, cleanup_backend = cursor.execute.call_args_list[0].args
    assert "EstadoIndexacion = 'retirado'" in cleanup_sql
    assert cleanup_backend == "azure_search"
    merge_sql = cursor.execute.call_args_list[1].args[0]
    assert "MERGE dbo.DocumentosFuente" in merge_sql
    assert "EstadoVigencia" in merge_sql
