"""Regresiones para reparacion de omisiones normativas en sintesis RAG."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_service import _repair_normative_validity_omission  # noqa: E402


def test_normative_validity_repair_adds_public_tt_rule_when_context_supports_it():
    question = "Que sistemas de puesta a tierra son validos segun el REBT para un sistema de carga de vehiculo electrico?"
    answer = (
        "Segun la ITC-BT-52, en los casos especiales en los que la instalacion este "
        "alimentada por un esquema TN, solamente se utilizara en la forma TN-S."
    )
    context = (
        "[REBT pag. 96] El esquema de distribucion para instalaciones receptoras "
        "alimentadas directamente de una red de distribucion publica de baja tension "
        "es el esquema TT.\n\n"
        "[REBT pag. 289] En los casos especiales en los que la instalacion este "
        "alimentada por un esquema TN, solamente se utilizara en la forma TN-S."
    )

    repaired = _repair_normative_validity_omission(question, answer, context)

    assert repaired is not None
    assert "esquema es TT" in repaired
    assert "TN-S" in repaired


def test_normative_validity_repair_ignores_unrelated_normative_questions():
    question = "Que periodicidad tiene la limpieza de condensadores segun la tabla 3.1 del RITE?"
    answer = "Segun el contexto recuperado, la periodicidad es mensual."
    context = (
        "El esquema de distribucion para instalaciones receptoras alimentadas directamente "
        "de una red de distribucion publica de baja tension es el esquema TT."
    )

    assert _repair_normative_validity_omission(question, answer, context) is None
