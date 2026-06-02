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
    _domain_phrase_queries,
    _EF_VERSION,
    _expected_domains,
    _rag_index_version_tag,
    _SHIFT31_SOURCE_TOKENS,
    _source_domain_key,
    _source_taxonomy,
    detect_hint_domains,
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
        {"department": "ingenieria", "document_type": "reglamento", "confidentiality": "internal"},
    ),
    (
        "baja_tension/BOE-326_Reglamento_electrotecnico_para_baja_tension_e_ITC.pdf",
        {"department": "ingenieria", "document_type": "reglamento", "confidentiality": "internal"},
    ),
    (
        "rite/A35931-35984.pdf",
        {"department": "ingenieria", "document_type": "reglamento", "confidentiality": "internal"},
    ),
    (
        "guias_tecnicas/Guia_bt_40_sep13R1 (1).pdf",
        {"department": "ingenieria", "document_type": "guia_tecnica", "confidentiality": "internal"},
    ),
    (
        "fotovoltaica_om/Manual-de-Manteminiento.pdf",
        {"department": "mantenimiento", "document_type": "manual", "confidentiality": "internal"},
    ),
    (
        "grupos_electrogenos/ISO-8528-5-2018.pdf",
        {"department": "mantenimiento", "document_type": "norma", "confidentiality": "internal"},
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
        "confidentiality": "restricted",
    }


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
