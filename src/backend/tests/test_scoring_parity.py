import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from azure_rag_service import (
    _apply_hint_boosts,
    _azure_document_variant,
    _domain_boost,
    _DOMAIN_MATCH_BOOST,
    _DOMAIN_MISMATCH_PENALTY,
    _LAYER_BOOSTS,
)


def test_domain_boost_match():
    item = {"domain": "rite", "document_layer": "normativa_oficial"}
    boost = _domain_boost(item, {"rite"})
    assert boost == _DOMAIN_MATCH_BOOST + _LAYER_BOOSTS["normativa_oficial"]


def test_domain_boost_mismatch():
    item = {"domain": "fotovoltaica_om", "document_layer": "manual_fabricante"}
    boost = _domain_boost(item, {"rite"})
    assert boost == _DOMAIN_MISMATCH_PENALTY + _LAYER_BOOSTS["manual_fabricante"]


def test_domain_boost_no_expected():
    item = {"domain": "rite", "document_layer": "normativa_oficial"}
    boost = _domain_boost(item, set())
    assert boost == _LAYER_BOOSTS["normativa_oficial"]


def test_domain_boost_no_layer():
    item = {"domain": "rite"}
    boost = _domain_boost(item, {"rite"})
    assert boost == _DOMAIN_MATCH_BOOST


def test_layer_boost_values_match_chroma():
    assert _LAYER_BOOSTS["normativa_oficial"] == 8.0
    assert _LAYER_BOOSTS["guia_oficial"] == 4.0
    assert _LAYER_BOOSTS["manual_fabricante"] == 0.0
    assert _LAYER_BOOSTS["pendiente"] == -5.0


def test_normativa_beats_manual_same_domain():
    normativa = {"domain": "baja_tension", "document_layer": "normativa_oficial"}
    manual = {"domain": "baja_tension", "document_layer": "manual_fabricante"}
    expected = {"baja_tension"}
    assert _domain_boost(normativa, expected) > _domain_boost(manual, expected)


def test_normativa_foreign_vs_manual_local():
    normativa_foreign = {"domain": "rite", "document_layer": "normativa_oficial"}
    manual_local = {"domain": "baja_tension", "document_layer": "manual_fabricante"}
    expected = {"baja_tension"}
    boost_normativa = _domain_boost(normativa_foreign, expected)
    boost_manual = _domain_boost(manual_local, expected)
    assert boost_manual > boost_normativa


def test_hint_boosts_variant_match():
    candidates = [
        {"document_variant": "it3", "article_refs": "", "it_section_refs": ""},
        {"document_variant": "2021", "article_refs": "", "it_section_refs": ""},
    ]
    _apply_hint_boosts(candidates, ["it3"], None, None)
    assert candidates[0]["_hint_boost"] > 0
    assert candidates[1]["_hint_boost"] == 0


def test_hint_boosts_article_ref():
    candidates = [
        {"document_variant": "", "article_refs": "articulo 12, articulo 15", "it_section_refs": ""},
        {"document_variant": "", "article_refs": "articulo 30", "it_section_refs": ""},
    ]
    _apply_hint_boosts(candidates, [], ["articulo 12"], None)
    assert candidates[0]["_hint_boost"] > 0
    assert candidates[1]["_hint_boost"] == 0


def test_hint_boosts_it_section_ref():
    candidates = [
        {"document_variant": "", "article_refs": "", "it_section_refs": "it 3.3"},
        {"document_variant": "", "article_refs": "", "it_section_refs": "it 1.2"},
    ]
    _apply_hint_boosts(candidates, [], None, ["it 3.3"])
    assert candidates[0]["_hint_boost"] > 0
    assert candidates[1]["_hint_boost"] == 0


def test_hint_boosts_combined():
    candidates = [
        {"document_variant": "it3", "article_refs": "articulo 12", "it_section_refs": "it 3.3"},
    ]
    _apply_hint_boosts(candidates, ["it3"], ["articulo 12"], ["it 3.3"])
    from azure_rag_service import _VARIANT_BOOST, _ARTICLE_REF_BOOST, _IT_SECTION_REF_BOOST
    expected = _VARIANT_BOOST + _ARTICLE_REF_BOOST + _IT_SECTION_REF_BOOST
    assert abs(candidates[0]["_hint_boost"] - expected) < 0.001


def test_azure_document_variant_falls_back_to_source_path():
    item = {
        "document_variant": "",
        "source_path": "ops/07_estudios_viabilidad/Est. Terminal Cruceros - Malaga.pdf",
    }
    assert _azure_document_variant(item) == "estudio_viabilidad_malaga"
