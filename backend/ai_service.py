from google import genai
import logging
import re
import time
from typing import Dict, List, Optional, Tuple

from config import GEMINI_API_KEY, GEMINI_MODEL


if not GEMINI_API_KEY:
    raise ValueError("Falta GEMINI_API_KEY en el archivo .env")

client = genai.Client(api_key=GEMINI_API_KEY)
logger = logging.getLogger(__name__)


def _build_prompt(question: str, context: str = "", sources: Optional[List[str]] = None) -> str:
    if context.strip():
        sources_text = ", ".join(sources or [])
        return f"""Eres un asistente tecnico especializado en normativa tecnica espanola.
Responde usando SOLO el contexto proporcionado.
Si hay informacion parcial, responde con lo que si aparece en el contexto.
Solo indica "no hay informacion suficiente" cuando realmente no se pueda extraer nada util.
No incluyas informacion tangencial aunque aparezca en el contexto si no responde de forma directa a la pregunta.
Si introduces una sigla o norma adicional, debe estar explicitamente soportada por el contexto recuperado.
Prioriza datos concretos: valores numericos, limites, condiciones y excepciones.
Si hay cifras, incluyelas textualmente.
Si no hay cifras en el contexto, dilo explicitamente.
Escribe en espanol claro y en 3-6 lineas.
Incluye al final: "Fuentes: ...".

CONTEXTO:
{context}

FUENTES DISPONIBLES:
{sources_text}

PREGUNTA:
{question}
"""
    return (
        "No hay contexto documental disponible para esta consulta. "
        "Indica que no tienes informacion suficiente para responder con base en reglamentos."
    )


def _extract_usage(response) -> Dict[str, int]:
    usage = getattr(response, "usage_metadata", None)
    prompt_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
    completion_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
    total_tokens = int(getattr(usage, "total_token_count", 0) or 0)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def generate_ai_response(question: str, context: str = "", sources: Optional[List[str]] = None) -> Dict:
    prompt = _build_prompt(question=question, context=context, sources=sources)

    last_error = None
    retries = 0
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
            )

            text = getattr(response, "text", None)
            if text and text.strip():
                return {
                    "text": text.strip(),
                    "usage": _extract_usage(response),
                    "model": GEMINI_MODEL,
                    "retries": retries,
                }

            last_error = ValueError("Respuesta vacia del modelo")
        except Exception as exc:
            last_error = exc
            logger.warning("Fallo Gemini (intento %s/3): %s", attempt + 1, str(exc))

        if attempt < 2:
            retries += 1
            time.sleep(1 + attempt)

    logger.error("[ALERT][LLM_ERROR] agotados intentos en Gemini: %s", str(last_error))

    raise RuntimeError(f"No se pudo obtener respuesta de Gemini: {str(last_error)}")


def get_ai_response(question: str, context: str = "", sources: Optional[List[str]] = None) -> str:
    generated = generate_ai_response(question=question, context=context, sources=sources)
    return generated["text"]


def validate_answer(answer: str, context: str, sources: Optional[List[str]]) -> Tuple[str, float]:
    text = (answer or "").strip()
    confidence = 0.0

    if len(text) >= 120:
        confidence += 0.25
    elif len(text) >= 60:
        confidence += 0.15

    source_list = sources or []
    if len(source_list) >= 1:
        confidence += 0.2
        if len(source_list) >= 2:
            confidence += 0.05
        if "fuentes:" not in text.lower():
            text = f"{text}\nFuentes: {', '.join(source_list)}"

    significant_tokens = [
        token for token in re.findall(r"[A-Za-z0-9]{5,}", text.lower())
        if token not in {"fuentes", "respuesta", "pregunta"}
    ]
    context_lower = (context or "").lower()
    overlap_hits = sum(1 for token in significant_tokens[:25] if token in context_lower)
    overlap_ratio = (overlap_hits / max(min(len(significant_tokens), 25), 1)) if significant_tokens else 0.0

    if context and overlap_ratio >= 0.25:
        confidence += 0.3
    elif context and overlap_ratio >= 0.12:
        confidence += 0.2
    elif context:
        confidence += 0.1

    has_numeric_evidence = bool(re.search(r"\b\d+[.,]?\d*\b", text))
    has_reference_evidence = bool(re.search(r"\b(tabla|articulo|itc|pag\.|p[áa]g\.)\b", text.lower()))
    lower_text = text.lower()
    uncertainty_phrases = (
        "no hay informacion suficiente",
        "no hay información suficiente",
        "no hay informacion especifica",
        "no hay información específica",
        "no se menciona",
        "no se relaciona directamente",
        "no se especifica",
        "no se indica",
    )
    uncertainty_signal = any(phrase in lower_text for phrase in uncertainty_phrases)

    if has_numeric_evidence:
        confidence += 0.05
    if has_reference_evidence:
        confidence += 0.05

    # Upper bounds to avoid overconfident scoring without strong evidence.
    cap = 0.9
    if len(source_list) >= 2 and overlap_ratio >= 0.25 and has_reference_evidence:
        cap = 0.95
    if uncertainty_signal:
        cap = min(cap, 0.6)
        confidence = min(confidence, 0.6)

    confidence = min(max(confidence, 0.0), cap)
    return text, confidence
