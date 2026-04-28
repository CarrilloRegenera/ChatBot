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
NUMERIC_ASK_PATTERN = re.compile(
    r"\b(cuanto|cuantos|cuantas|valor|valores|limite|limites|maximo|minimo|potencia|resistencia|"
    r"ohm|ohmios|kw|w|v|volt|voltios|a|amper|amperios|ma|mm2|m2|porcentaje|numero)\b",
    re.IGNORECASE,
)
FORMULA_FRAGMENT_PATTERN = re.compile(r"(?:[<>=+\-x*/]\s*)$|(?:\b(?:x|por|entre|igual)\s*)$", re.IGNORECASE)
PARTIAL_SIGNAL_PATTERN = re.compile(r"\b(parcial|parcialmente|no se especifica|no se indica|no se menciona)\b", re.IGNORECASE)
EXPLICIT_SIGNAL_PATTERN = re.compile(r"\b(explicita|explicitamente|segun el contexto|se indica|se establece)\b", re.IGNORECASE)
TRUNCATED_ENDING_PATTERN = re.compile(
    r"(?:\bprev\.?$|\binstal\.?$|\baprox\.?$|\bseg[uú]n\s*$|\bcuando existe\s+\w*$|\bsi existe\s+\w*$)",
    re.IGNORECASE,
)
UNSAFE_LAST_FRAGMENT_PATTERN = re.compile(r"(?:[:;,]\s*$|(?:\b(?:de|del|la|el|y|o|con|para|por)\s*)$)", re.IGNORECASE)


