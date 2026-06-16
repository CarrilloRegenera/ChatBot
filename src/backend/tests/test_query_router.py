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
        "Que es el OPS en puertos y como funciona el shore power?",
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


def test_business_numeric_produccion_code_routes_to_business_produccion():
    questions = [
        "Que cliente tiene el proyecto 26001",
        "Dime el estado del 26001",
        "Cual es el importe contratado de 24036",
    ]
    for question in questions:
        assert classify_question(question)["route"] == "business_produccion", question


def test_business_estudio_reference_routes_to_business_licitaciones():
    questions = [
        "Que produccion 2027 tiene EST-188-2025",
        "Que cliente tiene OPS-240-2026",
    ]
    for question in questions:
        assert classify_question(question)["route"] == "business_licitaciones", question


def test_ops_standard_reference_is_not_misrouted_to_business_produccion():
    questions = [
        "Resumen de IEC 80005 para OPS y suministro electrico a buques",
        "Segun ISO 80005-1, que es la shore-side electricity en OPS?",
    ]

    for question in questions:
        assert classify_question(question)["route"] == "knowledge", question


def test_documentary_manual_questions_are_not_misrouted_to_business_or_out_of_scope():
    questions = [
        "Si me llega el equipo a obra, que deberia comprobar en la recepcion y antes de moverlo?",
        "Entonces, para corregir esa situacion de baja carga, que recomienda el manual y cuanto deberian durar como maximo las pruebas semanales sin carga?",
        "En epoca fria, si el grupo es automatico, que condicion de temperatura pide el manual y que elementos preve para ayudar al arranque?",
        "Para cerrar, si el grupo pasa mucho tiempo parado, que rutina minima de mantenimiento recomienda el manual y que precaucion previa hay antes de intervenirlo?",
        "Segun la guia EOPSA, que es la shore-side electricity en OPS?",
    ]

    for question in questions:
        assert classify_question(question)["route"] == "knowledge", question
