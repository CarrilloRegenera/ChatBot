import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from azure_rag_service import _build_index_fields
from rag_service import _chunk_profile_metadata, _document_profile_metadata


def test_azure_index_schema_includes_expected_enriched_fields():
    field_names = {field.name for field in _build_index_fields()}
    assert "document_variant" in field_names
    assert "article_refs" in field_names
    assert "it_section_refs" in field_names


def test_azure_index_schema_covers_generated_profile_metadata():
    field_names = {field.name for field in _build_index_fields()}
    document_profile = _document_profile_metadata("grupo_electrogenos/test.pdf", "grupos_electrogenos")
    chunk_profile = _chunk_profile_metadata(
        "grupo_electrogenos/test.pdf",
        "Articulo 12",
        "Segun el articulo 12 y la ITC-BT-01 de la tabla 3",
        "text",
    )

    missing = (set(document_profile) | set(chunk_profile)) - field_names
    assert missing == set()
