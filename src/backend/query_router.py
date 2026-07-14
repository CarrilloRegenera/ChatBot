import re
from typing import Dict

from config import MIN_QUERY_LENGTH
from ops_domain import OPS_DOCUMENTARY_HINTS, OPS_TECHNICAL_HINTS, is_ops_standard_reference
from routing_taxonomy import (
    BUSINESS_COMMON_HINTS,
    BUSINESS_LICITACIONES_HINTS,
    BUSINESS_PRODUCCION_HINTS,
    COMMON_DOCUMENTARY_HINTS,
)
from routing_signals import (
    LICITACION_REFERENCE_PATTERN,
    PRODUCCION_CODE_PATTERN,
    has_concrete_business_reference,
    has_explicit_technical_reference,
    is_mixed_scope_query,
)
from text_normalization import normalize_for_matching


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
    *COMMON_DOCUMENTARY_HINTS,
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

BUSINESS_LICITACIONES_HINTS = set(BUSINESS_LICITACIONES_HINTS)

BUSINESS_PRODUCCION_HINTS = set(BUSINESS_PRODUCCION_HINTS)

BUSINESS_COMMON_HINTS = set(BUSINESS_COMMON_HINTS)

DOCUMENT_INVENTORY_HINTS = {
    "que documentos hay",
    "que documentos tenemos",
    "que documentos tiene",
    "que documentacion tenemos",
    "que documentacion hay",
    "que documentacion tiene",
    "que documentacion tecnica hay",
    "que documentacion tecnica hay indexada",
    "que documentacion tecnica hay indexada ahora mismo",
    "que documentacion tecnica esta indexada",
    "que documentacion tecnica esta cargada",
    "documentos disponibles",
    "documentacion disponible",
    "estructura de documentos",
    "estructura documental",
    "como esta organizada la documentacion",
    "como esta organizada la documentacion tecnica",
    "que dominios documentales hay",
    "que reglamentos tenemos cargados",
    # sinónimos de archivo/fichero
    "que archivos hay",
    "que archivos tenemos",
    "que archivos tiene",
    "que ficheros hay",
    "que ficheros tenemos",
    "que ficheros tiene",
    "archivos disponibles",
    "ficheros disponibles",
}

_INVENTORY_NOUNS = {"documentos", "documentacion", "reglamentos", "normativa", "bloques", "archivos", "ficheros", "pdf", "pdfs"}
# Subconjunto para verbos débiles (tiene/tienen): solo sustantivos inequívocamente de listado de ficheros
_INVENTORY_NOUNS_LISTING = {"documentos", "archivos", "ficheros", "pdf", "pdfs"}
_INVENTORY_VERBS = {"hay", "tenemos", "disponibles", "cargados", "cargadas", "indexados", "indexadas", "existen"}
_INVENTORY_VERBS_WEAK = {"tiene", "tienen"}
_INVENTORY_STRUCTURE = {"estructura", "organizada", "organizado", "reparten", "divididos", "clasificados", "bloques"}


def _token_resembles_inventory_noun(token: str) -> bool:
    """Detecta typos por transposición como 'docuemntos' → 'documentos' (prefijo 4 chars + longitud ±2).
    Excluye tokens que ya son sustantivos exactos para evitar activar la ruta de verbs débiles."""
    if token in _INVENTORY_NOUNS:
        return False
    if len(token) < 5:
        return False
    prefix = token[:4]
    for noun in _INVENTORY_NOUNS:
        if len(noun) >= 4 and noun[:4] == prefix and abs(len(token) - len(noun)) <= 2:
            return True
    return False

def _normalize(text: str) -> str:
    return normalize_for_matching(text, r"[Â¿?Â¡!.,;:()]+")


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


