import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rag_service
from routes.chat import _format_document_inventory_response


def test_format_document_inventory_response_groups_domains_and_counts():
    response = _format_document_inventory_response(
        {
            "ops/01_normativa_base/BS ISO IEC IEEE 80005-1_2012.pdf": "h1",
            "ops/03_checklists_operacion/EOPSA_Checklist_OPS_ES.pdf": "h2",
            "rite/RITE IT3.pdf": "h3",
            "baja_tension/BOE-326_Reglamento_electrotecnico_para_baja_tension_e_ITC.pdf": "h4",
        }
    )

    assert "La documentacion tecnica disponible en el chatbot esta organizada por bloques:" in response
    assert "- OPS: 2 documento(s)" in response
    assert "- RITE: 1 documento(s)" in response
    assert "- Baja tension: 1 documento(s)" in response
    assert "01_normativa_base/BS ISO IEC IEEE 80005-1_2012.pdf" in response
    assert "Total indexado actualmente: 4 documento(s)." in response


def test_format_document_inventory_response_handles_empty_inventory():
    response = _format_document_inventory_response({})
    assert "no veo documentos tecnicos indexados" in response.lower()


def test_list_indexed_sources_dispatches_to_azure_backend():
    with mock.patch.object(rag_service, "RAG_BACKEND", "azure_search"), mock.patch(
        "azure_rag_service.list_indexed_sources",
        return_value={"ops/demo.pdf": "hash"},
    ) as azure_list:
        result = rag_service.list_indexed_sources()

    azure_list.assert_called_once_with()
    assert result == {"ops/demo.pdf": "hash"}
