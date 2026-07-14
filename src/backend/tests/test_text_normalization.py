import os
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("OPENAI_API_KEY", "test-key")

import business_query_service as business  # noqa: E402
import query_router  # noqa: E402
from text_normalization import normalize_for_matching  # noqa: E402


def test_normalize_for_matching_removes_accents_and_collapses_spaces():
    result = normalize_for_matching("  Instalación   TÉCNICA  ", r"[^\w\s/-]")
    assert result == "instalacion tecnica"


def test_normalize_for_matching_replaces_punctuation_with_spaces():
    result = normalize_for_matching("Hola, mundo! (OPS)", r"[^\w\s/-]")
    assert result == "hola mundo ops"


def test_query_router_normalize_handles_inverted_punctuation():
    result = query_router._normalize("¿Qué documentos tenemos de OPS?")
    assert result == "que documentos tenemos de ops"


def test_business_normalize_applies_known_typo_replacements():
    result = business._normalize("Cual es el imorte medio de las liictaciones")
    assert "importe" in result
    assert "licitaciones" in result


def test_business_normalize_preserves_business_tokens_with_slashes():
    result = business._normalize("Rentabilidad prevista 2026 proyecto obra/subfase")
    assert "obra/subfase" in result
