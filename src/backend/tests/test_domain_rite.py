import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag_service import (
    _decode_chunk_corruption,
    _domain_phrase_queries,
    _expected_document_variants,
    _expected_domains,
    detect_hint_domains,
)


def test_rite_query_detection():
    domains = _expected_domains("Segun el RITE cual es la periodicidad de limpieza de los evaporadores")
    assert "rite" in domains


def test_rite_variant_detection_for_it3():
    variants = _expected_document_variants("tabla 3.1 del RITE", ["rite"])
    assert "it3" in variants


def test_rite_phrase_queries_for_table_31():
    phrases = _domain_phrase_queries("limpieza de condensadores segun tabla 3.1 del RITE")
    assert "Tabla 3.1" in phrases
    assert "IT 3.3" in phrases


def test_rite_followup_hints_and_decoder():
    assert "rite" in detect_hint_domains("y cual es la periodicidad de limpieza de los evaporadores")
    decoded = _decode_chunk_corruption("-JNQJF[BEFMPTDPOEFOTBEPSFT U U", "rite/doc.pdf")
    assert "limpieza" in decoded.lower()
