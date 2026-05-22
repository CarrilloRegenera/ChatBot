from openai import OpenAI
import logging
import re
import time
from typing import Dict, List, Optional, Tuple

from config import (
    CONFIDENCE_FALLBACK_THRESHOLD,
    FALLBACK_MAX_BASE_CONFIDENCE,
    FALLBACK_MIN_CONFIDENCE_GAP,
    FALLBACK_MIN_RETRIEVAL_QUALITY,
    LLM_FALLBACK_MODEL,
    LLM_SECONDARY_MODEL,
    OPENAI_API_KEY,
    OPENAI_MAX_RETRIES,
    OPENAI_MODEL,
    OPENAI_TIMEOUT_SECS,
)


if not OPENAI_API_KEY:
    raise ValueError("Falta OPENAI_API_KEY en el archivo .env")

client = OpenAI(
    api_key=OPENAI_API_KEY,
    timeout=OPENAI_TIMEOUT_SECS,
    max_retries=OPENAI_MAX_RETRIES,
)
logger = logging.getLogger(__name__)
NUMERIC_ASK_PATTERN = re.compile(
    r"\b(cuanto|cuantos|cuantas|valor|valores|limite|limites|maximo|minimo|potencia|resistencia|"
    r"ohm|ohmios|kw|w|v|volt|voltios|a|amper|amperios|ma|mm2|m2|porcentaje|numero)\b",
    re.IGNORECASE,
)
FORMULA_FRAGMENT_PATTERN = re.compile(r"(?:[<>=+\-x*/]\s*)$|(?:\b(?:x|por|entre|igual)\s*)$", re.IGNORECASE)
PARTIAL_SIGNAL_PATTERN = re.compile(r"\b(parcial|parcialmente|no se especifica|no se indica|no se menciona)\b", re.IGNORECASE)
EXPLICIT_SIGNAL_PATTERN = re.compile(r"\b(explicita|explicitamente|segun el contexto|se indica|se establece)\b", re.IGNORECASE)
TABLE_INCOMPLETE_SIGNAL_PATTERN = re.compile(
    r"\b(no hay informacion suficiente|no se incluye la tabla|no se incluye el listado|no se puede reconstruir la lista|contexto parcial)\b",
    re.IGNORECASE,
)
PARTIAL_WITH_DETAIL_PATTERN = re.compile(
    r"(?:\bno hay informacion suficiente\b|\bfalta informacion\b|\bno se aporta\b).*(?:\blo que si\b|\bsin embargo\b|\bademas\b)",
    re.IGNORECASE | re.DOTALL,
)
TRUNCATED_ENDING_PATTERN = re.compile(
    r"(?:\bprev\.?$|\binstal\.?$|\baprox\.?$|\bseg[uú]n\s*$|\bcuando existe\s+\w*$|\bsi existe\s+\w*$)",
    re.IGNORECASE,
)
UNSAFE_LAST_FRAGMENT_PATTERN = re.compile(
    r"(?:[:;,]\s*$|(?:\b(?:a|al|de|del|en|la|el|los|las|un|una|y|o|con|para|por|que|su|sus|no|ni|sino|aunque|como|cuando|donde|si)\s*)$)",
    re.IGNORECASE,
)
MARKDOWN_PATTERN = re.compile(r"\*{1,3}([^*\n]*)\*{1,3}|\*+|^#{1,6}\s+", re.MULTILINE)
DEFINITION_ASK_PATTERN = re.compile(
    r"(?:\bcomo\s+se\s+denomina\b|\bque\s+es\b|\bque\s+se\s+entiende\s+por\b|\bdefinicion\b|"
    r"\bque\s+funcion\s+cumple[n]?\b|\bpara\s+que\s+sirve[n]?\b|\bcual\s+es\s+su\s+funcion\b|"
    r"\bque\s+papel\s+cumple[n]?\b)",
    re.IGNORECASE,
)
LIST_ASK_PATTERN = re.compile(
    r"(?:\bcuales\s+son\b|\benumera\b|\blista\b|\btipos\s+de\b|\bclases\s+de\b|\bpueden\s+ser\b)",
    re.IGNORECASE,
)
SUMMARY_ASK_PATTERN = re.compile(
    r"(?:\bresume\b|\bresumen\b|\bresumir\b|\bsintetiza\b|\bsintesis\b)",
    re.IGNORECASE,
)
TABLE_ASK_PATTERN = re.compile(
    r"(?:\btabla\b|\bcircuitos?\s+minimos?\b|\bcircuitos?\s+mínimos?\b|\brelacion\s+de\b|\brelación\s+de\b|\blista\s+completa\b)",
    re.IGNORECASE,
)
COMPARISON_ASK_PATTERN = re.compile(
    r"(?:\bdiferencia\b|\bdiferencias\b|\bcompara\b|\bcomparar\b|\bfrente\s+a\b|\bversus\b)",
    re.IGNORECASE,
)
PROCEDURE_ASK_PATTERN = re.compile(
    r"(?:\bcomo\s+se\s+calcula\b|\bcomo\s+debe\b|\bcomo\s+puede\b|\bprocedimiento\b|\bpasos\b)",
    re.IGNORECASE,
)
GENERALIZATION_ASK_PATTERN = re.compile(
    r"(?:\ben\s+general\b|\bprincipales\b|\bcriterios?\b|\brequisitos?\b|\bexcepciones?\b|"
    r"\balcance\b|\baplicacion\b|\baplicación\b|\bfuncion\b|\bfunción\b|\bfinalidad\b|"
    r"\bobjetivo\b|\bcambios?\b|\bmodifica\b|\bintroduce\b|\bafecta\b|\bimplica\b)",
    re.IGNORECASE,
)
MOTIVATION_ASK_PATTERN = re.compile(
    r"(?:\bpor\s+que\b|\bpor\s+qué\b|\bmotivo\b|\bjustificacion\b|\bjustificación\b|"
    r"\bexposicion\s+de\s+motivos\b|\bexposición\s+de\s+motivos\b|\bpreambulo\b|\bpreámbulo\b)",
    re.IGNORECASE,
)
DIRECT_FACT_ASK_PATTERN = re.compile(
    r"(?:\bcomo\s+se\s+denomina\b|\bque\s+es\b|\bcual\s+es\b|\bcuales\s+son\b|\bcuanto\b|\bcuantos\b|\bcuantas\b)",
    re.IGNORECASE,
)
DEFINITION_WEAK_OPENING_PATTERN = re.compile(
    r"^(?:el contexto recuperado indica que|segun el contexto|la documentacion indica que)\b",
    re.IGNORECASE,
)
DEFINITION_DIRECT_ANSWER_PATTERN = re.compile(
    r"^(?:se denomina|es|se define como|recibe el nombre de)\b",
    re.IGNORECASE,
)
VAGUE_DEFINITION_PATTERN = re.compile(
    r"\b(?:es una caracteristica|es un concepto|es un valor|hace referencia a|se refiere a una caracteristica)\b",
    re.IGNORECASE,
)
LIST_FORMAT_PATTERN = re.compile(r"^(?:[-*]\s+|\d+\.\s+)", re.MULTILINE)
LIST_MARKER_PATTERN = re.compile(r"^(?:[-*•]\s+|\d+[\.\)]\s+|[a-z][\.\)]\s+)", re.IGNORECASE)
TRAILING_METADATA_PATTERN = re.compile(
    r"(?:(?:^|\n)[ \t]*|(?<=[.!?])\s+)(?:Base documental:|Fuentes:).*",
    re.IGNORECASE | re.DOTALL,
)
TRAILING_FRAGMENT_REF_PATTERN = re.compile(
    r"\b("
    r"primera|segunda|tercera|cuarta|quinta|sexta|septima|séptima|octava|novena|decima|décima|"
    r"disposicion\s+(?:transitoria|adicional|final)\s+(?:primera|segunda|tercera|cuarta|quinta|sexta|septima|séptima|octava|novena|decima|décima)|"
    r"disposición\s+(?:transitoria|adicional|final)\s+(?:primera|segunda|tercera|cuarta|quinta|sexta|septima|séptima|octava|novena|decima|décima)|"
    r"apartado|parrafo|párrafo"
    r")\.\s*\d+\.$",
    re.IGNORECASE,
)
PROMPT_LEAK_LINE_PATTERN = re.compile(
    r"^(?:wait,\s*rule\b|reglas?\s+obligatorias:|formato\s+de\s+salida\s+obligatorio:|contexto:|pregunta:|historial\s+reciente:)\b",
    re.IGNORECASE,
)
PROMPT_LEAK_INLINE_PATTERN = re.compile(r"wait,\s*rule\s*\d+.*", re.IGNORECASE)
LABELED_TERM_PATTERNS = {
    "clase": re.compile(r"\bclase\s+(i{1,3}|iv|v|vi|vii|viii|ix|x|\d+|[a-z])\b", re.IGNORECASE),
    "tipo": re.compile(r"\btipo\s+([a-z0-9]+)\b", re.IGNORECASE),
    "categoria": re.compile(r"\bcategor(?:ia|ía)\s+([a-z0-9]+)\b", re.IGNORECASE),
    "grado_ip": re.compile(r"\bip\s?(\d{2}[a-z]?)\b", re.IGNORECASE),
    "grado_ik": re.compile(r"\bik\s?(\d{2})\b", re.IGNORECASE),
    "esquema": re.compile(r"\b(tt|tn(?:-?[sc])?|it)\b", re.IGNORECASE),
}
MULTI_ITEM_ASK_PATTERN = re.compile(
    r"(?:\bclases\s+de\b|\btipos\s+de\b|\bcuales\s+son\b|\btodos?\s+los\b|\btodas?\s+las\b)",
    re.IGNORECASE,
)
SCOPE_ASK_PATTERN = re.compile(
    r"(?:\bobjeto\b|\bambito\b|\bámbito\b|\bfinalidad\b|\bfuncion\b|\bfunción\b|\bpapel\b|\balcance\b)",
    re.IGNORECASE,
)
INCOMPLETE_EVIDENCE_PATTERN = re.compile(
    r"(?:\bsolo\b.{0,40}\b(aparece|indica|menciona|consta)\b|\bno\s+aparece\b|\bno\s+se\s+incluye\b|\bno\s+se\s+describe\b)",
    re.IGNORECASE,
)
NARROW_SUBSECTION_PATTERN = re.compile(
    r"\b(itc[-\s]*[a-z]{1,4}[-\s]*\d+|art(?:iculo)?\.?\s*\d+|tabla\s*\d+)\b",
    re.IGNORECASE,
)
# Términos que, si aparecen en el contexto recuperado, indican que el alcance
# de la regla está condicionado. La respuesta debería reflejarlos para no
# convertir un caso particular en regla general (objetivo principal del bot).
QUALIFIER_CONTEXT_PATTERN = re.compile(
    r"\b(excepci(?:on|ón|ones|ónes)|salvo|exceptuando|no\s+se\s+aplica|no\s+aplicara?|no\s+aplican|"
    r"no\s+afecta|quedan?\s+exclu(?:ido|idas|idos)|"
    r"vigenci[ao]|deroga(?:do|ción|cion)|transitor[io]a?|"
    r"siempre\s+que|cuando\s+(?:se|exista|el|la|los|las)|condicion(?:ado|ada|es|adas)|"
    r"requisito|l[ií]mite(?:s)?|m[áa]xim[oa]s?|m[íi]nim[oa]s?|tope|umbral|"
    r"ámbito\s+de\s+aplicacion|ambito\s+de\s+aplicacion|alcance)\b",
    re.IGNORECASE,
)
# Detección de qualifiers en la respuesta. Más permisiva — basta que el modelo
# mencione cualquier indicio de condición/excepción para considerar que
# preservó la matización.
QUALIFIER_ANSWER_PATTERN = re.compile(
    r"\b(salvo|excepci(?:on|ón|ones|ónes)|exceptuando|no\s+se\s+aplica|no\s+aplica|"
    r"siempre\s+que|cuando|condicion(?:ado|ada|es|adas)|requisito|l[ií]mite(?:s)?|"
    r"m[áa]xim[oa]s?|m[íi]nim[oa]s?|tope|umbral|vigenci[ao]|"
    r"ámbito|ambito|alcance|aplicable\s+(?:a|cuando|para))\b",
    re.IGNORECASE,
)


