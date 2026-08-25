import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import STOPWORDS
from rag_service import (
    TARGET_ITC_REF_PATTERN,
    _build_query_profile,
    _clean_question,
    _domain_phrase_queries,
    _expected_domains,
    _source_domain_key,
    _tokenize,
)


def _target_itc_refs(question: str) -> set[str]:
    """Reproduce el filtro de target_itc_refs sin tocar la coleccion de Chroma
    (search_documents_detailed depende de haber indexado documentos reales,
    algo que no existe en un checkout limpio: chroma_db/ y data/documentos/
    estan en .gitignore)."""
    clean = _clean_question(question)
    keywords = {t for t in _tokenize(clean) if t not in STOPWORDS and len(t) >= 5}
    profile = _build_query_profile(clean, keywords)
    return {ref for ref in profile["exact_refs"] if TARGET_ITC_REF_PATTERN.fullmatch(ref)}


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
    assert "itc-lat-07" in _target_itc_refs("Que establece la ITC-LAT-07 sobre distancias de seguridad")
    assert "itc-rat-13" in _target_itc_refs("Que dice la ITC-RAT-13 sobre puesta a tierra")
    assert "itc-bt-25" in _target_itc_refs("Que establece la ITC-BT-25 sobre instalaciones interiores")
