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
from rag_service import (
    _document_layer_boost,
    _extract_circuit_definitions,
    _extract_requested_abbreviation_definitions,
    _clean_context_document,
    _matches_structural_focus,
    _query_support_metrics,
)
from ai_service import _retrieval_quality, postprocess_answer


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


def test_procedure_intent_prioritizes_practical_documents():
    assert _document_layer_boost("manual_fabricante", procedure_intent=True) > _document_layer_boost(
        "normativa_oficial",
        procedure_intent=True,
    )


def test_normative_intent_prioritizes_official_regulation():
    assert _document_layer_boost("normativa_oficial", normative_intent=True) > _document_layer_boost(
        "manual_fabricante",
        normative_intent=True,
    )


def test_structural_focus_accepts_exact_references():
    metadata = {
        "section": "ITC-BT-25",
        "exact_refs": "itc-bt-25",
        "itc_refs": "itc-bt-25",
    }
    assert _matches_structural_focus(
        metadata,
        "Circuitos interiores de viviendas.",
        exact_refs=["itc-bt-25"],
    )


def test_query_support_metrics_detect_unrelated_context():
    selected = [
        (1.0, "1", "Reglamento de instalaciones electricas.", {"source": "rebt.pdf"}),
        (0.9, "2", "Mantenimiento de calderas.", {"source": "rite.pdf"}),
    ]
    supported_ratio, max_coverage = _query_support_metrics(
        {"neumaticos", "camion", "fabricante", "presion"},
        selected,
    )
    assert supported_ratio == 0.0
    assert max_coverage < 0.25


def test_retrieval_quality_marks_unsupported_context_as_poor():
    assert _retrieval_quality(
        {
            "selected_count": 6,
            "source_diversity": 3,
            "expected_domains": [],
            "domain_match_ratio": 1.0,
            "supported_chunk_ratio": 0.0,
            "max_query_term_coverage": 0.2,
        }
    ) == "poor"


def test_requested_abbreviation_definitions_are_consolidated_from_chunks():
    selected = [
        (
            1.0,
            "1",
            "columna_3: t (una vez por temporada (AÑO)); columna_4: m (una vez al MES).",
            {"source": "manual.pdf"},
        )
    ]
    definitions = _extract_requested_abbreviation_definitions(
        "Que significan las letras t y m de la tabla?",
        selected,
    )
    assert definitions["t"].startswith("una vez por temporada")
    assert definitions["m"].startswith("una vez al MES")


def test_circuit_definitions_are_consolidated_from_split_chunks():
    selected = [
        (
            1.0,
            "1",
            "C1 circuito de distribucion interna, destinado a alimentar los puntos de iluminacion. "
            "C2 circuito de distribucion interna, destinado a tomas de corriente de uso general y frigorifico.",
            {"source": "rebt.pdf"},
        )
    ]
    definitions = _extract_circuit_definitions(selected)
    assert definitions["C1"].startswith("circuito de distribucion interna")
    assert "frigorifico" in definitions["C2"]


def test_circuit_definitions_are_consolidated_from_flattened_table():
    selected = [
        (
            1.0,
            "1",
            "C1 Iluminacion 200 0,75 C2 Tomas de uso general 3.450 0,2 "
            "C3 Cocina y horno 5.400 0,5 "
            "C4 Lavadora, lavavajillas y termo electrico 3.450 0,66 "
            "C5 Bano, cuarto de cocina 3.450 0,4",
            {"source": "rebt.pdf"},
        )
    ]
    assert _extract_circuit_definitions(selected) == {
        "C1": "Iluminacion",
        "C2": "Tomas de uso general",
        "C3": "Cocina y horno",
        "C4": "Lavadora, lavavajillas y termo electrico",
        "C5": "Bano, cuarto de cocina",
    }


def test_clean_context_document_removes_obsolete_rite_u_legend():
    text = (
        "operacion: Limpieza de evaporadores; < 70 kW: t (una vez por temporada).\n"
        "Leyenda Tabla 3.1 RITE: en el texto extraido, U corresponde a una vez "
        "por temporada (año). Si una fila aparece como U U, la periodicidad es "
        "una vez por temporada para ambas columnas de potencia."
    )
    cleaned = _clean_context_document(text, "rite/RITE IT3.pdf")
    assert "U corresponde" not in cleaned
    assert "< 70 kW: t" in cleaned


def test_postprocess_preserves_last_numbered_item_without_period():
    answer = "1. t: una vez por temporada.\n2. m: una vez al mes"
    assert postprocess_answer(answer, "que significan t y m") == (
        "1. t: una vez por temporada.\n2. m: una vez al mes."
    )


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


# ---------------------------------------------------------------------------
# Source mention boost — _source_mention_score
# ---------------------------------------------------------------------------

from rag_service import _source_mention_score, _tokenize, STOPWORDS, SOURCE_MENTION_BOOST


def _q_tokens(question: str) -> set:
    return {t for t in _tokenize(question.lower()) if t not in STOPWORDS and len(t) >= 4}


def test_source_mention_boost_matches_filename_token():
    qt = _q_tokens("errores frecuentes al dimensionar cables")
    assert _source_mention_score(qt, "baja_tension/errores_frecuentes_2023_v2.pdf") == SOURCE_MENTION_BOOST


def test_source_mention_no_match_on_unrelated_source():
    qt = _q_tokens("errores frecuentes al dimensionar cables")
    assert _source_mention_score(qt, "rite/RITE IT3.pdf") == 0


def test_source_mention_ignores_noise_tokens():
    qt = _q_tokens("manual de baja tension")
    assert _source_mention_score(qt, "baja_tension/errores_frecuentes_2023_v2.pdf") == 0


def test_source_mention_single_short_token_ignored():
    qt = _q_tokens("que es rite")
    assert _source_mention_score(qt, "rite/RITE IT3.pdf") == 0


def test_source_mention_works_for_future_pdfs():
    qt = _q_tokens("prysmian guia de instalacion")
    assert _source_mention_score(qt, "baja_tension/prysmian_guia_instalacion_2025.pdf") == SOURCE_MENTION_BOOST


def test_source_mention_electrotecnico():
    qt = _q_tokens("segun el reglamento electrotecnico que dice la itc-bt-25")
    assert _source_mention_score(qt, "baja_tension/BOE-326_Reglamento_electrotecnico_para_baja_tension_e_ITC.pdf") == SOURCE_MENTION_BOOST


# --- domain_exclusion_penalty tests ---

from rag_service import _domain_exclusion_penalty


def test_domain_exclusion_ops_penalizes_alta_tension_when_anchored():
    penalty = _domain_exclusion_penalty(["ops"], "alta_tension", "en ops que norma base manda sobre conexion buque tierra")
    assert penalty < 0


def test_domain_exclusion_no_penalty_without_condition_token():
    penalty = _domain_exclusion_penalty(["ops"], "alta_tension", "que dice la norma sobre lineas aereas")
    assert penalty == 0


def test_domain_exclusion_no_penalty_for_unrelated_pair():
    penalty = _domain_exclusion_penalty(["baja_tension"], "alta_tension", "que dice el rebt sobre buques")
    assert penalty == 0


def test_domain_exclusion_reverse_direction():
    penalty = _domain_exclusion_penalty(["alta_tension"], "ops", "segun itc-lat sobre lineas aereas de alta tension")
    assert penalty < 0
