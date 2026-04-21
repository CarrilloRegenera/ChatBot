import re
from typing import Dict

from config import MIN_QUERY_LENGTH


GREETING_PATTERNS = (
    r"^hola\b",
    r"^buenas\b",
    r"^buenos dias\b",
    r"^buenas tardes\b",
    r"^buenas noches\b",
)

OUT_OF_SCOPE_HINTS = {
    "futbol", "nba", "temperatura", "tiempo", "clima", "horoscopo",
    "chiste", "receta", "videojuego", "pelicula",
}


def classify_question(question: str) -> Dict[str, str]:
    text = (question or "").strip()
    lowered = text.lower()

    if len(text) < MIN_QUERY_LENGTH:
        return {
            "route": "invalid",
            "message": "La consulta es demasiado corta. Anade mas detalle para poder responder con precision.",
        }

    if any(re.search(pattern, lowered) for pattern in GREETING_PATTERNS):
        return {
            "route": "smalltalk",
            "message": "Hola. Puedo ayudarte con consultas tecnicas basadas en la documentacion disponible.",
        }

    if any(hint in lowered for hint in OUT_OF_SCOPE_HINTS):
        return {
            "route": "out_of_scope",
            "message": "Esa pregunta parece fuera del alcance documental actual. Haz una consulta tecnica sobre la documentacion cargada.",
        }

    return {"route": "knowledge", "message": ""}
