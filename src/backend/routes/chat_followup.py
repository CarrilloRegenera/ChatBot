import re
from typing import Callable, Dict, List


_FOLLOWUP_PREFIX_RE = re.compile(
    r"^(?:y|entonces|ademas|además|tambien|también|sobre eso|sobre ello|respecto a eso|respecto a ello|en ese caso)\b"
)
_FOLLOWUP_WORD_RE = re.compile(r"[a-z0-9]+", flags=re.IGNORECASE)
_EXPLICIT_TECHNICAL_ANCHOR_RE = re.compile(
    r"\b(?:rebt|rite|ralt|itc|bt-?\d+|iec|ieee|iso|80005(?:-[123])?|ops|eopsa|shore power|cold ironing|afir)\b",
    flags=re.IGNORECASE,
)


def normalize_followup_text(question: str) -> str:
    normalized = " ".join((question or "").strip().lower().split())
    return normalized.lstrip("¿?¡!.,;:()[]{}\"' ")


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

    token_count = len(_FOLLOWUP_WORD_RE.findall(normalized))
    if token_count > 6:
        return False
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

    return normalized.startswith(("que ", "qué ", "cual ", "cuál ", "como ", "cómo "))


def is_followup_prefix_question(question: str) -> bool:
    normalized = normalize_followup_text(question)
    if not normalized:
        return False
    return bool(_FOLLOWUP_PREFIX_RE.search(normalized))


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
