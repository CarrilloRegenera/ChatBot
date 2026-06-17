"""Regresión para clasificación de dominio.

Asegura que renombrar/mover PDFs típicos del corpus normativo no produce
deriva de dominio (ej. nombres con 'lat' como sufijo en 'plataforma' no
deben caer en alta_tension).

Ejecutar:
    cd src/backend && python -m pytest tests/test_domain_detection.py -v
"""
import sys
from pathlib import Path

# Permite importar rag_service cuando se ejecuta desde src/backend.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag_service import (  # noqa: E402
    _auto_technical_terms,
    _decode_chunk_corruption,
    _chunk_profile_metadata,
    _document_profile_metadata,
    _document_variant_from_source,
    _domain_phrase_queries,
    _EF_VERSION,
    _expected_document_variants,
    _expected_domains,
    _extract_article_refs,
    _extract_exact_refs,
    _extract_it_section_refs,
    _is_normative_intent_query,
    _normative_application_hit_count,
    _rag_index_version_tag,
    _SHIFT31_SOURCE_TOKENS,
    _source_domain_key,
    _source_taxonomy,
    _technical_equivalent_phrases,
    detect_hint_article_refs,
    detect_hint_document_variants,
    detect_hint_domains,
    detect_hint_it_section_refs,
)


CASES = [
    # (source_path, expected_domain)
    # Alta tensión
    ("alta_tension/BOE-A-2014-A16436-16554_ITC-LAT.pdf", "alta_tension"),
    ("docs/ITC-LAT-09.pdf", "alta_tension"),
    # RITE
    ("rite/BOE-A-2007-A35931-35984_RITE.pdf", "rite"),
    ("instalaciones_termicas/RITE_consolidado.pdf", "rite"),
    # Baja tensión
    ("baja_tension/BOE-326_REBT_ITC-BT-25.pdf", "baja_tension"),
    ("rebt/ITC-BT-40.pdf", "baja_tension"),
    # Guías técnicas (BT-40 es guía, no normativa, y debe ganar el override de guía)
    ("guias_tecnicas/Guia_BT_40.pdf", "guias_tecnicas"),
    ("iluminacion/UNE-12464-1_alumbrado.pdf", "guias_tecnicas"),
    # Fotovoltaica O&M
    ("fotovoltaica_om/Manual_OM_FV.pdf", "fotovoltaica_om"),
    ("operacion_mantenimiento/FV_plant_OM.pdf", "fotovoltaica_om"),
    # Grupos electrógenos
    ("grupos_electrogenos/ISO-8528-1.pdf", "grupos_electrogenos"),
    # OPS
    ("ops/EOPSA_Guia_OPS_ES_Completa.pdf", "ops"),
    ("ops/EMSA_Guidance_on_SSE_PART1.pdf", "ops"),
    ("ops/06_financiacion_afif/Procedimiento subvención AFIF.pdf", "ops"),
    ("ops/07_estudios_viabilidad/Est. Terminal Cruceros - Malaga.pdf", "ops"),
    ("ops/08_anteproyectos_referencia/ANTEPROYECTO-OPS-PUERTO-BILBAO.pdf", "ops"),
    # No-deriva: filenames con substring 'lat' que NO son alta tensión
    ("pendiente_ocr/Plataforma_inspeccion.pdf", "pendiente_ocr"),
    ("general/regulator_de_tension.pdf", "general"),
    # Default
    ("general/otra_norma_no_categorizada.pdf", "general"),
]


def test_domain_classification():
    failures = []
    for source, expected in CASES:
        actual = _source_domain_key(source)
        if actual != expected:
            failures.append(f"  {source!r}: esperado={expected!r}, obtenido={actual!r}")
    assert not failures, (
        f"\nClasificación de dominio incorrecta en {len(failures)} caso(s):\n"
        + "\n".join(failures)
    )

TAXONOMY_CASES = [
    (
        "alta_tension/A16436-16554.pdf",
        {"department": "ingenieria", "document_type": "reglamento", "document_layer": "normativa_oficial", "confidentiality": "internal"},
    ),
    (
        "baja_tension/BOE-326_Reglamento_electrotecnico_para_baja_tension_e_ITC.pdf",
        {"department": "ingenieria", "document_type": "reglamento", "document_layer": "normativa_oficial", "confidentiality": "internal"},
    ),
    (
        "rite/A35931-35984.pdf",
        {"department": "ingenieria", "document_type": "reglamento", "document_layer": "normativa_oficial", "confidentiality": "internal"},
    ),
    (
        "guias_tecnicas/Guia_bt_40_sep13R1 (1).pdf",
        {"department": "ingenieria", "document_type": "guia_tecnica", "document_layer": "guia_oficial", "confidentiality": "internal"},
    ),
    (
        "fotovoltaica_om/Manual-de-Manteminiento.pdf",
        {"department": "mantenimiento", "document_type": "manual", "document_layer": "manual_fabricante", "confidentiality": "internal"},
    ),
    (
        "grupos_electrogenos/ISO-8528-5-2018.pdf",
        {"department": "mantenimiento", "document_type": "norma", "document_layer": "normativa_oficial", "confidentiality": "internal"},
    ),
    (
        "ops/EOPSA_Guia_OPS_ES_Completa.pdf",
        {"department": "ingenieria", "document_type": "guia_tecnica", "document_layer": "guia_oficial", "confidentiality": "internal"},
    ),
]


def test_source_taxonomy_from_domain_config():
    for source, expected in TAXONOMY_CASES:
        assert _source_taxonomy(source) == expected


