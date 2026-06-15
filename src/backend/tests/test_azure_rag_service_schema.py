import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from azure_rag_service import _build_index_fields


def test_azure_index_schema_includes_document_variant():
    field_names = {field.name for field in _build_index_fields()}
    assert "document_variant" in field_names
