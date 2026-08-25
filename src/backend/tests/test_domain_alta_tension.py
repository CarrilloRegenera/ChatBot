import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag_service import (
    _domain_phrase_queries,
    _expected_domains,
    _source_domain_key,
    search_documents_detailed,
)


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


def test_itc_lat_and_rat_trigger_forced_retrieval():
    """El filtro que fuerza la recuperacion por codigo ITC solo cubria itc-bt-XX;
    itc-lat-XX e itc-rat-XX quedaban fuera aunque la pregunta los mencionara
    explicitamente (ver rag_service.py, target_itc_refs)."""
    _, _, stats_lat = search_documents_detailed("Que establece la ITC-LAT-07 sobre distancias de seguridad")
    assert "itc-lat-07" in stats_lat["target_itc_refs"]

    _, _, stats_rat = search_documents_detailed("Que dice la ITC-RAT-13 sobre puesta a tierra")
    assert "itc-rat-13" in stats_rat["target_itc_refs"]

    _, _, stats_bt = search_documents_detailed("Que establece la ITC-BT-25 sobre instalaciones interiores")
    assert "itc-bt-25" in stats_bt["target_itc_refs"]