def test_source_taxonomy_allows_explicit_metadata_override():
    metadata = {
        "domain": "fotovoltaica_om",
        "department": "operaciones",
        "document_type": "procedimiento",
        "confidentiality": "restricted",
    }
    assert _source_taxonomy("fotovoltaica_om/manual.pdf", metadata) == {
        "department": "operaciones",
        "document_type": "procedimiento",
        "document_layer": "manual_fabricante",
        "confidentiality": "restricted",
    }


def test_expected_domains_uses_configured_reference_patterns():
    """Referencias normativas deben enrutar por domains.json, no por ifs del codigo."""
    assert "baja_tension" in _expected_domains("Que es la ITC BT 40?")
    assert "guias_tecnicas" in _expected_domains("Que es la ITC BT 40?")
    assert "alta_tension" in _expected_domains("Resumen de la ITC LAT 09")
    assert "guias_tecnicas" in _expected_domains("Que exige UNE 12464-1?")
    assert "ops" in _expected_domains("Que es OPS y como funciona la shore-side electricity?")
    assert "ops" in _expected_domains("Resumen de IEC 80005 para OPS")


def test_ops_phrase_queries_cover_eopsa_checklists():
    phrases = _domain_phrase_queries(
        "Segun la checklist OPS de EOPSA, que se debe revisar antes de conectar?"
    )
    assert "EOPSA" in phrases
    assert "Lista de Verificacion OPS" in phrases
    assert "Lista de Verificacion para una Licitacion OPS Completa y Exitosa" in phrases
    assert "ORIGEN DE LA FUENTE DE ALIMENTACION" in phrases
    assert "DATOS ELECTRICOS DE ENTRADA DE LA RED" in phrases


def test_ops_document_variants_distinguish_normative_base_and_checklists():
    assert _document_variant_from_source("ops/01_normativa_base/BS ISO IEC IEEE 80005-1_2012.pdf") == "normativa_base"
    assert _document_variant_from_source("ops/03_checklists_operacion/EOPSA_Checklist_OPS_ES.pdf") == "eopsa_checklist"
    assert _document_variant_from_source("ops/03_checklists_operacion/EOPSA_Guia_OPS_ES_Completa.pdf") == "eopsa_guia_completa"
    assert _expected_document_variants("Que es OPS segun la IEC 80005-1?", ["ops"]) == ["normativa_base"]
    assert _expected_document_variants(
        "Segun la checklist OPS de EOPSA, que se debe revisar antes de conectar?",
        ["ops"],
    ) == ["eopsa_checklist"]
    assert _expected_document_variants(
        "Que es la guia EOPSA para una licitacion OPS completa y exitosa?",
        ["ops"],
    ) == ["eopsa_guia_completa"]


def test_ops_document_variants_cover_monitoring_summary_and_project_planning():
    assert _document_variant_from_source("ops/01_normativa_base/iecieee80005-2_2016.pdf") == "monitorizacion_control"
    assert _document_variant_from_source(
        "ops/02_guias_implantacion/EMSA Guidance on SSE_PART1.pdf"
    ) == "guia_implantacion_part1"
    assert _document_variant_from_source(
        "ops/02_guias_implantacion/EMSA Guidance on SSE_PART2_Version 2.pdf"
    ) == "guia_implantacion_part2"
    assert _document_variant_from_source(
        "ops/04_resumen_sectorial/On_shore_power_supply_summary-surveys_final.pdf"
    ) == "resumen_sectorial"
    assert _document_variant_from_source(
        "ops/05_planificacion_proyecto/OPS - Planificacion y Explotacion.pdf"
    ) == "planificacion_explotacion"
    assert _document_variant_from_source(
        "ops/05_planificacion_proyecto/OPS - Redaccion de Proyectos - Presentacion.pdf"
    ) == "redaccion_proyectos_presentacion"
    assert _document_variant_from_source(
        "ops/05_planificacion_proyecto/OPS - Redaccion de Proyectos - Recomendaciones.pdf"
    ) == "redaccion_proyectos_recomendaciones"
    variants_80005_2 = _expected_document_variants(
        "Segun la IEC 80005-2, que datos se usan para monitorizacion y control?",
        ["ops"],
    )
    assert "monitorizacion_control" in variants_80005_2
    assert _expected_document_variants(
        "Que dice la guia EMSA Part 1 sobre calidad de energia o compatibilidad red-buque?",
        ["ops"],
    ) == ["guia_implantacion_part1"]
    assert _expected_document_variants(
        "Que cubre la guia EMSA Part 2 sobre SSE?",
        ["ops"],
    ) == ["guia_implantacion_part2"]
    assert _expected_document_variants(
        "Que dice el survey de WPCAP sobre conexiones OPS exitosas?",
        ["ops"],
    ) == ["resumen_sectorial"]
    assert _expected_document_variants(
        "Que trata el documento de planificacion y explotacion de instalaciones OPS?",
        ["ops"],
    ) == ["planificacion_explotacion"]
    assert _expected_document_variants(
        "Que enfoque da la presentacion sobre la metodologia o la revision de proyectos OPS?",
        ["ops"],
    ) == ["redaccion_proyectos_presentacion"]
    assert _expected_document_variants(
        "Que recomienda OPS para pliegos de condiciones y programa de necesidades?",
        ["ops"],
    ) == ["redaccion_proyectos_recomendaciones"]


