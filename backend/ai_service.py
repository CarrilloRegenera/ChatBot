from google import genai
import logging
import time
from typing import List, Optional

from config import GEMINI_API_KEY, GEMINI_MODEL


if not GEMINI_API_KEY:
    raise ValueError("Falta GEMINI_API_KEY en el archivo .env")

client = genai.Client(api_key=GEMINI_API_KEY)
logger = logging.getLogger(__name__)


def get_ai_response(question: str, context: str = "", sources: Optional[List[str]] = None) -> str:
    if context.strip():
        sources_text = ", ".join(sources or [])
        prompt = f"""Eres un asistente tecnico especializado en normativa tecnica espanola.
Responde usando SOLO el contexto proporcionado.
Si hay informacion parcial, responde con lo que si aparece en el contexto.
Solo indica "no hay informacion suficiente" cuando realmente no se pueda extraer nada util.
Escribe la respuesta en espanol claro y en formato breve.
Incluye al final: "Fuentes: ...".

CONTEXTO:
{context}

FUENTES DISPONIBLES:
{sources_text}

PREGUNTA:
{question}
"""
    else:
        prompt = (
            "No hay contexto documental disponible para esta consulta. "
            "Indica que no tienes informacion suficiente para responder con base en reglamentos."
        )

    last_error = None
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
            )

            text = getattr(response, "text", None)
            if text and text.strip():
                return text.strip()

            last_error = ValueError("Respuesta vacia del modelo")
        except Exception as exc:
            last_error = exc
            logger.warning("Fallo Gemini (intento %s/3): %s", attempt + 1, str(exc))

        if attempt < 2:
            time.sleep(1 + attempt)

    raise RuntimeError(f"No se pudo obtener respuesta de Gemini: {str(last_error)}")
