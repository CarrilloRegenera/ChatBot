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


def test_ops_related_business_listings_keep_business_route():
    questions = [
        "Cuales son las licitaciones mas recientes relacionadas con OPS",
        "Dame el top de proyectos o licitaciones vinculados a OPS con su cliente",
    ]

    for question in questions:
        assert classify_question(question)["route"] == "business_licitaciones", question


def test_ops_client_project_queries_route_to_business_produccion():
    questions = [
        "Que proyectos tenemos en curso para el cliente EOPSA",
        "Que obras en curso tenemos para el cliente OPSA",
    ]

    for question in questions:
        assert classify_question(question)["route"] == "business_produccion", question


def test_ops_standard_reference_is_not_misrouted_to_business_produccion():
    questions = [
        "Resumen de IEC 80005 para OPS y suministro electrico a buques",
        "Segun ISO 80005-1, que es la shore-side electricity en OPS?",
        "Segun la IEC 80005-2, como funciona la monitorizacion y control del OPS?",
        "Que recomienda OPS para pliegos de condiciones y programa de necesidades?",
        "Segun el estudio de viabilidad OPS, cual es la demanda energetica y la potencia instalada necesaria?",
        "Que exige AFIF para la comunicacion de interes y la conformidad de Estado miembro?",
        "Que incluye el anteproyecto OPS del puerto de Bilbao en la memoria de infraestructura electrica?",
    ]

    for question in questions:
        assert classify_question(question)["route"] == "knowledge", question


def test_mixed_business_and_technical_questions_are_flagged_for_split():
    questions = [
        "Tengo un proyecto con cliente, presupuesto y licitacion de OPS, que exige el RITE?",
        "Que cliente tiene EOPSA y que dice la IEC 80005-1 sobre HVSC",
    ]

    for question in questions:
        result = classify_question(question)
        assert result["route"] == "mixed_scope", question
        assert "mezcla negocio y documentacion tecnica" in result["message"].lower()


def test_documentary_manual_questions_are_not_misrouted_to_business_or_out_of_scope():
    questions = [
        "Si me llega el equipo a obra, que deberia comprobar en la recepcion y antes de moverlo?",
        "Entonces, para corregir esa situacion de baja carga, que recomienda el manual y cuanto deberian durar como maximo las pruebas semanales sin carga?",
        "En epoca fria, si el grupo es automatico, que condicion de temperatura pide el manual y que elementos preve para ayudar al arranque?",
        "Para cerrar, si el grupo pasa mucho tiempo parado, que rutina minima de mantenimiento recomienda el manual y que precaucion previa hay antes de intervenirlo?",
        "Segun la guia EOPSA, que es la shore-side electricity en OPS?",
        "Que es la guia EOPSA para una licitacion OPS completa y exitosa?",
    ]

    for question in questions:
        assert classify_question(question)["route"] == "knowledge", question


def test_document_inventory_questions_get_explicit_inventory_route():
    questions = [
        "Que documentos tenemos disponibles en reglamento tecnico?",
        "Cual es la estructura documental del chatbot tecnico?",
        "Que documentacion hay cargada en el chatbot?",
        "Que documentacion tecnica hay indexada ahora mismo?",
        "Quiero el bloque de OPS",
        "Quiero que me digas solo los documentos que tenemos de OPS",
        "Que documentos tenemos de RITE?",
        "Que documentos tenemos de baja tension?",
    ]

    for question in questions:
        assert classify_question(question)["route"] == "document_inventory", question


def test_document_inventory_token_matching_catches_rephrased_questions():
    questions = [
        "Que documentos tecnicos tenemos ahora mismo cargados y en que bloques principales se reparten?",
        "Que reglamentos hay indexados?",
        "Que documentacion tenemos cargada?",
        "Que normativa hay disponibles en el sistema?",
        "Como esta organizada la documentacion en bloques?",
        "Que documentos existen?",
    ]
    for question in questions:
        assert classify_question(question)["route"] == "document_inventory", question


def test_document_inventory_does_not_false_positive_on_normal_technical():
    questions = [
        "Que dice el documento sobre caida de tension?",
        "Donde hay informacion sobre la seccion del cable?",
        "Que tabla del reglamento aplica a secciones de cable?",
    ]
    for question in questions:
        assert classify_question(question)["route"] != "document_inventory", question