def test_ops_document_variants_cover_afif_viability_and_anteproject():
    assert _document_variant_from_source(
        "ops/06_financiacion_afif/guia_solicitantes_cef-t-afif-2024_segundo_corte_1.0.pdf"
    ) == "afif_guia_solicitantes"
    assert _document_variant_from_source(
        "ops/06_financiacion_afif/Procedimiento subvenci?n AFIF.pdf"
    ) == "afif_procedimiento_subvencion"
    assert _document_variant_from_source(
        "ops/07_estudios_viabilidad/Est. Muelle Transversal y Poniente (Cruceros) - Valencia.pdf"
    ) == "estudio_viabilidad_valencia"
    assert _document_variant_from_source(
        "ops/07_estudios_viabilidad/Est. Terminal Cruceros - Malaga.pdf"
    ) == "estudio_viabilidad_malaga"
    assert _document_variant_from_source(
        "ops/08_anteproyectos_referencia/ANTEPROYECTO-OPS-PUERTO-BILBAO.pdf"
    ) == "anteproyecto_referencia"
    assert _expected_document_variants(
        "Que fases o tramites describe la guia de solicitantes AFIF?",
        ["ops"],
    ) == ["afif_guia_solicitantes"]
    assert _expected_document_variants(
        "Que exige AFIF para la comunicacion de interes y la conformidad de Estado miembro?",
        ["ops"],
    ) == ["afif_procedimiento_subvencion"]
    assert _expected_document_variants(
        "Segun el estudio de viabilidad OPS de Valencia, como se caracteriza la demanda energetica y la solucion recomendada?",
        ["ops"],
    ) == ["estudio_viabilidad_valencia"]
    assert _expected_document_variants(
        "Segun el anteproyecto OPS del puerto de Bilbao, que incluye la memoria de la infraestructura electrica?",
        ["ops"],
    ) == ["anteproyecto_referencia"]


def test_ops_phrase_queries_cover_project_and_monitoring_questions():
    phrases = _domain_phrase_queries(
        "Segun la IEC 80005-2, que datos se usan para monitorizacion y control SCADA?"
    )
    assert "IEC/IEEE 80005-2" in phrases
    assert "data communication for monitoring and control" in phrases
    assert "SCADA" in phrases

    project_phrases = _domain_phrase_queries(
        "Que recomienda OPS para pliegos de condiciones y programa de necesidades?"
    )
    assert "PLIEGOS DE CONDICIONES" in project_phrases
    assert "Programa de necesidades" in project_phrases
    assert "REDACCION DE PROYECTOS OPS" in project_phrases

    emsa_part1_phrases = _domain_phrase_queries(
        "Que dice la guia EMSA Part 1 sobre calidad de energia o compatibilidad red-buque?"
    )
    assert "EMSA Guidance on SSE Part 1" in emsa_part1_phrases
    assert "power quality" in emsa_part1_phrases

    emsa_part2_phrases = _domain_phrase_queries(
        "Que cubre la guia EMSA Part 2 sobre SSE?"
    )
    assert "EMSA Guidance on SSE Part 2" in emsa_part2_phrases
    assert "operational and safety aspects" in emsa_part2_phrases


def test_ops_phrase_queries_cover_afif_viability_and_anteproject_questions():
    afif_phrases = _domain_phrase_queries(
        "Que exige AFIF para la comunicacion de interes y la conformidad de Estado miembro?"
    )
    assert "CEF-T-AFIF-2024" in afif_phrases
    assert "Comunicacion de interes" in afif_phrases
    assert "conformidad de Estado miembro" in afif_phrases

    viability_phrases = _domain_phrase_queries(
        "Segun el estudio de viabilidad OPS, cual es la demanda energetica y la potencia instalada necesaria?"
    )
    assert "Estudio tecnico-economico" in viability_phrases
    assert "demanda energetica" in viability_phrases
    assert "potencia instalada necesaria" in viability_phrases

    anteproject_phrases = _domain_phrase_queries(
        "Que incluye el anteproyecto OPS del puerto de Bilbao en la memoria de infraestructura electrica?"
    )
    assert "ANTEPROYECTO" in anteproject_phrases
    assert "infraestructura electrica" in anteproject_phrases
    assert "Memoria" in anteproject_phrases


def test_ops_variants_cover_annex_shore_to_ship_and_planning_guides():
    assert _expected_document_variants(
        "Que es el Anexo 1 en la documentacion OPS y para que sirve?",
        ["ops"],
    ) == ["eopsa_checklist"]
    assert _expected_document_variants(
        "Que normativa tecnica aplica a la conexion shore-to-ship en OPS?",
        ["ops"],
    ) == ["normativa_base"]
    assert _expected_document_variants(
        "Que diferencias practicas hay entre una guia de planificacion de proyecto OPS y la normativa base IEC 80005?",
        ["ops"],
    ) == ["normativa_base", "planificacion_explotacion"]

    phrases = _domain_phrase_queries(
        "Que normativa tecnica aplica a la conexion shore-to-ship y que parte corresponde a guias de explotacion?"
    )
    assert "shore-to-ship connection and interface equipment" in phrases
    assert "PLANIFICACION Y EXPLOTACION DE INSTALACIONES OPS" in phrases


def test_alta_tension_variant_detects_lineas_at_and_tensiones_de_paso():
    assert _document_variant_from_source("alta_tension/A16436-16554.pdf") == "reglamento_lat"
    assert _expected_document_variants(
        "Que medidas de proteccion de las personas contempla el reglamento de AT frente a contactos y tensiones de paso?",
        ["alta_tension"],
    ) == ["reglamento_lat"]


def test_expected_domains_avoids_substring_false_positives():
    detected = _expected_domains(
        "Que criterios usa ISO 8528-5 para evaluar la respuesta transitoria de grupos electrogenos?"
    )
    assert "grupos_electrogenos" in detected
    assert "rite" not in detected


