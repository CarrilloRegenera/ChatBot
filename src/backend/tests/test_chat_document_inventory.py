import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rag_service
from document_inventory_service import format_document_inventory_response


def test_format_document_inventory_response_groups_domains_and_counts():
    response = format_document_inventory_response(
        {
            "ops/01_normativa_base/1329648_CENELEC_IEC_PDF.pdf": "h1",
            "ops/03_checklists_operacion/EOPSA_Checklist_OPS_ES.pdf": "h2",
            "rite/RITE IT3.pdf": "h3",
            "baja_tension/BOE-326_Reglamento_electrotecnico_para_baja_tension_e_ITC.pdf": "h4",
        },
        detect_hint_domains=rag_service.detect_hint_domains,
    )

    assert "Los documentos tecnicos disponibles son:" in response
    assert "- 1329648_CENELEC_IEC_PDF.pdf" in response
    assert "- EOPSA_Checklist_OPS_ES.pdf" in response
    assert "- RITE IT3.pdf" in response
    assert "- BOE-326_Reglamento_electrotecnico_para_baja_tension_e_ITC.pdf" in response
    assert "documento(s)" not in response
    assert "01_normativa_base/" not in response


def test_format_document_inventory_response_handles_empty_inventory():
    response = format_document_inventory_response({}, detect_hint_domains=rag_service.detect_hint_domains)
    assert "no veo documentos tecnicos indexados" in response.lower()


def test_format_document_inventory_response_can_focus_on_ops_only():
    response = format_document_inventory_response(
        {
            "ops/01_normativa_base/1329648_CENELEC_IEC_PDF.pdf": "h1",
            "ops/03_checklists_operacion/EOPSA_Checklist_OPS_ES.pdf": "h2",
            "rite/RITE IT3.pdf": "h3",
        },
        "Quiero que me digas solo los documentos que tenemos de OPS",
        detect_hint_domains=rag_service.detect_hint_domains,
    )

    assert "Los documentos que tenemos en OPS son:" in response
    assert "- 1329648_CENELEC_IEC_PDF.pdf" in response
    assert "- EOPSA_Checklist_OPS_ES.pdf" in response
    assert "RITE IT3.pdf" not in response


def test_format_document_inventory_response_can_focus_on_other_areas_too():
    response = format_document_inventory_response(
        {
            "ops/01_normativa_base/1329648_CENELEC_IEC_PDF.pdf": "h1",
            "rite/RITE IT3.pdf": "h2",
            "rite/RITE-2021-BOE-A-2021-4572.pdf": "h3",
            "baja_tension/BOE-326_Reglamento_electrotecnico_para_baja_tension_e_ITC.pdf": "h4",
        },
        "Que documentos tenemos de RITE?",
        detect_hint_domains=rag_service.detect_hint_domains,
    )

    assert "Los documentos que tenemos en RITE son:" in response
    assert "- RITE IT3.pdf" in response
    assert "- RITE-2021-BOE-A-2021-4572.pdf" in response
    assert "1329648_CENELEC_IEC_PDF.pdf" not in response
    assert "BOE-326_Reglamento_electrotecnico_para_baja_tension_e_ITC.pdf" not in response


def test_list_indexed_sources_dispatches_to_azure_backend():
    with mock.patch.object(rag_service, "RAG_BACKEND", "azure_search"), mock.patch(
        "azure_rag_service.list_indexed_sources",
        return_value={"ops/demo.pdf": "hash"},
    ) as azure_list:
        result = rag_service.list_indexed_sources()

    azure_list.assert_called_once_with()
    assert result == {"ops/demo.pdf": "hash"}