def _missing_qualifiers_signal(answer_text: str, context_text: str) -> bool:
    """True si el contexto trae matizadores (excepción/condición/ámbito/vigencia/
    límite) pero la respuesta no los refleja. Detecta el patrón de "convertir
    un caso particular en regla general" sin mencionar las restricciones.
    """
    if not context_text or not answer_text:
        return False
    if not QUALIFIER_CONTEXT_PATTERN.search(context_text):
        return False
    return not QUALIFIER_ANSWER_PATTERN.search(answer_text)


def _infer_answer_profile(question: str) -> Dict[str, object]:
    q = (question or "").strip()
    return {
        "definition": bool(DEFINITION_ASK_PATTERN.search(q)),
        "list": bool(LIST_ASK_PATTERN.search(q)),
        "summary": bool(SUMMARY_ASK_PATTERN.search(q)),
        "table": bool(TABLE_ASK_PATTERN.search(q)),
        "comparison": bool(COMPARISON_ASK_PATTERN.search(q)),
        "procedure": bool(PROCEDURE_ASK_PATTERN.search(q)),
        "generalization": bool(GENERALIZATION_ASK_PATTERN.search(q)),
        "motivation": bool(MOTIVATION_ASK_PATTERN.search(q)),
        "numeric": bool(NUMERIC_ASK_PATTERN.search(q)),
        "direct_fact": bool(DIRECT_FACT_ASK_PATTERN.search(q)),
    }


