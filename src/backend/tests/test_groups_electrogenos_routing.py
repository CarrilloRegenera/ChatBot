import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from business_query_service import detect_business_route
from query_router import classify_question
from rag_service import detect_hint_domains


def test_manual_questions_stay_in_knowledge_route():
    questions = [
        "Si me llega el equipo a obra, que deberia comprobar en la recepcion y antes de moverlo?",
        "Entonces, para corregir esa situacion de baja carga, que recomienda el manual y cuanto deberian durar como maximo las pruebas semanales sin carga?",
        "En epoca fria, si el grupo es automatico, que condicion de temperatura pide el manual y que elementos preve para ayudar al arranque?",
        "Para cerrar, si el grupo pasa mucho tiempo parado, que rutina minima de mantenimiento recomienda el manual y que precaucion previa hay antes de intervenirlo?",
    ]

    for question in questions:
        assert classify_question(question)["route"] == "knowledge", question


def test_documentary_manual_questions_do_not_trigger_business_route():
    questions = [
        "Si me llega el equipo a obra, que deberia comprobar en la recepcion y antes de moverlo?",
        "Entonces, para corregir esa situacion de baja carga, que recomienda el manual y cuanto deberian durar como maximo las pruebas semanales sin carga?",
        "En epoca fria, si el grupo es automatico, que condicion de temperatura pide el manual y que elementos preve para ayudar al arranque?",
    ]

    for question in questions:
        assert detect_business_route(question) is None, question


def test_generator_followups_keep_domain_hint():
    hints = detect_hint_domains(
        "seguimos con el manual de grupos electrogenos y el remolque del grupo automatico"
    )
    assert "grupos_electrogenos" in hints
