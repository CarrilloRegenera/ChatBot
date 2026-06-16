import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag_service import (
    _domain_phrase_queries,
    _expected_document_variants,
    _expected_domains,
)

_GOLDEN_PATH = Path(__file__).resolve().parent / "golden_questions.json"
_GOLDEN = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("gq", _GOLDEN, ids=[g["id"] for g in _GOLDEN])
def test_domain_detection(gq):
    detected = _expected_domains(gq["question"])
    for exp in gq["expected_domains"]:
        assert exp in detected, f"[{gq['id']}] dominio '{exp}' no detectado en: {detected}"


@pytest.mark.parametrize(
    "gq",
    [g for g in _GOLDEN if g["expected_variants"]],
    ids=[g["id"] for g in _GOLDEN if g["expected_variants"]],
)
def test_variant_detection(gq):
    variants = _expected_document_variants(gq["question"], gq["expected_domains"])
    for exp in gq["expected_variants"]:
        assert exp in variants, f"[{gq['id']}] variante '{exp}' no detectada en: {variants}"


@pytest.mark.parametrize(
    "gq",
    [g for g in _GOLDEN if g["expected_phrase_queries"]],
    ids=[g["id"] for g in _GOLDEN if g["expected_phrase_queries"]],
)
def test_phrase_queries(gq):
    phrases = _domain_phrase_queries(gq["question"])
    for exp in gq["expected_phrase_queries"]:
        assert any(exp.lower() in p.lower() for p in phrases), (
            f"[{gq['id']}] phrase '{exp}' no encontrada en: {phrases}"
        )
