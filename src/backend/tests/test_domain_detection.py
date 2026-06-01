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

from rag_service import _domain_phrase_queries, _source_domain_key, _source_taxonomy  # noqa: E402


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
