import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from query_router import classify_question


def test_technical_questions_without_core_regulation_words_go_to_knowledge():
    questions = [
        "Cuando tengo que respetar una distancia de 3cm?",
        "Los tubos utilizados en acometidas tendran:",
        "El alimentador de una cerca electrica puede alimentarse:",
        "Cuando y por quien deben realizarse inspecciones periodicas en lineas de AT?",
        "Que criterios usa ISO 8528-5 para evaluar la respuesta transitoria de grupos electrogenos?",
    ]

    for question in questions:
        assert classify_question(question)["route"] == "knowledge"


def test_out_of_scope_still_rejected_when_not_technical():
    questions = [
        "que tiempo hace manana",
        "que temperatura hace en Madrid",
        "cuentame un chiste de gatos",
        "dame una receta de chocolate",
        "obtener el horoscopo de hoy",
    ]
    for question in questions:
        assert classify_question(question)["route"] == "out_of_scope", question