def test_exact_refs_preserve_itc_family_prefix():
    assert "itc-lat-09" in _extract_exact_refs("Resumen de la ITC LAT 09")
    assert "itc-bt-40" in _extract_exact_refs("Que es la ITC BT 40?")
    assert "itc-bt-09" not in _extract_exact_refs("Resumen de la ITC LAT 09")


def test_document_profile_metadata_is_backend_neutral():
    profile = _document_profile_metadata("fotovoltaica_om/Manual_OM_FV.pdf")
    assert profile == {
        "department": "mantenimiento",
        "domain": "fotovoltaica_om",
        "category": "fotovoltaica_om",
        "document_type": "manual",
        "document_layer": "manual_fabricante",
        "confidentiality": "internal",
        "regulation": "fotovoltaica_om",
        "document_variant": "",
    }


def test_document_profile_metadata_classifies_regulations():
    assert _document_profile_metadata("baja_tension/BOE-326_REBT.pdf")["regulation"] == "REBT"
    assert _document_profile_metadata("rite/A35931-35984.pdf")["regulation"] == "RITE"
    assert _document_profile_metadata("alta_tension/ITC-LAT-09.pdf")["regulation"] == "LAT"


def test_chunk_profile_metadata_detects_table_and_scope():
    profile = _chunk_profile_metadata(
        "rite/A35931-35984.pdf",
        "IT 3.3. Operaciones de mantenimiento preventivo",
        "Tabla 3.1. Limpieza de los condensadores U U",
        "table",
    )
    assert profile["section_type"] == "technical_instruction"
    assert profile["content_intent"] == "table"
    assert profile["table_hint"] == "tabla"
    assert profile["section_level"] == 2


def test_chunk_profile_metadata_extracts_structural_refs():
    profile = _chunk_profile_metadata(
        "rite/RITE-BOE-A-2007-15820-consolidado.pdf",
        "Articulo 12. Eficiencia energetica, energias renovables y energias residuales",
        "El articulo 12 establece criterios generales del reglamento.",
        "text",
    )
    assert "articulo 12" in profile["article_refs"]
    assert profile["it_section_refs"] == ""

    profile_it = _chunk_profile_metadata(
        "rite/RITE IT3.pdf",
        "IT 3.4. Programa de gestion energetica",
        "La IT 3.4 evalua el rendimiento de los equipos.",
        "text",
    )
    assert "it 3.4" in profile_it["it_section_refs"]


def test_rebt_contact_voltage_phrase_queries_cover_grounding_questions():
    phrases = _domain_phrase_queries(
        "maxima tension de contacto a lo largo de la vida util en puestas a tierra segun REBT"
    )
    assert "ITC-BT-09. INSTALACIONES DE ALUMBRADO EXTERIOR" in phrases
    assert "ITC-BT-52. INSTALACIONES CON FINES ESPECIALES" in phrases
    assert "ITC-BT-18" in phrases
    assert "no se puedan producir tensiones" in phrases
    assert "mayores de 24 V" in phrases
    assert "RESISTENCIA DE LAS TOMAS DE TIERRA" in phrases


def test_vehicle_charging_grounding_expands_to_normative_terms():
    phrases = _technical_equivalent_phrases(
        "Que sistemas de puesta a tierra son validos segun el REBT para carga de vehiculo electrico"
    )
    assert "ITC-BT-52" in phrases
    assert "sistemas de conexion del neutro" in phrases
    assert "TN-S" in phrases
    assert "contactos indirectos" in phrases


def test_technical_equivalents_are_conceptual_not_global():
    phrases = _technical_equivalent_phrases("Que documentacion necesito para una instalacion electrica")
    assert "ITC-BT-52" not in phrases
    assert "TN-S" not in phrases


def test_normative_intent_detects_validity_and_application_questions():
    assert _is_normative_intent_query("Que sistemas son validos segun el REBT")
    assert _is_normative_intent_query("Que esquema aplica para una instalacion receptora")
    assert not _is_normative_intent_query("Cual es la tension nominal de una instalacion")


def test_normative_application_hit_count_detects_scope_chunks():
    text = "1.4 Aplicacion de los tres tipos de esquemas. Red de distribucion publica."
    assert _normative_application_hit_count(text) >= 2


# ---------------------------------------------------------------------------
# Fallo 1 — Alias de documento en trigger_terms
# ---------------------------------------------------------------------------

ALIAS_DOMAIN_CASES = [
    # RITE por código de expediente
    ("segun el archivo a35931-35984 indicame los articulos del indice", "rite"),
    ("según el RITE A35931 cuál es la potencia mínima", "rite"),
    ("consulta sobre A35984 climatización", "rite"),
    # Alta tensión por código
    ("el documento a16436-16554 regula las líneas de alta tensión", "alta_tension"),
    ("según a16436 cual es la distancia mínima", "alta_tension"),
    # Baja tensión por referencia BOE
    ("según el BOE-326 cuales son los circuitos mínimos", "baja_tension"),
    ("el boe326 establece la sección mínima de conductores", "baja_tension"),
    # Trigger clásico siguen funcionando
    ("según el RITE cual es el mantenimiento de la caldera", "rite"),
    ("instalaciones termicas en edificios de oficinas", "rite"),
]


def test_alias_document_triggers_correct_domain():
    """Alias de expediente/BOE en la pregunta deben disparar el dominio correcto."""
    failures = []
    for question, expected in ALIAS_DOMAIN_CASES:
        detected = _expected_domains(question)
        if expected not in detected:
            failures.append(
                f"  q={question!r}: esperado={expected!r}, obtenido={detected!r}"
            )
    assert not failures, (
        f"\nAlias de documento sin detección de dominio en {len(failures)} caso(s):\n"
        + "\n".join(failures)
    )


