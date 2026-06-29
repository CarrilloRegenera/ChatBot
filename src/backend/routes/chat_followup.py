import re
from typing import Callable, Dict, List


_FOLLOWUP_PREFIX_RE = re.compile(
    r"^(?:y|ahora|entonces|ademas|además|tambien|también|sobre eso|sobre ello|respecto a eso|respecto a ello|en ese caso)\b"
)
_FOLLOWUP_REFERENCE_RE = re.compile(
    r"\b(?:respecto a lo anterior|como antes|lo anterior|ese calculo|ese cálculo|"
    r"esa tabla|ese equipo|para cerrar el calculo|para cerrar el cálculo)\b"
)
_FOLLOWUP_WORD_RE = re.compile(r"[a-z0-9]+", flags=re.IGNORECASE)
_EXPLICIT_TECHNICAL_ANCHOR_RE = re.compile(
    r"\b(?:rebt|rite|ralt|itc|bt-?\d+|iec|ieee|iso|80005(?:-[123])?|ops|eopsa|shore power|cold ironing|afir)\b",
    flags=re.IGNORECASE,
)
# Detecta preguntas de resolución de significado: "qué significa la t", "indaga lo que significa m"
_DEFINITION_QUERY_RE = re.compile(
    r"\b(significa[n]?|quiere(?:s|n)?\s+decir|que\s+quiere(?:s|n)?\s+decir|que\s+es\b|definici[oó]n\s+de)\b",
    flags=re.IGNORECASE,
)
_ABBREVIATION_QUERY_RE = re.compile(
    r"\b(?:significa[n]?|quiere(?:s|n)?\s+decir|interpreta[r]?|representa[n]?|equivale[n]?)\b.*\b[a-z]\b",
    flags=re.IGNORECASE,
)


def normalize_followup_text(question: str) -> str:
    normalized = " ".join((question or "").strip().lower().split())
    return normalized.lstrip("¿?¡!.,;:()[]{}\"' ")


def is_symbol_definition_query(question: str) -> bool:
    """Detecta preguntas de resolución de símbolo/abreviatura sin ancla técnica explícita.

    Ejemplos: 'qué significa la t', 'indaga lo que significa la m', 'que quiere decir 2t'.
    Devuelve False si la pregunta ya contiene un ancla técnica explícita (RITE, REBT...).
    """
    normalized = normalize_followup_text(question)
    if not normalized:
        return False
    if _EXPLICIT_TECHNICAL_ANCHOR_RE.search(normalized):
        return False
    return bool(_DEFINITION_QUERY_RE.search(normalized))


def is_abbreviation_query(question: str) -> bool:
    normalized = normalize_followup_text(question)
    if not normalized or not _ABBREVIATION_QUERY_RE.search(normalized):
        return False
    has_table_context = any(
        term in normalized
        for term in ("tabla", "periodicidad", "abreviatura", "simbolo", "letra")
    )
    single_letters = re.findall(r"(?<![a-z0-9])[a-z](?![a-z0-9])", normalized)
    return has_table_context or len(set(single_letters)) >= 2


def should_apply_history_hints(
    question: str,
    *,
    rag_service,
) -> bool:
    normalized = normalize_followup_text(question)
    if not normalized:
        return False
    if _FOLLOWUP_PREFIX_RE.search(normalized):
        return True
    if _FOLLOWUP_REFERENCE_RE.search(normalized):
        return True

    # Preguntas de resolución de símbolo/abreviatura heredan contexto aunque sean largas
    # ("indaga en el archivo y busca lo que significa la t y la m" > 6 tokens)
    definition_query = is_symbol_definition_query(question)

    if _EXPLICIT_TECHNICAL_ANCHOR_RE.search(normalized):
        return False

    explicit_domains = rag_service.detect_hint_domains(normalized)
    explicit_document_variants = rag_service.detect_hint_document_variants(
        normalized,
        explicit_domains or None,
    )
    explicit_article_refs = rag_service.detect_hint_article_refs(normalized)
    explicit_it_section_refs = rag_service.detect_hint_it_section_refs(normalized)
    if (
        explicit_domains
        or explicit_document_variants
        or explicit_article_refs
        or explicit_it_section_refs
    ):
        return False

    # Si la pregunta no trae un ancla nueva, el ultimo foco tecnico es una
    # mejor restriccion que volver a buscar en todo el corpus. Las preguntas
    # con dominio explicito salen antes y no heredan.
    return True


