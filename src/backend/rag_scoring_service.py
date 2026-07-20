from typing import Callable


DOCUMENT_LAYER_BOOSTS = {
    "normativa_oficial": 8.0,
    "guia_oficial": 4.0,
    "manual_fabricante": 0.0,
    "pendiente": -5.0,
}


def document_layer_boost(
    layer: str,
    *,
    normalize_text: Callable[[str], str],
    normative_intent: bool = False,
    procedure_intent: bool = False,
) -> float:
    normalized = normalize_text(layer or "")
    if procedure_intent and not normative_intent:
        return {
            "normativa_oficial": 0.0,
            "guia_oficial": 4.0,
            "manual_fabricante": 6.0,
            "pendiente": -5.0,
        }.get(normalized, 0.0)
    return DOCUMENT_LAYER_BOOSTS.get(normalized, 0.0)


def bm25_score(
    query_tokens: set,
    doc_text: str,
    avg_doc_len: float,
    *,
    normalize_text: Callable[[str], str],
    k1: float = 1.5,
    b: float = 0.75,
) -> float:
    doc_tokens = normalize_text(doc_text).split()
    doc_len = len(doc_tokens)
    if doc_len == 0 or avg_doc_len == 0:
        return 0.0

    tf_map: dict[str, int] = {}
    for token in doc_tokens:
        tf_map[token] = tf_map.get(token, 0) + 1

    score = 0.0
    for token in query_tokens:
        frequency = tf_map.get(normalize_text(token), 0)
        if frequency == 0:
            continue
        numerator = frequency * (k1 + 1)
        denominator = frequency + k1 * (1 - b + b * (doc_len / avg_doc_len))
        score += numerator / denominator
    return score


def domain_exclusion_penalty(
    expected_domains: list,
    source_domain: str,
    question_lower: str,
    *,
    exclusions: list,
) -> int:
    if not expected_domains or not exclusions:
        return 0
    for rule in exclusions:
        if rule["primary"] in expected_domains and rule["excluded"] == source_domain:
            if any(token in question_lower for token in rule.get("condition_tokens", [])):
                return rule.get("extra_penalty", -20)
    return 0