# ---------------------------------------------------------------------------
# Fallo 2 — Decoder +31 de corrupción
# ---------------------------------------------------------------------------

def test_rite_table31_human_queries_trigger_rite_domain():
    """Preguntas humanas sobre Tabla 3.1/mantenimiento HVAC deben acotar a RITE."""
    questions = [
        "En un edificio de menos de 70kw indicame la periodicidad de revision general de caldera de gas segun tabla 3.1",
        "periodicidad de limpieza de condensadores en operaciones de mantenimiento",
        "y limpieza de los evaporadores?",
    ]
    for question in questions:
        assert "rite" in _expected_domains(question)


def test_decode_chunk_corruption_known_rows():
    """Las filas conocidas de la Tabla 3.1 del RITE se decodifican correctamente."""
    rite_source = "rite/A35931-35984.pdf"

    # Fila: Limpieza de los evaporadores  U  U
    row_evap = "-JNQJF[BEFMPTFWBQPSBEPSFT U U"
    decoded_evap = _decode_chunk_corruption(row_evap, rite_source)
    assert "limpieza" in decoded_evap.lower(), f"'limpieza' no encontrado: {decoded_evap!r}"
    assert "evaporadores" in decoded_evap.lower(), f"'evaporadores' no encontrado: {decoded_evap!r}"
    # Los códigos de periodicidad al final se preservan sin decodificar
    assert "U U" in decoded_evap or decoded_evap.strip().endswith("U"), (
        f"Códigos de periodicidad alterados: {decoded_evap!r}"
    )

    # Fila: Limpieza de los condensadores  U  U
    row_cond = "-JNQJF[BEFMPTDPOEFOTBEPSFT U U"
    decoded_cond = _decode_chunk_corruption(row_cond, rite_source)
    assert "limpieza" in decoded_cond.lower(), f"'limpieza' no encontrado: {decoded_cond!r}"
    assert "condensadores" in decoded_cond.lower(), f"'condensadores' no encontrado: {decoded_cond!r}"
    assert "U U" in decoded_cond or decoded_cond.strip().endswith("U"), (
        f"Códigos de periodicidad alterados: {decoded_cond!r}"
    )


def test_decode_rite_table31_adds_human_readable_legend():
    rite_source = "rite/A35931-35984.pdf"
    chunk = "Tabla?Operacionesdemantenimientopreventivoysuperiodicidad\nLimpiezadelosevaporadores. U U"
    decoded = _decode_chunk_corruption(chunk, rite_source)
    assert "Tabla 3.1" in decoded
    assert "Limpieza de los evaporadores" in decoded
    assert "U corresponde a una vez por temporada" in decoded


def test_decode_chunk_corruption_preserves_legible_text():
    """Texto legible del RITE (leyenda, títulos) no se modifica."""
    rite_source = "rite/A35931-35984.pdf"
    leyenda = "U: una vez por temporada (año)\nT: una vez cada semana"
    assert _decode_chunk_corruption(leyenda, rite_source) == leyenda

    heading = "IT 3.3. Operaciones de mantenimiento preventivo y su periodicidad"
    assert _decode_chunk_corruption(heading, rite_source) == heading


def test_decode_chunk_corruption_ignores_non_rite_sources():
    """Para fuentes que no son RITE el texto nunca se modifica."""
    non_rite = "baja_tension/BOE-326.pdf"
    row = "-JNQJF[BEFMPTDPOEFOTBEPSFT U U"
    assert _decode_chunk_corruption(row, non_rite) == row


def test_decode_chunk_corruption_multiline():
    """En un chunk con mezcla de filas corruptas y legibles, solo se decodifican las corruptas."""
    rite_source = "rite/A35931-35984.pdf"
    chunk = (
        "Tabla 3.1. Operaciones de mantenimiento preventivo\n"
        "-JNQJF[BEFMPTFWBQPSBEPSFT U U\n"
        "-JNQJF[BEFMPTDPOEFOTBEPSFT U U\n"
        "U: una vez por temporada (año)"
    )
    result = _decode_chunk_corruption(chunk, rite_source)
    lines = result.splitlines()
    assert lines[0] == "Tabla 3.1. Operaciones de mantenimiento preventivo"
    assert "evaporadores" in lines[1].lower()
    assert "condensadores" in lines[2].lower()
    assert lines[3] == "U: una vez por temporada (año)"


# ---------------------------------------------------------------------------
# Fallo 3 — detect_hint_domains para preguntas de seguimiento
# ---------------------------------------------------------------------------

def test_detect_hint_domains_from_rite_history():
    """Una pregunta RITE en el historial produce hint rite."""
    history_text = "segun el RITE cual es el mantenimiento recomendado para climatizacion"
    hints = detect_hint_domains(history_text)
    assert "rite" in hints


def test_detect_hint_domains_from_baja_tension_history():
    """Una pregunta REBT en el historial produce hint baja_tension."""
    history_text = "según el REBT cuál es la sección mínima del circuito C1"
    hints = detect_hint_domains(history_text)
    assert "baja_tension" in hints


def test_detect_hint_domains_from_rite_table31_followup():
    """Seguimientos sobre evaporadores mantienen el dominio RITE."""
    hints = detect_hint_domains("y cuál es la periodicidad de limpieza de los evaporadores")
    assert "rite" in hints


def test_detect_hint_domains_empty_input():
    """Texto vacío devuelve lista vacía sin error."""
    assert detect_hint_domains("") == []
    assert detect_hint_domains("   ") == []


# ---------------------------------------------------------------------------
# Fallo 4 — phrase_queries para RITE Tabla 3.1 e índice
# ---------------------------------------------------------------------------

