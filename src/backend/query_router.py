import re
import unicodedata
from typing import Dict

from config import MIN_QUERY_LENGTH
from ops_domain import OPS_DOCUMENTARY_HINTS, OPS_TECHNICAL_HINTS, is_ops_standard_reference


GREETING_PATTERNS = (
    r"^hola\b",
    r"^hola+\b",
    r"^buenas\b",
    r"^buenos dias\b",
    r"^buenas tardes\b",
    r"^buenas noches\b",
    r"^hey\b",
)

SMALLTALK_EXACT = {
    "hola",
    "hola",
    "buenas",
    "buenos dias",
    "buenas tardes",
    "buenas noches",
    "gracias",
    "ok",
    "vale",
    "perfecto",
}

SMALLTALK_HINTS = {
    "como te llamas",
    "quien eres",
    "que eres",
    "que puedes hacer",
    "para que sirves",
    "puedes ayudarme",
    "eres un chatbot",
    "eres una ia",
    "gracias por tu ayuda",
}

OUT_OF_SCOPE_HINTS = {
    "futbol", "nba", "temperatura", "tiempo", "clima", "horoscopo",
    "chiste", "receta", "videojuego", "pelicula",
}

DOCUMENTARY_HINTS = {
    "manual", "pdf", "documentacion", "documento", "normativa", "normativo",
    "norma", "reglamento", "apartado", "seccion", "capitulo", "pagina",
    "pag.", "segun el manual", "segun el documento", "segun el pdf",
    *OPS_DOCUMENTARY_HINTS,
}

TECHNICAL_HINTS = {
    "rebt", "rite", "ralt", "itc", "instalacion", "instalaciones", "baja tension",
    "alta tension", "linea", "lineas", "inspeccion", "inspecciones",
    "electrificacion", "conductor", "neutro", "potencia", "proteccion", "diferencial",
    "puesta a tierra", "pararrayos", "documentacion", "memoria tecnica", "certificado",
    "canalizacion", "canalizaciones", "distancia", "acometida", "acometidas",
    "tubo", "tubos", "cerca", "cercas", "alimentador", "alimentadores",
    "circuito", "circuitos", "seccion", "secciones", "vivienda", "viviendas",
    "generadora", "generadoras", "aislada", "aisladas", "asistida", "asistidas",
    "interconectada", "interconectadas", "grupo electrogeno", "grupos electrogenos",
    "iso 8528", "respuesta transitoria",
    "mantenimiento", "arranque", "motor", "bateria", "remolque", "enganche",
    "desenganche", "recepcion", "izado", "combustible", "escape", "ventilacion",
    "refrigeracion", "baja carga", "banco de carga", "magnetotermico", "calefactor",
    "calefaccion", "automatico", "grupo movil", "grupo automatico", "himoinsa",
    *OPS_TECHNICAL_HINTS,
}

BUSINESS_LICITACIONES_HINTS = {
    "licitacion", "licitaciones", "oferta", "adjudicacion", "adjudicada",
    "fecha de adjudicacion", "fecha de presentacion", "fecha de apertura",
}

BUSINESS_PRODUCCION_HINTS = {
    "produccion", "control de obras", "cartera", "responsable",
    "cuatrimestre", "rentabilidad", "codigo obra",
}

BUSINESS_COMMON_HINTS = {
    "importe contratado", "cliente", "estado", "comentarios",
    "numero proyecto", "numero oferta", "tipo obra",
}

LICITACION_REFERENCE_PATTERN = re.compile(r"\b(?:est[-\s]?\d{1,4}[-\s]?20\d{2}|[a-z]{2,6}-\d{1,5}-20\d{2})\b")
PRODUCCION_CODE_PATTERN = re.compile(r"\b\d{5}\b")


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text or "")
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    normalized = normalized.lower().strip()
    normalized = re.sub(r"[¿?¡!.,;:()]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _is_smalltalk(text: str) -> bool:
    if text in SMALLTALK_EXACT:
        return True
    if any(re.search(pattern, text) for pattern in GREETING_PATTERNS):
        return True
    if any(hint in text for hint in SMALLTALK_HINTS):
        return True
    if len(text.split()) <= 5 and any(token in text for token in ("gracias", "ok", "vale", "perfecto")):
        return True
    return False


def _is_out_of_scope(text: str) -> bool:
    if any(hint in text for hint in OUT_OF_SCOPE_HINTS):
        return True
    return False


def _looks_technical(text: str) -> bool:
    return any(hint in text for hint in TECHNICAL_HINTS)


def _looks_documentary(text: str) -> bool:
    return any(hint in text for hint in DOCUMENTARY_HINTS)


def _has_strong_business_signal(text: str) -> bool:
    if LICITACION_REFERENCE_PATTERN.search(text):
        return True
    if PRODUCCION_CODE_PATTERN.search(text):
        return True
    if any(hint in text for hint in BUSINESS_LICITACIONES_HINTS):
        return True
    if any(hint in text for hint in BUSINESS_PRODUCCION_HINTS):
        return True
    if "importe contratado" in text:
        return True
    return False


def classify_question(question: str) -> Dict[str, str]:
    text = (question or "").strip()
    normalized = _normalize(text)
    documentary_or_technical = _looks_documentary(normalized) or _looks_technical(normalized)

    if len(normalized) < MIN_QUERY_LENGTH:
        return {
            "route": "invalid",
            "message": "La consulta es demasiado corta. Anade mas detalle para poder responder con precision.",
        }

    if _is_smalltalk(normalized) and not documentary_or_technical:
        return {
            "route": "smalltalk",
            "message": "Hola. Soy el asistente tecnico de REGENERA y puedo ayudarte con consultas basadas en la documentacion cargada.",
        }

    if _is_out_of_scope(normalized) and not documentary_or_technical:
        return {
            "route": "out_of_scope",
            "message": "Esa pregunta parece fuera del alcance documental actual. Haz una consulta tecnica sobre la documentacion cargada.",
        }

    has_licitacion_reference = bool(LICITACION_REFERENCE_PATTERN.search(normalized))
    has_produccion_code = bool(PRODUCCION_CODE_PATTERN.search(normalized))
    if has_licitacion_reference:
        return {"route": "business_licitaciones", "message": ""}
    if has_produccion_code and not is_ops_standard_reference(PRODUCCION_CODE_PATTERN.search(normalized).group(0), normalized) and not any(hint in normalized for hint in BUSINESS_LICITACIONES_HINTS):
        return {"route": "business_produccion", "message": ""}

    if documentary_or_technical and not _has_strong_business_signal(normalized):
        return {"route": "knowledge", "message": ""}

    if any(hint in normalized for hint in BUSINESS_LICITACIONES_HINTS):
        return {"route": "business_licitaciones", "message": ""}

    if "importe contratado" in normalized:
        return {"route": "business_licitaciones", "message": ""}

    if any(hint in normalized for hint in BUSINESS_PRODUCCION_HINTS):
        return {"route": "business_produccion", "message": ""}

    if any(hint in normalized for hint in BUSINESS_COMMON_HINTS) and _has_strong_business_signal(normalized):
        return {"route": "business_licitaciones", "message": ""}

    return {"route": "knowledge", "message": ""}