def _build_output_instruction(profile: Dict[str, object]) -> str:
    if profile["table"]:
        return (
            "Si el contexto contiene la tabla o el listado completo, enumera todos los elementos recuperados en una lista numerada 1. 2. 3. "
            "Si el contexto solo remite a una tabla sin mostrar su contenido completo, indicalo claramente y no inventes los elementos que faltan."
        )
    if profile["summary"] and profile["list"]:
        return (
            "Resume el conjunto completo en una lista numerada 1. 2. 3. "
            "Incluye cada clase, tipo o elemento distinto que aparezca en el contexto con una descripcion breve por linea."
        )
    if profile["summary"]:
        return (
            "Resume solo las ideas principales del contexto, normalmente en 3-6 lineas breves. "
            "Sintetiza sin copiar fragmentos largos ni dejar frases truncadas, y separa lo principal de lo accesorio."
        )
    if profile["definition"]:
        return (
            "Responde de forma breve y directa, normalmente en 1-3 frases. "
            "Si la pregunta pide funcion, papel o finalidad, explica primero para que sirve en la practica y luego cita el alcance concreto que aparezca en el contexto."
        )
    if profile["motivation"]:
        return (
            "Explica el motivo o justificacion normativa en 2-5 frases. "
            "Prioriza preambulos, exposiciones de motivos, objetivos y referencias a adaptacion normativa; no lo sustituyas por tramites formales salvo que sea lo unico recuperado."
        )
    if profile["numeric"]:
        return (
            "Responde de forma directa y precisa, normalmente en 1-3 frases. "
            "Prioriza los valores, limites, unidades y condiciones asociadas."
        )
    if profile["list"]:
        return (
            "Responde con un elemento por linea en formato numerado simple 1. 2. 3. cuando el contexto enumere varios casos. "
            "No repitas el enunciado y no anadas categorias no presentes en el contexto."
        )
    if profile["comparison"]:
        return (
            "Responde comparando solo los puntos relevantes que aparezcan en el contexto. "
            "Destaca diferencias concretas sin inventar relaciones."
        )
    if profile["procedure"]:
        return (
            "Responde de forma ordenada y operativa. "
            "Si el contexto lo permite, presenta condiciones o pasos de forma breve."
        )
    return "Desarrolla la respuesta con el detalle necesario, normalmente en 2-5 frases. Si la pregunta lo pide, puedes usar una lista corta."


def _build_prompt(question: str, context: str = "", history: Optional[List[Dict]] = None) -> str:
    if context.strip():
        profile = _infer_answer_profile(question)
        definition_hint = ""
        if profile["definition"]:
            definition_hint = (
                "- Si la pregunta pide como se denomina o una definicion, empieza por el termino exacto en la primera frase.\n"
                "- Si la pregunta pide funcion, papel o finalidad, responde con la funcion practica dentro del reglamento; no te limites a decir que forma parte de el.\n"
                "- En preguntas definicionales, evita respuestas vagas como 'es una caracteristica' si el contexto permite nombrar el termino.\n"
            )
        intent_hint = ""
        if profile["list"]:
            intent_hint += (
                "- Si la pregunta pide tipos, clases o enumeraciones, devuelve todos los elementos recuperados que respondan a la pregunta.\n"
                "- En preguntas de lista, escribe un elemento por linea con numeracion simple 1. 2. 3. y evita repetir el enunciado.\n"
            )
        if profile["summary"]:
            intent_hint += (
                "- Si la pregunta pide un resumen, sintetiza el contenido relevante en vez de copiar un unico fragmento.\n"
                "- Si el resumen trata sobre clases, tipos o categorias, incluye todos los elementos distintos presentes en el contexto con una descripcion breve de cada uno.\n"
                "- No devuelvas una sola clase o un solo fragmento si la pregunta pide un conjunto en plural, salvo que el contexto solo contenga ese unico elemento.\n"
            )
        if profile["table"]:
            intent_hint += (
                "- Si la pregunta depende de una tabla o listado completo, solo enumera los elementos si aparecen de forma recuperada y legible en el contexto.\n"
                "- Si el contexto menciona la tabla pero no contiene su contenido completo, di expresamente que no se puede reconstruir la lista completa con seguridad.\n"
                "- Evita responder con un unico elemento aislado si la pregunta pide un conjunto completo.\n"
                "- Si el contexto contiene lineas que empiezan por FILA_TABLA, tratalas como datos estructurados: respeta la relacion columna-valor y no reasignes valores a otra columna.\n"
            )
        if profile["comparison"]:
            intent_hint += "- Si la pregunta compara conceptos, responde alineando diferencias o similitudes relevantes sin extenderte.\n"
        if profile["motivation"]:
            intent_hint += (
                "- Si la pregunta pide por que se aprobo una norma, prioriza la justificacion, finalidad o adaptacion normativa del preambulo; "
                "no respondas solo con tramites administrativos salvo que no haya otro dato.\n"
            )
        if profile["procedure"]:
            intent_hint += "- Si la pregunta pide como actuar o calcular algo, ordena la respuesta segun condiciones o pasos presentes en el contexto.\n"
        if profile["numeric"]:
            intent_hint += (
                "- Si la pregunta busca un valor o limite, prioriza la cifra exacta, su unidad y la condicion aplicable.\n"
                "- Si el valor procede de una fila de tabla, menciona el criterio de fila y columna que lo soporta, por ejemplo material, seccion, caso o ambito.\n"
            )
        if profile["generalization"]:
            intent_hint += (
                "- Si la pregunta pide una sintesis, alcance, funcion, criterio, cambio o consecuencia, generaliza solo a partir de hechos repetidos o explicitamente conectados en el contexto.\n"
                "- Cuando generalices, conserva las condiciones, excepciones y limites que aparezcan; no conviertas un caso particular en regla general.\n"
            )

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

        output_instruction = _build_output_instruction(profile)

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
13. Si el contexto contiene expresiones como "segun se indica mas adelante" o referencias internas incompletas, no las copies literalmente; resume la condicion recuperada y aclara si falta el detalle posterior.
14. No conviertas un requisito de una ITC, tabla, emplazamiento o caso concreto en requisito general de todas las instalaciones.
15. Si el contexto recuperado trae varios fragmentos complementarios del mismo apartado, integralo en una unica respuesta; no digas que no hay informacion suficiente solo porque un fragmento aislado sea parcial.
16. Si aparecen filas estructuradas "FILA_TABLA", usa cada par columna: valor como fuente prioritaria para consultas numericas o tabulares; no mezcles columnas de filas distintas.
17. Si el usuario menciona una pagina, tabla, apartado o ITC concreta y el contexto la contiene, responde desde esa referencia antes que desde fragmentos parecidos.
18. Responde en texto plano. No uses formato markdown: sin asteriscos ni cabeceras. Si la respuesta es una lista, usa lineas numeradas simples 1. 2. 3.; si no es una lista, escribe frases completas que terminen con punto.
19. Maxima precision: distingue entre dato literal, sintesis razonable y falta de evidencia. No presentes una inferencia como si fuera una cita literal.
20. Generalizacion controlada: puedes sintetizar una regla, funcion o criterio comun solo cuando el contexto lo soporte; conserva siempre condiciones, excepciones, ambito y vigencia.
{definition_hint}
{intent_hint}