def test_rite_table31_phrase_queries_condensadores():
    phrases = _domain_phrase_queries("periodicidad de limpieza de condensadores segun tabla 3.1 del RITE")
    assert "-JNQJF[BEFMPTDPOEFOTBEPSFT" in phrases
    assert "una vez por temporada" in phrases


def test_rite_table31_phrase_queries_evaporadores():
    """Pregunta de seguimiento solo con 'evaporadores' activa la phrase_query."""
    phrases = _domain_phrase_queries("y limpieza de los evaporadores")
    assert "-JNQJF[BEFMPTFWBQPSBEPSFT" in phrases
    assert "una vez por temporada" in phrases


def test_rite_table31_phrase_queries_caldera_gas():
    phrases = _domain_phrase_queries(
        "revision general de caldera de gas periodicidad tabla 3.1 mantenimiento"
    )
    assert "una vez por temporada" in phrases


def test_rite_table31_phrase_queries_mantenimiento_tabla():
    phrases = _domain_phrase_queries("operaciones de mantenimiento tabla del RITE IT 3.3")
    assert "una vez por temporada" in phrases
    assert "IT 3.3" in phrases


def test_rite_indice_phrase_queries_with_rite():
    phrases = _domain_phrase_queries("indicame el indice del rite")
    assert "rticulo 1" in phrases
    assert "NDICE" in phrases


def test_rite_indice_phrase_queries_with_articulos():
    phrases = _domain_phrase_queries("segun el archivo a35931-35984 enumerame los articulos del indice")
    assert "rticulo 1" in phrases
    assert "NDICE" in phrases


# ---------------------------------------------------------------------------
# Fallo 7 — encoding shift31 derivado de domains.json (no hardcodeado)
# ---------------------------------------------------------------------------

def test_shift31_tokens_derived_from_domains_json():
    """_SHIFT31_SOURCE_TOKENS se deriva de domains.json y contiene tokens de RITE."""
    assert "a35931" in _SHIFT31_SOURCE_TOKENS, (
        "RITE tiene encoding shift31 en domains.json pero 'a35931' no está en _SHIFT31_SOURCE_TOKENS"
    )
    assert "rite" in _SHIFT31_SOURCE_TOKENS, (
        "'rite' debería estar en _SHIFT31_SOURCE_TOKENS"
    )


def test_rag_index_version_is_part_of_embedding_version():
    """RAG_INDEX_VERSION forma parte de la version que fuerza reindexado."""
    assert "-i" in _EF_VERSION
    assert _rag_index_version_tag("  produccion 2 / textos corruptos ") == "produccion-2-textos-corruptos"
    assert _rag_index_version_tag("") == "1"


def test_decode_corruption_uses_config_not_hardcode():
    """El decoder actúa sobre fuentes que coincidan con source_tokens de dominios shift31."""
    # Fuente con token 'rite' (derivado de domains.json)
    assert "limpieza" in _decode_chunk_corruption(
        "-JNQJF[BEFMPTDPOEFOTBEPSFT U U", "rite/doc.pdf"
    ).lower()
    # Fuente con token 'a35931' (derivado de domains.json)
    assert "limpieza" in _decode_chunk_corruption(
        "-JNQJF[BEFMPTDPOEFOTBEPSFT U U", "docs/a35931_rite.pdf"
    ).lower()
    # Fuente sin token en _SHIFT31_SOURCE_TOKENS → no se modifica
    row = "-JNQJF[BEFMPTDPOEFOTBEPSFT U U"
    assert _decode_chunk_corruption(row, "fotovoltaica/manual_om.pdf") == row


def test_adding_new_shift31_domain_in_config_applies_decoder(tmp_path, monkeypatch):
    """Añadir un nuevo dominio con encoding shift31 en domains.json aplica el decoder sin cambios de código."""
    import json
    import importlib
    import rag_service

    # Clonar config actual y añadir dominio ficticio con shift31
    fake_cfg = {
        "domains": {
            "nuevo_dominio": {
                "encoding": {"type": "shift31"},
                "source_tokens": ["fakepdf123"],
            }
        },
        "filename_overrides": {},
    }
    fake_path = tmp_path / "domains.json"
    fake_path.write_text(json.dumps(fake_cfg), encoding="utf-8")

    monkeypatch.setattr(rag_service, "_DOMAINS_CONFIG_PATH", fake_path)
    new_cfg = rag_service._load_domain_config()
    new_tokens = frozenset(
        token
        for cfg in new_cfg.get("domains", {}).values()
        if cfg.get("encoding", {}).get("type") == "shift31"
        for token in cfg.get("source_tokens", [])
    )
    monkeypatch.setattr(rag_service, "_SHIFT31_SOURCE_TOKENS", new_tokens)

    row = "-JNQJF[BEFMPTDPOEFOTBEPSFT U U"
    assert "limpieza" in rag_service._decode_chunk_corruption(row, "fakepdf123/doc.pdf").lower()
    # Fuente que no pertenece al nuevo dominio no se modifica
    assert rag_service._decode_chunk_corruption(row, "rite/doc.pdf") == row


# ---------------------------------------------------------------------------
# Fallo 6 — _auto_technical_terms: extracción automática de términos técnicos
# ---------------------------------------------------------------------------

def test_auto_terms_extracts_long_technical_words():
    """Palabras técnicas de 8+ chars se extraen de la pregunta."""
    terms = _auto_technical_terms("periodicidad de limpieza de condensadores segun RITE")
    assert "condensadores" in terms
    assert "periodicidad" in terms


