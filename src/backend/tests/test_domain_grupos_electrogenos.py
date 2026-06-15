import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag_service import _domain_phrase_queries, _expected_domains, _source_domain_key, detect_hint_domains


def test_grupos_electrogenos_source_classification():
    assert _source_domain_key("grupos_electrogenos/ISO-8528-1.pdf") == "grupos_electrogenos"


def test_grupos_electrogenos_query_detection():
    domains = _expected_domains("Segun el manual de grupos electrogenos que hacer con el remolque y el enganche")
    assert "grupos_electrogenos" in domains


def test_grupos_electrogenos_followup_hint_detection():
    hints = detect_hint_domains("seguimos con el manual de grupos electrogenos y el remolque del grupo automatico")
    assert "grupos_electrogenos" in hints


def test_grupos_electrogenos_phrase_queries_for_iso_terms():
    phrases = _domain_phrase_queries("what is the load pick-up readiness time in ISO 8528")
    assert "3.19 load pick-up readiness time" in phrases