def derive_history_hints(
    history: List[Dict[str, str]] | None,
    *,
    rag_service,
) -> Dict[str, List[str]]:
    """Recupera el ultimo foco explicito sin analizar respuestas generadas."""
    empty = {
        "domains": [],
        "document_variants": [],
        "article_refs": [],
        "it_section_refs": [],
    }
    for item in reversed((history or [])[-3:]):
        question = str(item.get("question", "") or "").strip()
        if not question:
            continue
        domains = rag_service.detect_hint_domains(question)
        variants = rag_service.detect_hint_document_variants(question, domains or None)
        article_refs = rag_service.detect_hint_article_refs(question)
        it_section_refs = rag_service.detect_hint_it_section_refs(question)
        if domains or variants or article_refs or it_section_refs:
            return {
                "domains": domains,
                "document_variants": variants,
                "article_refs": article_refs,
                "it_section_refs": it_section_refs,
            }
    return empty


def is_followup_prefix_question(question: str) -> bool:
    normalized = normalize_followup_text(question)
    if not normalized:
        return False
    return bool(_FOLLOWUP_PREFIX_RE.search(normalized))


def maybe_inherit_inventory_route(
    question: str,
    *,
    history: List[Dict[str, str]] | None,
    classify_question: Callable[[str], Dict[str, str]],
) -> str | None:
    """Si la pregunta es un follow-up y el turno anterior fue inventario, hereda la ruta."""
    if not is_followup_prefix_question(question):
        return None
    for item in reversed((history or [])[-3:]):
        previous_question = str(item.get("question", "") or "").strip()
        if previous_question and classify_question(previous_question).get("route") == "document_inventory":
            return "document_inventory"
    return None


def augment_if_symbol_query(
    rag_question: str,
    *,
    original_question: str,
    hint_domains: List[str],
    hint_it_section_refs: List[str],
) -> str:
    """Expande queries de resolución de símbolo/abreviatura con contexto heredado."""
    if not (
        is_symbol_definition_query(original_question)
        or is_abbreviation_query(original_question)
    ):
        return rag_question
    parts = [rag_question, "leyenda definicion abreviatura simbolo periodicidad tabla"]
    if hint_it_section_refs:
        parts.extend(hint_it_section_refs[:2])
    if hint_domains:
        parts.extend(hint_domains[:2])
    return " ".join(parts)


def recover_route_from_history(
    question: str,
    *,
    chat_mode: str,
    route: str,
    business_route_hint: str | None,
    history: List[Dict[str, str]] | None,
    detect_business_route: Callable[[str], str | None],
    has_concrete_business_reference: Callable[[str], bool],
) -> str:
    if business_route_hint and chat_mode == "business":
        return business_route_hint
    if business_route_hint and chat_mode == "technical" and has_concrete_business_reference(question):
        return business_route_hint

    recent_history = history or []
    if not is_followup_prefix_question(question):
        return route

    if chat_mode == "technical" and route in {"smalltalk", "invalid", "out_of_scope"}:
        return "knowledge"

    if chat_mode == "business" and route in {"smalltalk", "invalid", "out_of_scope", "knowledge"}:
        for item in reversed(recent_history):
            previous_question = str(item.get("question", "") or "").strip()
            if not previous_question:
                continue
            inferred = detect_business_route(previous_question)
            if inferred in {"business_licitaciones", "business_produccion"}:
                return inferred

    return route
