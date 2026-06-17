from __future__ import annotations

import re
import unicodedata


LICITACION_REFERENCE_PATTERN = re.compile(r"\b(?:est[-\s]?\d{1,4}[-\s]?20\d{2}|[a-z]{2,6}-\d{1,5}-20\d{2})\b")
PRODUCCION_CODE_PATTERN = re.compile(r"\b\d{5}\b")

_EXPLICIT_TECHNICAL_REFERENCE_HINTS = (
    "rebt",
    "rite",
    "ralt",
    "itc",
    "iec 80005",
    "ieee 80005",
    "iso 80005",
    "80005-1",
    "80005-2",
    "80005-3",
    "hvsc",
    "lvsc",
    "shore-to-ship",
    "shore to ship",
    "shore-side electricity",
    "shore side electricity",
    "cold ironing",
    "anexo 1",
    "checklist",
    "lista de verificacion",
    "lista de verificación",
    "guia eopsa",
    "guía eopsa",
    "afif",
    "cef-t",
    "estudio de viabilidad",
    "estudio de malaga",
    "estudio malaga",
    "estudio tecnico-economico",
    "estudio técnico-económico",
    "anteproyecto ops",
    "infraestructura electrica",
    "infraestructura eléctrica",
    "memoria de infraestructura",
    "guia ops completa",
    "guia completa de eopsa",
    "guia para una licitacion ops completa y exitosa",
    "puerto de malaga",
    "terminal de cruceros",
    "puerto de bilbao",
    "santurtzi",
    "modulo ops",
    "subestacion de conversion",
    "sistema de gestion de cables",
)

_MIXED_SCOPE_BUSINESS_HINTS = (
    "cliente",
    "importe",
    "presupuesto",
    "produccion",
    "produccion total",
    "backlog",
    "adjudicado",
    "adjudicada",
    "fecha de presentacion",
    "fecha presentacion",
    "fecha de apertura",
    "proyecto 26",
    "licitaciones mas recientes",
    "licitaciones mas recientes relacionadas",
    "top de proyectos",
    "top de licitaciones",
    "ranking",
    "en curso",
    "estado",
)


def normalize_signal_text(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text or "")
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    normalized = normalized.lower().strip()
    normalized = re.sub(r"[^\w\s/-]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def has_explicit_technical_reference(text: str) -> bool:
    normalized = normalize_signal_text(text)
    return any(hint in normalized for hint in _EXPLICIT_TECHNICAL_REFERENCE_HINTS)


def has_concrete_business_reference(text: str) -> bool:
    normalized = normalize_signal_text(text)
    if LICITACION_REFERENCE_PATTERN.search(normalized):
        return True
    if PRODUCCION_CODE_PATTERN.search(normalized):
        numeric_ref = PRODUCCION_CODE_PATTERN.search(normalized).group(0)
        if re.search(r"\b80005(?:-[123])?\b", normalized) and numeric_ref == "80005":
            return False
        return True
    return False


def is_mixed_scope_query(text: str, *, has_business_signal: bool) -> bool:
    normalized = normalize_signal_text(text)
    if not has_business_signal:
        return False
    if not has_explicit_technical_reference(normalized):
        return False
    return any(hint in normalized for hint in _MIXED_SCOPE_BUSINESS_HINTS)
