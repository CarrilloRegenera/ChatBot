"""Constantes y helpers ligeros para el dominio OPS.

La idea es mantener las senales de OPS encapsuladas en un modulo propio para
que el routing tecnico y las protecciones frente a negocio no queden mezcladas
con la logica historica del resto de dominios.
"""

from __future__ import annotations

import re
import unicodedata


OPS_TECHNICAL_HINTS = (
    "ops",
    "on shore power supply",
    "shore power",
    "shore side electricity",
    "shore-side electricity",
    "sse",
    "cold ironing",
    "eopsa",
    "emsa guidance",
    "emsa guidance on sse",
    "iec 80005",
    "ieee 80005",
    "iso 80005",
    "80005-1",
    "80005-2",
    "80005-3",
    "hvsc",
    "lvsc",
    "data communication",
    "monitorizacion y control",
    "monitoring and control",
    "electrificacion de atraques",
    "electrificacion de buques",
    "suministro electrico a buques",
    "conexion buque puerto",
    "conexiones buque puerto",
    "buque puerto",
    "atraque electrificado",
    "atraques electrificados",
    "puerto electrificado",
    "puertos electrificados",
    "afif",
    "cef-t",
    "cef transport",
    "subvencion afif",
    "comunicacion de interes",
    "conformidad de estado miembro",
    "estudio tecnico-economico",
    "estudio de viabilidad",
    "viabilidad ops",
    "demanda energetica",
    "solucion recomendada",
    "anteproyecto ops",
    "infraestructura electrica",
)


OPS_DOCUMENTARY_HINTS = OPS_TECHNICAL_HINTS + (
    "guia eopsa",
    "guia ops completa",
    "guia completa de eopsa",
    "guia para una licitacion ops completa y exitosa",
    "checklist ops",
    "lista de verificacion",
    "lista de verificacion completa",
    "shore-side",
    "shore side",
    "planificacion y explotacion",
    "redaccion de proyectos",
    "programa de necesidades",
    "modulo ops",
    "subestacion de conversion",
    "sistema de gestion de cables",
    "cms",
    "pliegos de condiciones",
    "questionnaire results",
    "world ports climate action",
    "afif",
    "cef-t",
    "subvencion afif",
    "comunicacion de interes",
    "conformidad de estado miembro",
    "estudio de viabilidad",
    "estudio de malaga",
    "estudio malaga",
    "estudio tecnico-economico",
    "anteproyecto ops",
    "puerto de malaga",
    "terminal de cruceros",
    "puerto de bilbao",
    "santurtzi",
)


_OPS_STANDARD_REFERENCE_RE = re.compile(r"\b80005(?:-[123])?\b")


def _normalize_ops_text(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text or "")
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    normalized = normalized.lower().strip()
    normalized = re.sub(r"[^\w\s/-]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def is_ops_standard_reference(reference: str | None, text: str | None = None) -> bool:
    normalized_reference = _normalize_ops_text(reference or "").replace(" ", "")
    if not normalized_reference:
        return False
    if not _OPS_STANDARD_REFERENCE_RE.search(normalized_reference):
        return False

    normalized_text = _normalize_ops_text(text or "")
    if not normalized_text:
        return True

    return any(hint in normalized_text for hint in OPS_DOCUMENTARY_HINTS)
