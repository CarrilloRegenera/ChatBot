import sys
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import azure_rag_service
from azure_rag_service import (
    _build_index_fields,
    _build_semantic_config,
    _compose_search_text,
    _iter_pdf_chunks,
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


def test_compose_search_text_adds_structural_reference_variants():
    text = _compose_search_text(
        "Segun el RITE consolidado, que dice el articulo 2?",
        "segun el rite consolidado que dice el articulo 2",
        ["rite"],
    )
    assert "articulo 2" in text
    assert "artículo 2" in text
    assert "art. 2" in text


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


def test_iter_pdf_chunks_refreshes_progress_while_processing_pages(monkeypatch):
    class FakePdf:
        def __init__(self, pages):
            self._pages = pages

        def __len__(self):
            return len(self._pages)

        def __iter__(self):
            return iter(self._pages)

        def close(self):
            return None

    fake_pages = [SimpleNamespace(number=1), SimpleNamespace(number=2)]
    progress_updates = []

    monkeypatch.setattr(
        azure_rag_service.fitz,
        "open",
        lambda **kwargs: FakePdf(fake_pages),
    )
    monkeypatch.setattr(
        azure_rag_service,
        "_extract_page_text",
        lambda page: f"Texto pagina {page.number}",
    )
    monkeypatch.setattr(
        azure_rag_service,
        "_decode_chunk_corruption",
        lambda text, blob_name: text,
    )
    monkeypatch.setattr(
        azure_rag_service,
        "_extract_text_blocks",
        lambda text: [{"text": text, "section": f"Seccion {text[-1]}"}],
    )
    monkeypatch.setattr(
        azure_rag_service,
        "_split_structured_blocks",
        lambda blocks: [(blocks[0]["text"], blocks[0]["section"])],
    )
    monkeypatch.setattr(azure_rag_service, "_looks_like_table_block", lambda chunk: False)
    monkeypatch.setattr(azure_rag_service, "_encode_passage", lambda chunk: [0.1, 0.2])
    monkeypatch.setattr(azure_rag_service, "_document_profile_metadata", lambda *args: {"domain": "ops"})
    monkeypatch.setattr(
        azure_rag_service,
        "_chunk_profile_metadata",
        lambda _blob_name, section, _chunk, _chunk_kind: {"section": section, "article_refs": "", "it_section_refs": ""},
    )

    docs = _iter_pdf_chunks(
        "ops/02_guias_implantacion/EMSA Guidance on SSE_PART1.pdf",
        b"%PDF",
        "hash123",
        "ops",
        progress_callback=progress_updates.append,
        processed_files=14,
        total_files=26,
    )

    assert len(docs) == 2
    assert [update["current_page"] for update in progress_updates] == [1, 2]
    assert all(update["processed_files"] == 14 for update in progress_updates)
    assert all(update["total_files"] == 26 for update in progress_updates)
    assert all(update["current_file"].endswith("EMSA Guidance on SSE_PART1.pdf") for update in progress_updates)
    assert docs[0]["section"] == "Seccion 1"
    assert docs[1]["section"] == "Seccion 2"