def test_auto_terms_excludes_generic_long_words():
    """Palabras genéricas (instalaciones, articulos...) no se extraen aunque tengan 8+ chars."""
    terms = _auto_technical_terms("cuales son las instalaciones termicas segun el reglamento")
    assert "instalaciones" not in terms
    assert "reglamento" not in terms


def test_auto_terms_no_short_words():
    """Palabras de menos de 8 caracteres no se incluyen."""
    terms = _auto_technical_terms("tabla rite limpia medir valor gas")
    assert all(len(t) >= 8 for t in terms), f"Término corto encontrado: {terms}"


def test_auto_terms_deduplicates():
    """Sin duplicados aunque la palabra aparezca varias veces."""
    terms = _auto_technical_terms("condensadores y mas condensadores de los condensadores")
    assert terms.count("condensadores") == 1


def test_auto_terms_empty_input():
    """Entrada vacía o corta devuelve lista vacía sin error."""
    assert _auto_technical_terms("") == []
    assert _auto_technical_terms("que es") == []


# ---------------------------------------------------------------------------
# Variante documental — _document_variant_from_source
# ---------------------------------------------------------------------------

VARIANT_SOURCE_CASES = [
    ("rite/RITE IT3.pdf", "it3"),
    ("rite/RITE-2021-BOE-A-2021-4572.pdf", "2021"),
    ("rite/RITE-BOE-A-2007-15820-consolidado.pdf", "consolidado"),
    # Fuentes sin variante configurada
    ("baja_tension/BOE-326_REBT.pdf", ""),
    ("alta_tension/ITC-LAT-09.pdf", ""),
    ("guias_tecnicas/Guia_BT_40.pdf", ""),
    ("rite/RITE-desconocido.pdf", ""),
]

VARIANT_ENRICHED_SOURCE_CASES = [
    (
        "rite/RITE-BOE-A-2007-15820-consolidado.pdf (pag. 5, pag. doc 2021)",
        "consolidado",
    ),
    (
        "rite/RITE-BOE-A-2007-15820-consolidado.pdf (pag. 11, Artículo 12. Eficiencia energética.)",
        "consolidado",
    ),
    (
        "rite/RITE-2021-BOE-A-2021-4572.pdf (pag. 3, pag. doc 2021)",
        "2021",
    ),
]


def test_document_variant_from_rite_sources():
    """Las variantes del dominio RITE se derivan correctamente del nombre de fichero."""
    failures = []
    for source, expected in VARIANT_SOURCE_CASES:
        actual = _document_variant_from_source(source)
        if actual != expected:
            failures.append(f"  {source!r}: esperado={expected!r}, obtenido={actual!r}")
    assert not failures, (
        f"\nVariante incorrecta en {len(failures)} caso(s):\n" + "\n".join(failures)
    )


def test_document_variant_in_profile_metadata():
    """_document_profile_metadata incluye document_variant derivado del fichero."""
    profile = _document_profile_metadata("rite/RITE IT3.pdf")
    assert profile["document_variant"] == "it3"

    profile2021 = _document_profile_metadata("rite/RITE-2021-BOE-A-2021-4572.pdf")
    assert profile2021["document_variant"] == "2021"

    profile_bt = _document_profile_metadata("baja_tension/BOE-326_REBT.pdf")
    assert profile_bt["document_variant"] == ""


def test_document_variant_ignores_page_snippet_suffixes():
    """La variante debe salir del PDF real, no del texto añadido al source."""
    failures = []
    for source, expected in VARIANT_ENRICHED_SOURCE_CASES:
        actual = _document_variant_from_source(source)
        if actual != expected:
            failures.append(f"  {source!r}: esperado={expected!r}, obtenido={actual!r}")
    assert not failures, (
        f"\nVariante incorrecta en {len(failures)} source(s) enriquecidos:\n"
        + "\n".join(failures)
    )


# ---------------------------------------------------------------------------
# Variante documental — _expected_document_variants
# ---------------------------------------------------------------------------

def test_expected_variants_rite_it3_from_query():
    """Preguntas que mencionan IT 3 o la Tabla 3.1 deben apuntar a la variante it3."""
    assert "it3" in _expected_document_variants("según la IT 3.3 cuál es la periodicidad de revisión", ["rite"])
    assert "it3" in _expected_document_variants("operaciones de mantenimiento preventivo RITE", ["rite"])
    assert "it3" in _expected_document_variants("tabla 3.1 del RITE", ["rite"])


def test_expected_variants_rite_2021_from_query():
    """Preguntas sobre la modificación 2021 o BACS deben apuntar a la variante 2021."""
    assert "2021" in _expected_document_variants("qué cambió en el RITE en 2021", ["rite"])
    assert "2021" in _expected_document_variants("sistemas BACS según el RITE", ["rite"])
    assert "2021" in _expected_document_variants("RD 178/2021 automatizacion y control de edificios", ["rite"])


def test_expected_variants_rite_consolidado_from_query():
    """Preguntas que citan el texto consolidado apuntan a la variante consolidado."""
    assert "consolidado" in _expected_document_variants("según el RITE consolidado cuál es el artículo 1", ["rite"])


def test_expected_variants_empty_when_no_trigger():
    """Sin triggers de variante la lista es vacía."""
    assert _expected_document_variants("cuál es el objeto del RITE", ["rite"]) == []
    assert _expected_document_variants("cuáles son las ITC del REBT", ["baja_tension"]) == []


def test_expected_variants_ignores_other_domains():
    """Los triggers de variante de un dominio no afectan a otros dominios."""
    # "it3" solo está configurado en rite, no en baja_tension
    variants = _expected_document_variants("IT 3.3 del REBT", ["baja_tension"])
    assert "it3" not in variants


