import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag_service import _domain_phrase_queries, _expected_domains, _source_domain_key


def test_alta_tension_source_classification():
    assert _source_domain_key("alta_tension/BOE-A-2014-A16436-16554_ITC-LAT.pdf") == "alta_tension"
    assert _source_domain_key("docs/ITC-LAT-09.pdf") == "alta_tension"


def test_alta_tension_query_detection():
    domains = _expected_domains("Segun la ITC LAT 09 cual es la distancia minima de seguridad")
    assert "alta_tension" in domains


def test_alta_tension_phrase_queries_for_people_protection():
    phrases = _domain_phrase_queries("que medidas de proteccion de las personas hay frente a contactos")
    assert "contactos directos" in phrases
    assert "contactos indirectos" in phrases
    assert "puesta a tierra" in phrases