{history_section}Formato de salida obligatorio:
{output_instruction}
Texto plano unicamente. No anadas lineas finales de metadatos como "Base documental" o "Fuentes".

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
    usage = getattr(response, "usage", None)
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _extract_response_text(response) -> str:
    choices = getattr(response, "choices", None) or []
    if choices:
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if content and str(content).strip():
            return str(content).strip()
    return ""


def _describe_empty_response(response) -> str:
    choices = getattr(response, "choices", None) or []
    if choices:
        finish_reason = getattr(choices[0], "finish_reason", None)
        if finish_reason:
            return f"Respuesta vacia del modelo (finish_reason={finish_reason})"
    return "Respuesta vacia del modelo"


def _trim_unsafe_last_line(line: str) -> str:
    candidate = line.rstrip()
    if not candidate:
        return candidate

    candidate = TRAILING_FRAGMENT_REF_PATTERN.sub(r"\1.", candidate)

    # Mid-word cut: ends with a letter but no sentence terminator → trim to last sentence
    if candidate[-1].isalpha() and not re.search(r"[.!?]\s*$", candidate):
        for sep in (". ", "? ", "! ", "; "):
            if sep in candidate:
                candidate = candidate.rsplit(sep, 1)[0] + sep.rstrip()
                break

    if TRUNCATED_ENDING_PATTERN.search(candidate) or UNSAFE_LAST_FRAGMENT_PATTERN.search(candidate):
        trimmed = False
        for separator in (". ", "? ", "! ", "; ", ": ", ", "):
            if separator in candidate:
                candidate = candidate.rsplit(separator, 1)[0].rstrip(" ,;:")
                trimmed = True
                break
        if not trimmed:
            candidate = re.sub(r"\s+\S+\s*$", "", candidate).rstrip(" ,;:")

    candidate = re.sub(r"\s+", " ", candidate).strip(" \t,;:")
    return candidate


def _capitalize_list_item(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return cleaned
    return cleaned[:1].upper() + cleaned[1:]


def _count_structured_items(text: str) -> int:
    non_empty = [line.strip() for line in (text or "").splitlines() if line.strip()]
    numbered = sum(1 for line in non_empty if LIST_MARKER_PATTERN.match(line))
    if numbered:
        return numbered
    # Fallback: count short non-empty lines after a heading line if present
    if len(non_empty) >= 2 and non_empty[0].endswith(":"):
        return len(non_empty[1:])
    return len(non_empty)


def _normalize_list_answer(question: str, lines: List[str]) -> Optional[str]:
    profile = _infer_answer_profile(question)
    if not profile["list"]:
        return None

    non_empty = [line.strip() for line in lines if line.strip()]
    if len(non_empty) < 2:
        return None

    heading = ""
    item_lines = non_empty
    if non_empty[0].endswith(":"):
        heading = non_empty[0]
        item_lines = non_empty[1:]
    elif len(non_empty) >= 3 and non_empty[0].rstrip(".:").lower() == (question or "").strip().rstrip(".:").lower():
        heading = non_empty[0].rstrip(".") + ":"
        item_lines = non_empty[1:]

    if len(item_lines) < 2:
        return None

    normalized_items = []
    for line in item_lines:
        item = LIST_MARKER_PATTERN.sub("", line.strip())
        item = re.sub(r"\s+", " ", item).strip(" \t-")
        if not item:
            continue
        item = _capitalize_list_item(item)
        if item[-1] not in ".!?":
            item += "."
        normalized_items.append(item)

    if len(normalized_items) < 2:
        return None

    numbered_items = [f"{idx}. {item}" for idx, item in enumerate(normalized_items, start=1)]
    if heading:
        return "\n".join([heading] + numbered_items)
    return "\n".join(numbered_items)


def postprocess_answer(text: str, question: str = "") -> str:
    raw = (text or "").strip()
    if not raw:
        return raw

    # Strip markdown bold/italic/headings before any other processing
    raw = re.sub(r'\*{2,3}([^*\n]*)\*{2,3}', r'\1', raw)  # **bold** / ***text*** → text
    raw = re.sub(r'\*([^*\n]+)\*', r'\1', raw)             # *italic* → text
    raw = re.sub(r'\*+', '', raw)                           # remaining stray asterisks
    raw = re.sub(r'^#{1,6}\s+', '', raw, flags=re.MULTILINE)  # ## headings → plain text

    raw = TRAILING_METADATA_PATTERN.sub("", raw).strip()
    raw = PROMPT_LEAK_INLINE_PATTERN.sub("", raw).strip()

    lines = [line.rstrip() for line in raw.splitlines()]
    processed = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            if processed and processed[-1] != "":
                processed.append("")
            continue
        if PROMPT_LEAK_LINE_PATTERN.match(stripped):
            continue

        if idx == len(lines) - 1 and not stripped.lower().startswith("fuentes:"):
            stripped = _trim_unsafe_last_line(stripped)
        else:
            stripped = re.sub(r"\s+", " ", stripped)

        if stripped:
            processed.append(stripped)

    normalized_list = _normalize_list_answer(question, processed)
    cleaned = normalized_list if normalized_list else "\n".join(processed).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r" {2,}", " ", cleaned)
    return cleaned


def _infer_document_basis(answer: str) -> str:
    lower_text = (answer or "").lower()
    if any(
        phrase in lower_text
        for phrase in (
            "no hay informacion suficiente",
            "no se menciona",
            "no se especifica",
            "no se indica",
            "informacion parcial",
            "información parcial",
        )
    ):
        return "parcial"
    if PARTIAL_SIGNAL_PATTERN.search(lower_text):
        return "parcial"
    return "explicita"


