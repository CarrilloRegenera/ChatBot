import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chat_technical_response_service import (
    apply_known_technical_answer_overrides,
    augment_retrieval_question,
)


def test_augment_retrieval_question_adds_rite_context_for_evaporadores():
    augmented = augment_retrieval_question("y cual es la periodicidad de limpieza de los evaporadores")
    assert "Tabla 3.1" in augmented
    assert "RITE" in augmented
    assert "IT 3.3" in augmented


def test_apply_known_technical_answer_overrides_recovers_rebt_definition():
    response, confidence = apply_known_technical_answer_overrides(
        "Que es el REBT?",
        "No tengo informacion suficiente para responder con base en reglamentos.",
        0.18,
    )
    assert "Reglamento Electrotecnico para Baja Tension" in response
    assert confidence >= 0.9


def test_apply_known_technical_answer_overrides_recovers_ralt_protecciones():
    response, confidence = apply_known_technical_answer_overrides(
        "Que dice el RALT sobre protecciones?",
        "No hay información suficiente en el contexto recuperado.",
        0.4,
    )
    assert "máxima y mínima frecuencia" in response
    assert confidence >= 0.8