def _build_prompt(question: str, context: str = "", sources: Optional[List[str]] = None, history: Optional[List[Dict]] = None) -> str:
    if context.strip():
        sources_text = ", ".join(sources or [])

        history_section = ""
        if history:
            turns = []
            for turn in history:
                q = (turn.get("question") or "").strip()
                a = (turn.get("response") or "").strip()
                if len(a) > 300:
                    a = a[:300] + "..."
                if q:
                    turns.append(f"Usuario: {q}\nAsistente: {a}")
            if turns:
                history_section = "HISTORIAL RECIENTE:\n" + "\n\n".join(turns) + "\n\n"

        return f"""Eres un asistente tecnico especializado en normativa tecnica espanola.
Tu tarea es responder usando SOLO el contexto proporcionado.
No uses conocimiento externo, no completes huecos y no hagas suposiciones.

Reglas obligatorias:
1. Responde solo a lo preguntado.
2. Usa un tono tecnico y claro.
3. Prioriza valores numericos, limites, condiciones, excepciones, tablas, apartados y requisitos.
4. Si el dato aparece de forma explicita en el contexto, dilo de forma directa.
5. Si solo hay informacion parcial, responde solo con esa parte y deja claro lo que falta.
6. Si el contexto no permite responder de forma suficiente, escribe exactamente: "No hay informacion suficiente en el contexto recuperado".
7. No incluyas informacion tangencial aunque aparezca en el contexto.
8. No menciones normas, ITC, articulos, tablas o conceptos que no aparezcan en el contexto.
9. Si hay cifras en el contexto, copialas textualmente.
10. Si la pregunta pide numeros y el contexto no los contiene, indicalo explicitamente.
11. Si hay varias fuentes y aportan datos distintos o parciales, integralo sin inventar relaciones entre ellas.
12. No copies fragmentos incompletos del contexto; si una frase esta truncada, reformulala solo con la parte segura.

{history_section}Formato de salida obligatorio:
Desarrolla la respuesta con el detalle necesario, normalmente en 4-8 frases. Si la pregunta lo pide, puedes usar una lista corta.
Base documental: indica si la respuesta es explicita o parcial segun el contexto.
Fuentes: {sources_text}

CONTEXTO:
{context}

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


def _trim_unsafe_last_line(line: str) -> str:
    candidate = line.rstrip()
    if not candidate:
        return candidate

    if TRUNCATED_ENDING_PATTERN.search(candidate) or UNSAFE_LAST_FRAGMENT_PATTERN.search(candidate):
        for separator in (". ", "; ", ": ", ", "):
            if separator in candidate:
                candidate = candidate.rsplit(separator, 1)[0].rstrip(" ,;:")
                break

    candidate = re.sub(r"\s+", " ", candidate).strip(" \t,;:")
    return candidate


def postprocess_answer(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return raw

    lines = [line.rstrip() for line in raw.splitlines()]
    processed = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            if processed and processed[-1] != "":
                processed.append("")
            continue

        if idx == len(lines) - 1 and not stripped.lower().startswith("fuentes:"):
            stripped = _trim_unsafe_last_line(stripped)
        else:
            stripped = re.sub(r"\s+", " ", stripped)

        if stripped:
            processed.append(stripped)

    cleaned = "\n".join(processed).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r" {2,}", " ", cleaned)
    return cleaned


_RETRY_WAITS = [2, 5, 10, 20]  # segundos entre intentos (backoff para 503 sostenidos)
_MAX_ATTEMPTS = len(_RETRY_WAITS) + 1


class AIResponseError(RuntimeError):
    def __init__(self, message: str, *, retries: int = 0, transient: bool = False, status_code: Optional[int] = None):
        super().__init__(message)
        self.retries = retries
        self.transient = transient
        self.status_code = status_code


def _extract_status_code(exc: Exception) -> Optional[int]:
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code

    match = re.search(r"\b(\d{3})\b", str(exc))
    if match:
        return int(match.group(1))

    return None


def _is_transient_error(exc: Exception) -> bool:
    status_code = _extract_status_code(exc)
    if status_code in {429, 500, 502, 503, 504}:
        return True

    message = str(exc).lower()
    transient_markers = (
        "unavailable",
        "high demand",
        "temporar",
        "timeout",
        "timed out",
        "deadline exceeded",
        "connection reset",
        "service unavailable",
    )
    return any(marker in message for marker in transient_markers)


def generate_ai_response(question: str, context: str = "", sources: Optional[List[str]] = None, history: Optional[List[Dict]] = None) -> Dict:
    prompt = _build_prompt(question=question, context=context, sources=sources, history=history)

    last_error = None
    retries = 0
    for attempt in range(_MAX_ATTEMPTS):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
            )

            text = getattr(response, "text", None)
            if text and text.strip():
                return {
                    "text": postprocess_answer(text),
                    "usage": _extract_usage(response),
                    "model": GEMINI_MODEL,
                    "retries": retries,
                }

            last_error = ValueError("Respuesta vacia del modelo")
        except Exception as exc:
            last_error = exc
            logger.warning("Fallo Gemini (intento %s/%s): %s", attempt + 1, _MAX_ATTEMPTS, str(exc))

        if attempt < len(_RETRY_WAITS):
            retries += 1
            time.sleep(_RETRY_WAITS[attempt])

    logger.error("[ALERT][LLM_ERROR] agotados intentos en Gemini: %s", str(last_error))
    status_code = _extract_status_code(last_error) if isinstance(last_error, Exception) else None
    transient = _is_transient_error(last_error) if isinstance(last_error, Exception) else False
    raise AIResponseError(
        f"No se pudo obtener respuesta de Gemini: {str(last_error)}",
        retries=retries,
        transient=transient,
        status_code=status_code,
    )


def get_ai_response(question: str, context: str = "", sources: Optional[List[str]] = None, history: Optional[List[Dict]] = None) -> str:
    generated = generate_ai_response(question=question, context=context, sources=sources, history=history)
    return generated["text"]


def validate_answer(question: str, answer: str, context: str, sources: Optional[List[str]]) -> Tuple[str, float]:
    text = postprocess_answer(answer)
    confidence = 0.0
    question_lower = (question or "").lower()
    lower_text = text.lower()
    context_lower = (context or "").lower()

    if len(text) >= 180:
        confidence += 0.25
    elif len(text) >= 90:
        confidence += 0.18
    elif len(text) >= 60:
        confidence += 0.12

    source_list = sources or []
    if len(source_list) >= 1:
        confidence += 0.2
        if len(source_list) >= 2:
            confidence += 0.05
        if "fuentes:" not in lower_text:
            text = f"{text}\nFuentes: {', '.join(source_list)}"
            lower_text = text.lower()

    significant_tokens = [
        token for token in re.findall(r"[A-Za-z0-9]{5,}", lower_text)
        if token not in {"fuentes", "respuesta", "pregunta", "documental"}
    ]
    overlap_hits = sum(1 for token in significant_tokens[:30] if token in context_lower)
    overlap_ratio = (overlap_hits / max(min(len(significant_tokens), 30), 1)) if significant_tokens else 0.0

    if context and overlap_ratio >= 0.3:
        confidence += 0.3
    elif context and overlap_ratio >= 0.18:
        confidence += 0.22
    elif context and overlap_ratio >= 0.1:
        confidence += 0.12
    elif context:
        confidence -= 0.05

    has_numeric_evidence = bool(re.search(r"\b\d+[.,]?\d*\b", text))
    has_reference_evidence = bool(re.search(r"\b(tabla|articulo|itc|pag\.|p[áa]g\.)\b", lower_text))
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
    numeric_question = bool(NUMERIC_ASK_PATTERN.search(question_lower))
    partial_signal = bool(PARTIAL_SIGNAL_PATTERN.search(lower_text)) or "base documental: parcial" in lower_text
    explicit_signal = bool(EXPLICIT_SIGNAL_PATTERN.search(lower_text)) or "base documental: explic" in lower_text
    formula_fragment_signal = any(
        FORMULA_FRAGMENT_PATTERN.search(line.strip())
        for line in text.splitlines()
        if line.strip() and "fuentes:" not in line.lower()
    )

    if has_numeric_evidence:
        confidence += 0.05
    if has_reference_evidence:
        confidence += 0.05
    if explicit_signal and not uncertainty_signal:
        confidence += 0.05
    if partial_signal:
        confidence -= 0.05
    if numeric_question and has_numeric_evidence:
        confidence += 0.05
    elif numeric_question and not has_numeric_evidence and not uncertainty_signal:
        confidence -= 0.2
    if formula_fragment_signal:
        confidence -= 0.15
    if context and overlap_ratio < 0.08:
        confidence -= 0.15

    cap = 0.9
    if len(source_list) >= 2 and overlap_ratio >= 0.25 and has_reference_evidence:
        cap = 0.95
    if numeric_question and not has_numeric_evidence:
        cap = min(cap, 0.7)
    if partial_signal:
        cap = min(cap, 0.75)
    if uncertainty_signal:
        cap = min(cap, 0.6)
        confidence = min(confidence, 0.6)
    if formula_fragment_signal:
        cap = min(cap, 0.7)

    confidence = min(max(confidence, 0.0), cap)
    return text, confidence