def _extract_labeled_terms(text: str) -> Dict[str, set[str]]:
    extracted: Dict[str, set[str]] = {}
    for family, pattern in LABELED_TERM_PATTERNS.items():
        matches = {
            re.sub(r"\s+", "", match.group(1).lower())
            for match in pattern.finditer(text or "")
            if match.group(1)
        }
        if matches:
            extracted[family] = matches
    return extracted


def _find_labeled_term_conflicts(question: str, answer: str, context: str) -> List[str]:
    answer_terms = _extract_labeled_terms(answer)
    if not answer_terms:
        return []

    question_terms = _extract_labeled_terms(question)
    context_terms = _extract_labeled_terms(context)
    conflicts = []

    for family, answer_values in answer_terms.items():
        allowed_values = set()
        allowed_values.update(question_terms.get(family, set()))
        allowed_values.update(context_terms.get(family, set()))
        if not allowed_values:
            continue

        unexpected = answer_values - allowed_values
        if unexpected:
            conflicts.append(f"{family}:{', '.join(sorted(unexpected))}")

    return conflicts


def _split_context_chunks(context: str) -> List[str]:
    return [chunk.strip() for chunk in re.split(r"\n\s*\n", context or "") if chunk.strip()]


def _extract_focus_terms(question: str, limit: int = 6) -> List[str]:
    stopwords = {
        "como", "cual", "cuales", "cuanto", "cuantos", "cuantas", "debe", "deben",
        "puede", "pueden", "material", "clase", "tipo", "categoria", "categoría",
        "aislamiento", "reforzado", "doble", "con", "por", "para", "del", "de", "la", "el",
        "define", "definicion", "definiciÃ³n", "denomina",
    }
    terms = []
    seen = set()
    for token in re.findall(r"[A-Za-z0-9]{4,}", (question or "").lower()):
        if token in stopwords or token in seen:
            continue
        seen.add(token)
        terms.append(token)
        if len(terms) >= limit:
            break
    return terms


def _has_chunk_support_for_labeled_terms(question: str, answer: str, context: str) -> bool:
    answer_terms = _extract_labeled_terms(answer)
    if not answer_terms:
        return True

    chunks = _split_context_chunks(context)
    if not chunks:
        return True

    focus_terms = _extract_focus_terms(question)
    for family, values in answer_terms.items():
        for value in values:
            supported = False
            for chunk in chunks:
                chunk_terms = _extract_labeled_terms(chunk)
                if value not in chunk_terms.get(family, set()):
                    continue

                chunk_lower = chunk.lower()
                focus_hits = sum(1 for term in focus_terms if term in chunk_lower)
                if focus_hits >= 1 or not focus_terms:
                    supported = True
                    break
            if not supported:
                return False

    return True


def _extract_answer_terms(answer: str, limit: int = 10) -> List[str]:
    stopwords = {
        "respuesta", "documental", "fuentes", "explicita", "explicita", "contexto",
        "segun", "según", "material", "clase", "tipo", "categoria", "categoría",
        "debe", "deben", "puede", "pueden", "sera", "será", "igual", "superior",
    }
    terms = []
    seen = set()
    for token in re.findall(r"[A-Za-z0-9]{4,}", (answer or "").lower()):
        if token in stopwords or token in seen:
            continue
        seen.add(token)
        terms.append(token)
        if len(terms) >= limit:
            break
    return terms


def _best_chunk_support_score(question: str, answer: str, context: str) -> float:
    chunks = _split_context_chunks(context)
    if not chunks:
        return 0.0

    focus_terms = _extract_focus_terms(question)
    answer_terms = _extract_answer_terms(answer)
    answer_labels = _extract_labeled_terms(answer)
    answer_numbers = set(re.findall(r"\b\d+[.,]?\d*\b", answer or ""))

    best_score = 0.0
    for chunk in chunks:
        chunk_lower = chunk.lower()
        focus_hits = sum(1 for term in focus_terms if term in chunk_lower)
        answer_hits = sum(1 for term in answer_terms if term in chunk_lower)
        number_hits = sum(1 for value in answer_numbers if value in chunk)
        label_hits = 0
        chunk_labels = _extract_labeled_terms(chunk)
        for family, values in answer_labels.items():
            label_hits += sum(1 for value in values if value in chunk_labels.get(family, set()))

        score = 0.0
        if focus_terms:
            score += min(focus_hits / max(len(focus_terms), 1), 1.0) * 0.45
        if answer_terms:
            score += min(answer_hits / max(len(answer_terms), 1), 1.0) * 0.35
        if answer_numbers:
            score += min(number_hits / max(len(answer_numbers), 1), 1.0) * 0.10
        if answer_labels:
            total_labels = sum(len(values) for values in answer_labels.values())
            score += min(label_hits / max(total_labels, 1), 1.0) * 0.10

        best_score = max(best_score, score)

    if len(focus_terms) <= 1 and best_score < 0.35:
        lexical_focus = focus_terms[0] if focus_terms else ""
        if lexical_focus:
            for chunk in chunks:
                chunk_lower = chunk.lower()
                if lexical_focus in chunk_lower:
                    overlap_hits = sum(1 for term in answer_terms[:4] if term in chunk_lower)
                    if overlap_hits >= 1:
                        best_score = max(best_score, 0.46)

    return round(best_score, 4)


def _has_critical_term_conflict(question: str, answer: str, context: str) -> bool:
    profile = _infer_answer_profile(question)
    if not (profile["definition"] or profile["direct_fact"] or profile["numeric"]):
        return False
    if _find_labeled_term_conflicts(question, answer, context):
        return True
    return not _has_chunk_support_for_labeled_terms(question, answer, context) and _best_chunk_support_score(question, answer, context) < 0.25


def format_answer_for_user(answer: str, sources: Optional[List[str]], question: str = "") -> str:
    clean_answer = postprocess_answer(answer, question=question)
    return clean_answer


_RETRY_WAITS = [2, 5, 10, 20]  # segundos entre intentos (backoff para errores transitorios)
_MAX_ATTEMPTS = len(_RETRY_WAITS) + 1
_MAX_CONSECUTIVE_503 = 3  # tras 3 errores 503 seguidos se abandona el modelo para ir al fallback


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


def _record_model_avail_error_event(model: str, status_code: int) -> None:
    try:
        from memory_service import record_model_error_event
        error_kind = f"http_{status_code}"
        record_model_error_event(model, status_code, error_kind=error_kind, origin="generate_ai_response")
    except Exception:
        logger.exception("No se pudo registrar el error %s del modelo %s", status_code, model)


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


_COMPLEX_SIGNALS = re.compile(
    r"\b(diferencia|diferencias|compara|comparar|explica|detalla|relaciona|enumera|resume|todos los|cuales son todos)\b",
    re.IGNORECASE,
)


