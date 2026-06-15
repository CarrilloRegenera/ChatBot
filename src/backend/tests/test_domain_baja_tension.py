import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag_service import _domain_phrase_queries, _expected_domains, _extract_exact_refs, _source_domain_key


def test_baja_tension_source_classification():
    assert _source_domain_key("baja_tension/BOE-326_REBT_ITC-BT-25.pdf") == "baja_tension"


def test_baja_tension_query_detection_and_exact_ref():
    domains = _expected_domains("Que exige la ITC BT 25 para electrificacion basica")
    assert "baja_tension" in domains
    assert "itc-bt-25" in _extract_exact_refs("Que exige la ITC BT 25 para electrificacion basica")


def test_baja_tension_phrase_queries_for_contact_voltage():
    phrases = _domain_phrase_queries("maxima tension de contacto en puestas a tierra segun REBT")
    assert "ITC-BT-18" in phrases
    assert "puesta a tierra" in phrases
