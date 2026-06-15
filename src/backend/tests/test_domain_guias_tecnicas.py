import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag_service import _domain_phrase_queries, _expected_domains, _source_domain_key


def test_guias_tecnicas_source_classification():
    assert _source_domain_key("guias_tecnicas/Guia_BT_40.pdf") == "guias_tecnicas"
    assert _source_domain_key("iluminacion/UNE-12464-1_alumbrado.pdf") == "guias_tecnicas"


def test_guias_tecnicas_query_detection():
    domains = _expected_domains("Que condiciones de conexion a red aplica la guia BT 40 para instalaciones generadoras")
    assert "guias_tecnicas" in domains


def test_guias_tecnicas_phrase_queries_for_generators():
    phrases = _domain_phrase_queries("como legalizar un generador conectado a red en baja tension")
    assert "instalaciones generadoras" in phrases
    assert "condiciones de conexion a red" in phrases