def _is_complex_question(question: str) -> bool:
    q = (question or "").strip()
    has_complex_signal = bool(_COMPLEX_SIGNALS.search(q))
    is_very_long = len(q) > 220
    is_multi_part = q.count("?") > 1
    is_long_and_analytical = len(q) > 150 and has_complex_signal
    return is_very_long or is_multi_part or is_long_and_analytical


def _effective_confidence_threshold(question: str) -> float:
    profile = _infer_answer_profile(question)
    threshold = CONFIDENCE_FALLBACK_THRESHOLD
    if profile["comparison"] or profile["summary"] or profile["table"]:
        threshold -= 0.08
    elif profile["list"] or profile["procedure"]:
        threshold -= 0.06
    elif profile["definition"] or profile["direct_fact"]:
        threshold -= 0.04
    return max(0.5, round(threshold, 2))


def _retrieval_quality(retrieval_stats: Optional[Dict[str, object]]) -> str:
    if not retrieval_stats:
        return "unknown"
    domain_match_ratio = float(retrieval_stats.get("domain_match_ratio", 1.0) or 0.0)
    source_diversity = int(retrieval_stats.get("source_diversity", 0) or 0)
    selected_count = int(retrieval_stats.get("selected_count", 0) or 0)
    expected_domains = retrieval_stats.get("expected_domains") or []
    if selected_count == 0:
        return "empty"
    if expected_domains and domain_match_ratio < 0.34:
        return "poor"
    if source_diversity == 0:
        return "poor"
    if domain_match_ratio >= 0.67 or not expected_domains:
        return "good"
    return "mixed"


def _should_escalate(
    question: str,
    base_text: str,
    confidence: float,
    confidence_threshold: float,
    primary_has_conflict: bool,
    retrieval_stats: Optional[Dict[str, object]],
    context: str = "",
) -> Optional[str]:
    lower_text = (base_text or "").lower()
    retrieval_quality = _retrieval_quality(retrieval_stats)
    table_coverage_ratio = float((retrieval_stats or {}).get("table_coverage_ratio", 1.0) or 0.0)
    profile = _infer_answer_profile(question)
    retrieval_rank = {"empty": 0, "poor": 1, "mixed": 2, "good": 3}
    required_quality_rank = retrieval_rank.get(FALLBACK_MIN_RETRIEVAL_QUALITY, 2)
    current_quality_rank = retrieval_rank.get(retrieval_quality, 0)
    confidence_gap = max(0.0, confidence_threshold - confidence)
    explicit_insufficient = "no hay informacion suficiente" in lower_text
    partial_with_detail = bool(PARTIAL_WITH_DETAIL_PATTERN.search(base_text or ""))
    missing_qualifiers = _missing_qualifiers_signal(base_text or "", context or "")
    scope_or_general = bool(SCOPE_ASK_PATTERN.search(question or "")) or bool(profile.get("generalization"))

    if primary_has_conflict and confidence < max(0.45, confidence_threshold - 0.1):
        return "conflict"
    if explicit_insufficient and retrieval_quality in {"good", "mixed"}:
        return "insufficient_with_context"
    if partial_with_detail and retrieval_quality in {"good", "mixed"} and confidence >= max(0.72, confidence_threshold):
        return "partial_high_confidence"
    # Si el contexto tiene matizadores (excepciones/ámbito/vigencia/límites) y la
    # respuesta los omite con confianza alta, escalamos: el modelo secundario
    # tiende a ser más conservador citando condiciones.
    if missing_qualifiers and scope_or_general and retrieval_quality in {"good", "mixed"} \
       and confidence >= max(0.7, confidence_threshold):
        return "missing_qualifiers"
    if profile.get("table") and table_coverage_ratio < 0.4 and retrieval_quality in {"good", "mixed"}:
        return "table_low_coverage"
    if current_quality_rank < required_quality_rank:
        return None
    if _is_complex_question(question) and confidence < confidence_threshold:
        return "complex_low_confidence"
    # Escala por baja confianza de base, pero con freno para evitar sobreuso:
    # 1) solo si esta realmente por debajo del umbral (gap minimo)
    # 2) solo si la confianza absoluta no es ya alta
    if confidence_gap >= FALLBACK_MIN_CONFIDENCE_GAP and confidence <= FALLBACK_MAX_BASE_CONFIDENCE:
        return "low_confidence"
    return None


def generate_ai_response(question: str, context: str = "", history: Optional[List[Dict]] = None, *, model: Optional[str] = None) -> Dict:
    prompt = _build_prompt(question=question, context=context, history=history)
    active_model = model or OPENAI_MODEL

    last_error = None
    retries = 0
    consecutive_avail_errors = 0
    for attempt in range(_MAX_ATTEMPTS):
        try:
            logger.info(
                "[LLM_REQUEST] model=%s attempt=%s/%s timeout=%ss prompt_chars=%s",
                active_model,
                attempt + 1,
                _MAX_ATTEMPTS,
                OPENAI_TIMEOUT_SECS,
                len(prompt),
            )
            response = client.chat.completions.create(
                model=active_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_completion_tokens=2048,
            )
            text = _extract_response_text(response)
            if text and text.strip():
                choices = getattr(response, "choices", None) or []
                if choices:
                    finish_reason = getattr(choices[0], "finish_reason", None)
                    if finish_reason and finish_reason not in ("stop",):
                        logger.warning("[FINISH_REASON] %s model=%s", finish_reason, active_model)
                logger.info("[LLM_RESPONSE] model=%s chars=%s retries=%s", active_model, len(text), retries)
                return {
                    "text": postprocess_answer(text, question=question),
                    "usage": _extract_usage(response),
                    "model": active_model,
                    "retries": retries,
                }

            last_error = ValueError(_describe_empty_response(response))
            consecutive_avail_errors = 0
        except Exception as exc:
            last_error = exc
            logger.warning("Fallo OpenAI %s (intento %s/%s): %s", active_model, attempt + 1, _MAX_ATTEMPTS, str(exc))
            status_code = _extract_status_code(exc)
            if status_code in (429, 503):
                _record_model_avail_error_event(active_model, status_code)
                consecutive_avail_errors += 1
                if consecutive_avail_errors >= _MAX_CONSECUTIVE_503:
                    logger.warning("[AVAIL_ABORT] %s errores consecutivos en %s; abandonando para usar fallback", consecutive_avail_errors, active_model)
                    break
            else:
                consecutive_avail_errors = 0

        if isinstance(last_error, ValueError) and "finish_reason=" in str(last_error).lower():
            break

        if attempt < len(_RETRY_WAITS):
            retries += 1
            time.sleep(_RETRY_WAITS[attempt])

    logger.error("[ALERT][LLM_ERROR] agotados intentos en OpenAI %s: %s", active_model, str(last_error))
    status_code = _extract_status_code(last_error) if isinstance(last_error, Exception) else None
    transient = _is_transient_error(last_error) if isinstance(last_error, Exception) else False
    raise AIResponseError(
        f"No se pudo obtener respuesta de OpenAI ({active_model}): {str(last_error)}",
        retries=retries,
        transient=transient,
        status_code=status_code,
    )