def _asks_for_document_inventory(text: str) -> bool:
    if any(hint in text for hint in DOCUMENT_INVENTORY_HINTS):
        return True
    if "bloque de" in text and any(hint in text for hint in TECHNICAL_HINTS):
        return True
    if any(term in text for term in (
        "documentos de", "documentacion de", "solo los documentos", "archivos de", "ficheros de",
    )) and any(hint in text for hint in TECHNICAL_HINTS):
        return True
    tokens = set(text.split())
    if tokens & _INVENTORY_NOUNS and tokens & _INVENTORY_VERBS:
        return True
    # "tiene"/"tienen" solo con sustantivos inequívocos de listado + dominio técnico
    if tokens & _INVENTORY_NOUNS_LISTING and tokens & _INVENTORY_VERBS_WEAK and any(
        hint in text for hint in TECHNICAL_HINTS
    ):
        return True
    if tokens & _INVENTORY_NOUNS and tokens & _INVENTORY_STRUCTURE:
        return True
    # Typo-tolerant: "docuemntos" → matches "documentos" por prefijo
    if any(_token_resembles_inventory_noun(t) for t in tokens):
        if tokens & _INVENTORY_VERBS:
            return True
        if tokens & _INVENTORY_VERBS_WEAK and any(hint in text for hint in TECHNICAL_HINTS):
            return True
    return False


def _has_strong_business_signal(text: str) -> bool:
    mixed_business_markers = (
        "cliente",
        "importe",
        "presupuesto",
        "produccion",
        "estado",
        "en curso",
        "top",
        "ranking",
    )
    if (
        has_explicit_technical_reference(text)
        and not has_concrete_business_reference(text)
        and not any(marker in text for marker in mixed_business_markers)
    ):
        return False
    if LICITACION_REFERENCE_PATTERN.search(text):
        return True
    if PRODUCCION_CODE_PATTERN.search(text):
        numeric_ref = PRODUCCION_CODE_PATTERN.search(text).group(0)
        if not is_ops_standard_reference(numeric_ref, text):
            return True
    if any(hint in text for hint in BUSINESS_LICITACIONES_HINTS):
        return True
    if any(hint in text for hint in BUSINESS_PRODUCCION_HINTS):
        return True
    if "importe contratado" in text:
        return True
    if "cliente" in text and any(token in text for token in ("tiene", "tenemos", "es", "del cliente")):
        return True
    if any(token in text for token in ("cliente", "estado", "en curso", "top", "ranking")) and any(
        entity in text for entity in ("proyecto", "proyectos", "obra", "obras", "licitacion", "licitaciones", "estudio", "estudios", "oferta", "ofertas")
    ):
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

    if _asks_for_document_inventory(normalized):
        return {"route": "document_inventory", "message": ""}

    business_signal = _has_strong_business_signal(normalized)
    if is_mixed_scope_query(normalized, has_business_signal=business_signal) and not has_concrete_business_reference(normalized):
        return {
            "route": "mixed_scope",
            "message": (
                "La consulta mezcla negocio y documentacion tecnica. "
                "Para responder con precision, separa la parte de negocio y la parte normativa en preguntas distintas."
            ),
        }
    if documentary_or_technical and has_explicit_technical_reference(normalized) and not has_concrete_business_reference(normalized):
        return {"route": "knowledge", "message": ""}

    has_licitacion_reference = bool(LICITACION_REFERENCE_PATTERN.search(normalized))
    has_produccion_code = bool(PRODUCCION_CODE_PATTERN.search(normalized))
    if has_licitacion_reference:
        return {"route": "business_licitaciones", "message": ""}
    if has_produccion_code and not is_ops_standard_reference(PRODUCCION_CODE_PATTERN.search(normalized).group(0), normalized) and not any(hint in normalized for hint in BUSINESS_LICITACIONES_HINTS):
        return {"route": "business_produccion", "message": ""}
    if any(token in normalized for token in ("proyecto", "proyectos", "obra", "obras")) and any(
        token in normalized for token in ("cliente", "en curso", "actualmente", "estado")
    ) and not any(token in normalized for token in ("licitacion", "licitaciones", "oferta", "ofertas", "estudio", "estudios")):
        return {"route": "business_produccion", "message": ""}
    if any(token in normalized for token in ("licitacion", "licitaciones", "estudio", "estudios", "oferta", "ofertas")) and any(
        token in normalized for token in ("recientes", "mas recientes", "relacionadas", "relacionados", "vinculadas", "vinculados", "top", "ranking")
    ):
        return {"route": "business_licitaciones", "message": ""}

    if documentary_or_technical and not business_signal:
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