# ---------------------------------------------------------------------------
# Variante documental — detect_hint_document_variants
# ---------------------------------------------------------------------------

def test_detect_hint_variants_from_it3_history():
    """Historial con IT 3 propaga variante it3."""
    hints = detect_hint_document_variants("según la IT 3.3 del RITE cuál es la periodicidad", ["rite"])
    assert "it3" in hints


def test_detect_hint_variants_from_2021_history():
    """Historial con 2021 propaga variante 2021."""
    hints = detect_hint_document_variants("el RITE 2021 modificó los sistemas BACS", ["rite"])
    assert "2021" in hints


def test_detect_hint_variants_infers_domain_if_no_hint_domains():
    """Sin hint_domains el dominio se infiere del texto."""
    hints = detect_hint_document_variants("según el RITE 2021 cuáles son los sistemas BACS")
    assert "2021" in hints


def test_detect_hint_variants_empty_input():
    """Texto vacío devuelve lista vacía sin error."""
    assert detect_hint_document_variants("") == []
    assert detect_hint_document_variants("   ") == []


# ---------------------------------------------------------------------------
# IT section references — _extract_it_section_refs
# ---------------------------------------------------------------------------

def test_extract_it_section_refs_basic():
    assert "it 3" in _extract_it_section_refs("según la IT 3 del RITE")
    assert "it 3.3" in _extract_it_section_refs("IT 3.3. Operaciones de mantenimiento")
    assert "it 1" in _extract_it_section_refs("IT 1 y IT 2 del RITE")
    assert "it 2" in _extract_it_section_refs("IT 1 y IT 2 del RITE")


def test_extract_it_section_refs_no_false_positives():
    """'unit', 'kit' etc. no deben generar refs IT."""
    assert _extract_it_section_refs("unidad 3 del kit") == []
    assert _extract_it_section_refs("información técnica general") == []
    assert _extract_it_section_refs("") == []


def test_extract_it_section_refs_deduplicates():
    refs = _extract_it_section_refs("IT 3 de IT 3 según IT 3")
    assert refs.count("it 3") == 1


def test_extract_article_refs_basic():
    assert "articulo 12" in _extract_article_refs("segun el articulo 12 del RITE")
    assert "articulo 37" in _extract_article_refs("art. 37 tras la modificacion de 2021")


def test_extract_article_refs_no_false_positives():
    assert _extract_article_refs("hay 12 sistemas de control") == []
    assert _extract_article_refs("") == []


def test_extract_article_refs_deduplicates():
    refs = _extract_article_refs("articulo 12 y art. 12 del mismo reglamento")
    assert refs == ["articulo 12"]


def test_detect_hint_article_refs_from_history():
    hints = detect_hint_article_refs("segun el articulo 12 del RITE cual es su alcance")
    assert hints == ["articulo 12"]


def test_detect_hint_it_section_refs_from_history():
    hints = detect_hint_it_section_refs("seguimos con la IT 3.3 del RITE y su periodicidad")
    assert "it 3.3" in hints


# --- Regresión: artículos genéricos (Q13/Q14 pattern) ---

def test_q13_article_ref_detected():
    """Q13: 'articulo 12' debe extraerse de la pregunta."""
    refs = _extract_article_refs(
        "que establece el articulo 12 sobre eficiencia energetica energias renovables y residuales"
    )
    assert "articulo 12" in refs


def test_q13_no_rite_domain_from_question_alone():
    """Q13 no contiene trigger_terms de RITE: el dominio debe venir de hints, no de la pregunta."""
    domains = _expected_domains(
        "que establece el articulo 12 sobre eficiencia energetica energias renovables y residuales"
    )
    assert "rite" not in domains, "El dominio RITE debe provenir de hints, no detectarse en Q13"


def test_q14_article_ref_detected():
    """Q14: 'articulo 37' debe extraerse correctamente."""
    refs = _extract_article_refs("que dice el articulo 37 sobre inspecciones periodicas")
    assert "articulo 37" in refs


def test_article_refs_no_domain_crosstalk():
    """Artículos genéricos (12, 37) no deben disparar dominios alta_tension ni baja_tension."""
    for q in (
        "que establece el articulo 12 sobre eficiencia energetica",
        "que dice el articulo 37 sobre inspecciones",
        "y el articulo 12",
    ):
        domains = _expected_domains(q)
        assert "alta_tension" not in domains, f"alta_tension disparado incorrectamente por: {q!r}"
        assert "baja_tension" not in domains, f"baja_tension disparado incorrectamente por: {q!r}"


def test_consolidado_variant_requires_explicit_trigger():
    """La variante 'consolidado' solo se detecta si el texto menciona un trigger explícito."""
    # Sin trigger → vacío
    assert _expected_document_variants(
        "que establece el articulo 12 sobre renovables", ["rite"]
    ) == []
    # Con trigger → detecta consolidado
    assert "consolidado" in _expected_document_variants(
        "segun el RITE consolidado articulo 12", ["rite"]
    )


if __name__ == "__main__":
    # Ejecución directa sin pytest, útil para iterar rápido.
    failures = 0
    for source, expected in CASES:
        actual = _source_domain_key(source)
        status = "OK" if actual == expected else "FAIL"
        if actual != expected:
            failures += 1
        print(f"[{status}] {source} -> {actual} (esperado: {expected})")
    print(f"\n{'-' * 60}")
    if failures:
        print(f"{failures} fallo(s) de {len(CASES)} casos")
        sys.exit(1)
    else:
        print(f"OK: {len(CASES)}/{len(CASES)} casos pasados")
