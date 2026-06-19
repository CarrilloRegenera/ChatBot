import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from azure_rag_service import (
    _build_index_fields,
    _build_semantic_config,
    _compose_search_text,
    _search_all_indexed_sources,
)
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


def test_compose_search_text_enriches_ops_normative_queries():
    text = _compose_search_text(
        "Que es OPS segun la IEC 80005-1?",
        "que es ops segun la iec 80005-1",
        ["ops"],
    )
    assert "IEC/ISO/IEEE 80005-1" in text
    assert "High Voltage Shore Connection" in text
    assert "Utility connections in port" in text


def test_compose_search_text_enriches_ops_monitoring_queries():
    text = _compose_search_text(
        "Como funciona la monitorizacion y control general en OPS?",
        "como funciona la monitorizacion y control general en ops",
        ["ops"],
    )
    assert "IEC/IEEE 80005-2" in text
    assert "data communication for monitoring and control" in text.lower()
    assert "SCADA" in text


def test_build_semantic_config_uses_supported_title_field_shape():
    config = _build_semantic_config()
    prioritized = config.prioritized_fields

    assert prioritized is not None
    assert getattr(prioritized, "title_field", None) is not None
    assert getattr(prioritized.title_field, "field_name", None) == "section"


def test_search_all_indexed_sources_reads_all_pages_without_losing_documents():
    class FakeClient:
        def __init__(self):
            self.calls = []

        def search(self, **kwargs):
            self.calls.append(kwargs)
            skip = kwargs.get("skip", 0)
            top = kwargs.get("top", 1000)
            items = [
                {"source_path": "baja_tension/BOE-326_Reglamento_electrotecnico_para_baja_tension_e_ITC.pdf", "file_hash": "h1"},
                {"source_path": "baja_tension/BOE-326_Reglamento_electrotecnico_para_baja_tension_e_ITC.pdf", "file_hash": "h1"},
                {"source_path": "baja_tension/errores_frecuentes_2023_v2.pdf", "file_hash": "h2"},
                {"source_path": "baja_tension/manual-electricidad-baja-tension-1.pdf", "file_hash": "h3"},
                {"source_path": "baja_tension/soluciones_situaciones_particulares_2023.pdf", "file_hash": "h4"},
            ]
            return items[skip:skip + top]

    client = FakeClient()

    result = _search_all_indexed_sources(client, batch_size=2)

    assert result == {
        "baja_tension/BOE-326_Reglamento_electrotecnico_para_baja_tension_e_ITC.pdf": "h1",
        "baja_tension/errores_frecuentes_2023_v2.pdf": "h2",
        "baja_tension/manual-electricidad-baja-tension-1.pdf": "h3",
        "baja_tension/soluciones_situaciones_particulares_2023.pdf": "h4",
    }
    assert [call["skip"] for call in client.calls] == [0, 2, 4]