def generate_ai_response_with_fallback(
    question: str,
    context: str = "",
    sources: Optional[List[str]] = None,
    history: Optional[List[Dict]] = None,
    retrieval_stats: Optional[Dict[str, object]] = None,
) -> Dict:
    confidence_threshold = _effective_confidence_threshold(question)
    question_profile = _infer_answer_profile(question)
    try:
        result = generate_ai_response(question, context=context, history=history)
    except AIResponseError as exc:
        avail_error = getattr(exc, "status_code", None) in (429, 503)
        should_try_avail_fallback = (
            avail_error
            and LLM_FALLBACK_MODEL
            and LLM_FALLBACK_MODEL != OPENAI_MODEL
        )
        if not should_try_avail_fallback:
            raise

        logger.warning(
            "[AVAIL_FALLBACK] %s devolvio %s; probando %s",
            OPENAI_MODEL,
            getattr(exc, "status_code", None),
            LLM_FALLBACK_MODEL,
        )
        result = generate_ai_response(
            question,
            context=context,
            history=history,
            model=LLM_FALLBACK_MODEL,
        )
        result["avail_fallback"] = True
        result["source_model"] = OPENAI_MODEL

    _, confidence = validate_answer(question, result["text"], context, sources)
    table_coverage_ratio = float((retrieval_stats or {}).get("table_coverage_ratio", 1.0) or 0.0)
    domain_match_ratio = float((retrieval_stats or {}).get("domain_match_ratio", 1.0) or 0.0)
    expected_domains = (retrieval_stats or {}).get("expected_domains") or []
    if question_profile.get("table"):
        if table_coverage_ratio < 0.35:
            confidence -= 0.18
        elif table_coverage_ratio < 0.55:
            confidence -= 0.1
        confidence = max(0.0, round(confidence, 4))
    if expected_domains and domain_match_ratio < 0.45:
        confidence = max(0.0, round(confidence - 0.14, 4))
    elif expected_domains and domain_match_ratio < 0.65:
        confidence = max(0.0, round(confidence - 0.07, 4))
    primary_has_conflict = _has_critical_term_conflict(question, result["text"], context)
    escalation_reason = _should_escalate(
        question,
        result["text"],
        confidence,
        confidence_threshold,
        primary_has_conflict,
        retrieval_stats,
        context=context,
    )
    result["base_model"] = result.get("source_model") or OPENAI_MODEL
    result["base_confidence"] = round(confidence, 4)
    result["confidence_threshold"] = confidence_threshold
    result["base_conflict"] = primary_has_conflict
    result["retrieval_quality"] = _retrieval_quality(retrieval_stats)
    result["table_coverage_ratio"] = table_coverage_ratio
    result["escalation_reason"] = escalation_reason or ""

    if not escalation_reason:
        result["final_model"] = result.get("model", OPENAI_MODEL)
        result["final_confidence"] = round(confidence, 4)
        result["confidence"] = result["final_confidence"]
        result["escalated"] = False
        return result

    logger.info(
        "[MODEL_FALLBACK] conf=%.2f reason=%s retrieval=%s modelo_secundario=%s q='%s'",
        confidence, escalation_reason, result["retrieval_quality"], LLM_SECONDARY_MODEL, question[:80],
    )
    try:
        result_pro = generate_ai_response(question, context=context, history=history, model=LLM_SECONDARY_MODEL)
        result_pro["escalated"] = True
        result_pro["flash_confidence"] = round(confidence, 4)
        result_pro["base_confidence"] = round(confidence, 4)
        result_pro["confidence_threshold"] = confidence_threshold
        result_pro["base_conflict"] = primary_has_conflict
        result_pro["escalation_reason"] = escalation_reason
        result_pro["retrieval_quality"] = _retrieval_quality(retrieval_stats)
        result_pro["base_model"] = result.get("source_model") or OPENAI_MODEL
        result_pro["final_model"] = LLM_SECONDARY_MODEL
        _, secondary_confidence = validate_answer(question, result_pro["text"], context, sources)
        if question_profile.get("table"):
            if table_coverage_ratio < 0.35:
                secondary_confidence -= 0.18
            elif table_coverage_ratio < 0.55:
                secondary_confidence -= 0.1
            secondary_confidence = max(0.0, round(secondary_confidence, 4))
        if expected_domains and domain_match_ratio < 0.45:
            secondary_confidence = max(0.0, round(secondary_confidence - 0.14, 4))
        elif expected_domains and domain_match_ratio < 0.65:
            secondary_confidence = max(0.0, round(secondary_confidence - 0.07, 4))
        secondary_has_conflict = _has_critical_term_conflict(question, result_pro["text"], context)
        result_pro["secondary_conflict"] = secondary_has_conflict
        if secondary_has_conflict and secondary_confidence < max(0.45, confidence_threshold - 0.12):
            logger.warning(
                "[MODEL_FALLBACK_REJECT] respuesta inconsistente tras fallback en %s; devolviendo insuficiencia",
                LLM_SECONDARY_MODEL,
            )
            result_pro["text"] = "No hay informacion suficiente en el contexto recuperado"
            secondary_confidence = 0.0
        result_pro["final_confidence"] = round(secondary_confidence, 4)
        result_pro["confidence"] = result_pro["final_confidence"]
        return result_pro
    except AIResponseError as exc:
        logger.warning(
            "[MODEL_FALLBACK_FAIL] devolviendo respuesta base tras fallo en %s: %s",
            LLM_SECONDARY_MODEL,
            str(exc),
        )
        if primary_has_conflict and confidence < max(0.45, confidence_threshold - 0.12):
            logger.warning("[PRIMARY_REJECT] respuesta base inconsistente con el contexto; devolviendo insuficiencia")
            result["text"] = "No hay informacion suficiente en el contexto recuperado"
        result["pro_fallback_failed"] = True
        result["flash_confidence"] = round(confidence, 4)
        result["final_model"] = result.get("model", OPENAI_MODEL)
        result["final_confidence"] = round(confidence, 4)
        result["confidence"] = result["final_confidence"]
        result["escalated"] = False
        result["retries"] = max(int(result.get("retries", 0) or 0), int(getattr(exc, "retries", 0) or 0))
        return result


def get_ai_response(question: str, context: str = "", history: Optional[List[Dict]] = None) -> str:
    generated = generate_ai_response(question=question, context=context, history=history)
    return generated["text"]


