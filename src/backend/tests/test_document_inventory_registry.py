import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from document_source_registry import list_active_document_sources


def test_list_active_document_sources_reads_only_indexed_backend_documents():
    cursor = mock.Mock()
    cursor.fetchall.return_value = [("rite/RITE IT3.pdf", "h1")]
    connection = mock.Mock()
    connection.cursor.return_value = cursor
    with mock.patch("document_source_registry.db_conn") as db_conn:
        db_conn.return_value.__enter__.return_value = connection
        assert list_active_document_sources("azure_search") == {"rite/RITE IT3.pdf": "h1"}
    assert "EstadoIndexacion = 'indexado'" in cursor.execute.call_args.args[0]
    assert cursor.execute.call_args.args[1] == "azure_search"