def validate_answer(question: str, answer: str, context: str, sources: Optional[List[str]]) -> Tuple[str, float]:
    text = postprocess_answer(answer, question=question)
    confidence = 0.0
    lower_text = text.lower()
    context_lower = (context or "").lower()
    profile = _infer_answer_profile(question)

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
    numeric_question = bool(profile["numeric"])
    definition_question = bool(profile["definition"])
    list_question = bool(profile["list"])
    comparison_question = bool(profile["comparison"])
    procedure_question = bool(profile["procedure"])
    direct_fact_question = bool(profile["direct_fact"])
    summary_question = bool(profile["summary"])
    table_question = bool(profile["table"])
    scope_question = bool(SCOPE_ASK_PATTERN.search(question or "")) or bool(profile["generalization"])
    expects_multiple_items = bool(MULTI_ITEM_ASK_PATTERN.search(question or ""))
    partial_signal = bool(PARTIAL_SIGNAL_PATTERN.search(lower_text)) or "base documental: parcial" in lower_text
    explicit_signal = bool(EXPLICIT_SIGNAL_PATTERN.search(lower_text)) or "base documental: explic" in lower_text
    formula_fragment_signal = any(
        FORMULA_FRAGMENT_PATTERN.search(line.strip())
        for line in text.splitlines()
        if line.strip() and "fuentes:" not in line.lower()
    )
    first_non_source_line = next((line.strip() for line in text.splitlines() if line.strip() and "fuentes:" not in line.lower()), "")
    weak_definition_opening = bool(DEFINITION_WEAK_OPENING_PATTERN.search(first_non_source_line))
    direct_definition_opening = bool(DEFINITION_DIRECT_ANSWER_PATTERN.search(first_non_source_line))
    vague_definition_signal = bool(VAGUE_DEFINITION_PATTERN.search(lower_text))
    list_format_signal = bool(LIST_FORMAT_PATTERN.search(text))
    structured_item_count = _count_structured_items(text)
    table_incomplete_signal = bool(TABLE_INCOMPLETE_SIGNAL_PATTERN.search(lower_text))
    has_step_markers = bool(re.search(r"\b(paso|primero|segundo|despues|a continuacion)\b", lower_text))
    comparison_markers = bool(re.search(r"\b(mientras que|por el contrario|en cambio|diferencia|frente a)\b", lower_text))
    incomplete_evidence_signal = bool(INCOMPLETE_EVIDENCE_PATTERN.search(lower_text))
    narrow_subsection_signal = bool(NARROW_SUBSECTION_PATTERN.search(lower_text))
    missing_qualifiers_signal = _missing_qualifiers_signal(text, context or "")
    labeled_term_conflicts = _find_labeled_term_conflicts(question, text, context)
    chunk_support_score = _best_chunk_support_score(question, text, context)

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
    if list_question and list_format_signal:
        confidence += 0.06
    elif list_question and len(text) < 40:
        confidence -= 0.08
    if (list_question or summary_question or table_question) and expects_multiple_items and structured_item_count < 2 and not uncertainty_signal:
        confidence -= 0.12
    if table_question and "tabla" in lower_text and structured_item_count < 2 and not uncertainty_signal:
        confidence -= 0.08
    if table_question and structured_item_count >= 3 and has_reference_evidence and not uncertainty_signal:
        confidence += 0.08
    if table_question and (uncertainty_signal or table_incomplete_signal) and structured_item_count < 3:
        confidence -= 0.12
    if comparison_question and comparison_markers:
        confidence += 0.06
    elif comparison_question and not uncertainty_signal:
        confidence -= 0.08
    if procedure_question and has_step_markers:
        confidence += 0.05
    if chunk_support_score >= 0.72:
        confidence += 0.12
    elif chunk_support_score >= 0.55:
        confidence += 0.06
    elif chunk_support_score >= 0.4 and (definition_question or direct_fact_question or numeric_question):
        confidence += 0.03
    elif (definition_question or direct_fact_question or numeric_question or list_question) and chunk_support_score < 0.35:
        confidence -= 0.12
    elif chunk_support_score < 0.22:
        confidence -= 0.08
    if definition_question and direct_definition_opening:
        confidence += 0.08
    elif definition_question and weak_definition_opening:
        confidence -= 0.18
    if definition_question and vague_definition_signal:
        confidence -= 0.22
    if direct_fact_question and weak_definition_opening and not uncertainty_signal:
        confidence -= 0.12
    if (summary_question or table_question) and expects_multiple_items and structured_item_count >= 2:
        confidence += 0.08
    if labeled_term_conflicts:
        confidence -= 0.3
    if formula_fragment_signal:
        confidence -= 0.15
    if context and overlap_ratio < 0.08:
        confidence -= 0.15
    if PARTIAL_WITH_DETAIL_PATTERN.search(text) and len(text) >= 240:
        confidence -= 0.14
    # El contexto trae condicionantes/excepciones/ámbito/límites y la respuesta
    # los omite: penalización fuerte para no "generalizar de más" (objetivo
    # principal del bot).
    if missing_qualifiers_signal:
        if scope_question or summary_question or list_question or definition_question:
            confidence -= 0.18
        else:
            confidence -= 0.1
    # Penaliza respuestas "parcialmente correctas" en preguntas de alcance/objeto:
    # si la salida indica evidencia incompleta y se apoya en una subseccion concreta,
    # la completitud de intencion es baja aunque haya grounding lexico alto.
    if scope_question and incomplete_evidence_signal:
        confidence -= 0.12
    if scope_question and incomplete_evidence_signal and narrow_subsection_signal:
        confidence -= 0.1
    if scope_question and uncertainty_signal and narrow_subsection_signal:
        confidence -= 0.08

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
    if definition_question and (weak_definition_opening or vague_definition_signal) and not direct_definition_opening:
        cap = min(cap, 0.62)
    if direct_fact_question and weak_definition_opening and not direct_definition_opening:
        cap = min(cap, 0.68)
    if labeled_term_conflicts:
        cap = min(cap, 0.58)
    if (list_question or summary_question or table_question) and expects_multiple_items and structured_item_count < 2:
        cap = min(cap, 0.62)
    if table_question and (uncertainty_signal or table_incomplete_signal):
        cap = min(cap, 0.64)
    if (definition_question or direct_fact_question or numeric_question or list_question) and chunk_support_score < 0.35:
        cap = min(cap, 0.6)
    if scope_question and incomplete_evidence_signal:
        cap = min(cap, 0.72)
    if scope_question and incomplete_evidence_signal and narrow_subsection_signal:
        cap = min(cap, 0.68)
    if PARTIAL_WITH_DETAIL_PATTERN.search(text):
        cap = min(cap, 0.74)
    if missing_qualifiers_signal:
        cap = min(cap, 0.7)

    confidence = min(max(confidence, 0.0), cap)
    return text, confidence
