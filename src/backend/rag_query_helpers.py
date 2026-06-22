import re
from typing import Callable, Dict, List


def extract_reference_terms(text: str, *, reference_pattern) -> List[str]:
    return [match.group(0).strip().lower() for match in reference_pattern.finditer(text or "")]


def extract_it_section_refs(text: str, *, it_section_reference_pattern) -> List[str]:
    seen: set[str] = set()
    result = []
    for match in it_section_reference_pattern.finditer(text or ""):
        ref = f"it {match.group(1)}"
        if ref not in seen:
            seen.add(ref)
            result.append(ref)
    return result


def extract_article_refs(
    text: str,
    *,
    normalize_text: Callable[[str], str],
    article_reference_pattern,
) -> List[str]:
    seen: set[str] = set()
    result = []
    normalized = normalize_text(text or "")
    for value in article_reference_pattern.findall(normalized):
        ref = f"articulo {int(value)}"
        if ref not in seen:
            seen.add(ref)
            result.append(ref)
    return result


def extract_exact_refs(
    text: str,
    *,
    itc_reference_pattern,
    table_reference_pattern,
) -> List[str]:
    refs = set()
    raw = text or ""
    for prefix, value in itc_reference_pattern.findall(raw):
        number = str(value).zfill(2)
        normalized_prefix = (prefix or "bt").lower()
        refs.add(f"itc-{normalized_prefix}-{number}")
        refs.add(f"itc {normalized_prefix} {number}")
        refs.add(f"{normalized_prefix}-{number}")
    for value in table_reference_pattern.findall(raw):
        refs.add(f"tabla {int(value)}")
    return sorted(refs)


def extract_page_refs(text: str, *, page_reference_pattern) -> List[int]:
    pages = []
    seen = set()
    for value in page_reference_pattern.findall(text or ""):
        try:
            page = int(value)
        except ValueError:
            continue
        if page > 0 and page not in seen:
            seen.add(page)
            pages.append(page)
    return pages[:5]


def extract_location_target(
    text: str,
    *,
    normalize_text: Callable[[str], str],
    tokenize: Callable[[str], List[str]],
    stopwords: set[str],
) -> str:
    normalized = normalize_text(text)
    match = re.search(
        r"(?:pagina|pag|page|donde\s+(?:aparece|esta|se\s+encuentra)|ubicacion|apartado\s+de)\s+(?:de\s+|del\s+|la\s+|el\s+)?(.+)",
        normalized,
    )
    if not match:
        return ""
    target = match.group(1)
    target = re.split(r"\b(?:en|del|de)\s+(?:el\s+|la\s+)?(?:pdf|documento|guia|itc|boe|rebt|rite)\b", target, maxsplit=1)[0]
    tokens = [token for token in tokenize(target) if token not in stopwords]
    return " ".join(tokens[:8])


def extract_labeled_terms(text: str, *, labeled_query_patterns) -> Dict[str, set[str]]:
    extracted: Dict[str, set[str]] = {}
    for family, pattern in labeled_query_patterns.items():
        matches = {
            re.sub(r"\s+", "", match.group(1).lower())
            for match in pattern.finditer(text or "")
            if match.group(1)
        }
        if matches:
            extracted[family] = matches
    return extracted


def extract_disambiguation_terms(
    text: str,
    *,
    tokenize: Callable[[str], List[str]],
    normalize_text: Callable[[str], str],
    stopwords: set[str],
    extract_labeled_terms_fn: Callable[[str], Dict[str, set[str]]],
    exclude_labeled: bool = True,
    limit: int = 6,
) -> List[str]:
    local_stopwords = stopwords.union({
        "clase", "tipo", "categoria", "categoría", "grado", "esquema",
        "cual", "cuales", "que", "qué", "como", "cómo", "caracteriza",
        "caracteristicas", "características", "define", "definicion", "definición",
        "materiales",
    })
    labeled_values = set()
    if exclude_labeled:
        for values in extract_labeled_terms_fn(text).values():
            labeled_values.update(values)

    terms = []
    seen = set()
    for token in tokenize(text):
        normalized = normalize_text(token)
        normalized = re.sub(r"\s+", "", normalized)
        if (
            not normalized
            or normalized in local_stopwords
            or normalized in labeled_values
            or len(normalized) < 5
            or normalized in seen
        ):
            continue
        seen.add(normalized)
        terms.append(normalized)
        if len(terms) >= limit:
            break
    return terms


def extract_topic_terms(
    text: str,
    *,
    tokenize: Callable[[str], List[str]],
    normalize_text: Callable[[str], str],
    stopwords: set[str],
    limit: int,
) -> List[str]:
    tokens = []
    seen = set()
    for token in tokenize(text):
        normalized = normalize_text(token)
        if normalized in stopwords or len(normalized) < 5 or normalized in seen:
            continue
        seen.add(normalized)
        tokens.append(normalized)
        if len(tokens) >= limit:
            break
    return tokens


def build_query_profile(
    clean_question: str,
    question_keywords: set[str],
    *,
    normalize_text: Callable[[str], str],
    extract_numeric_terms: Callable[[str], List[str]],
    numeric_query_variants: Callable[[str], List[str]],
    numeric_value_groups: Callable[[str], List[str]],
    standalone_numbers: Callable[[str], List[str]],
    extract_reference_terms_fn: Callable[[str], List[str]],
    extract_exact_refs_fn: Callable[[str], List[str]],
    extract_page_refs_fn: Callable[[str], List[int]],
    extract_location_target_fn: Callable[[str], str],
    query_phrase_queries: Callable[[str], List[str]],
    technical_equivalent_phrases: Callable[[str], List[str]],
    is_normative_intent_query: Callable[[str], bool],
    query_intent: Callable[[str], str],
    extract_labeled_terms_fn: Callable[[str], Dict[str, set[str]]],
    extract_disambiguation_terms_fn: Callable[[str], List[str]],
    definition_query_pattern,
    list_query_pattern,
    summary_query_pattern,
    table_query_pattern,
    table_reference_pattern,
    comparison_query_pattern,
    procedure_query_pattern,
    generalization_query_pattern,
    scope_query_pattern,
    motivation_query_pattern,
    temporal_query_pattern,
    extract_topic_terms_fn: Callable[[str, int], List[str]],
    max_topic_tokens: int,
    extract_article_refs_fn: Callable[[str], List[str]],
    extract_it_section_refs_fn: Callable[[str], List[str]],
) -> Dict[str, object]:
    normalized = normalize_text(clean_question)
    return {
        "normalized_question": normalized,
        "numeric_terms": extract_numeric_terms(clean_question),
        "numeric_variants": numeric_query_variants(clean_question),
        "numeric_value_groups": numeric_value_groups(clean_question),
        "standalone_numbers": standalone_numbers(clean_question),
        "reference_terms": extract_reference_terms_fn(clean_question),
        "exact_refs": extract_exact_refs_fn(clean_question),
        "page_refs": extract_page_refs_fn(clean_question),
        "location_target": extract_location_target_fn(clean_question),
        "phrase_queries": query_phrase_queries(clean_question),
        "technical_equivalent_phrases": technical_equivalent_phrases(clean_question),
        "normative_intent_query": is_normative_intent_query(clean_question),
        "intent": query_intent(clean_question),
        "labeled_terms": extract_labeled_terms_fn(clean_question),
        "disambiguation_terms": extract_disambiguation_terms_fn(clean_question),
        "comparison": any(term in normalized for term in ("compara", "diferencia", "frente", "versus")),
        "expects_numeric": bool(re.search(r"\b(cuanto|cuantos|cuantas|valor|limite|potencia|resistencia|ohm|kw|mm2|m2|volt|amper|porcentaje)\b", normalized)),
        "definition_query": bool(definition_query_pattern.search(normalized)),
        "list_query": bool(list_query_pattern.search(normalized)),
        "summary_query": bool(summary_query_pattern.search(normalized)),
        "table_query": bool(table_query_pattern.search(normalized) or table_reference_pattern.search(clean_question)),
        "comparison_query": bool(comparison_query_pattern.search(normalized)),
        "procedure_query": bool(procedure_query_pattern.search(normalized)),
        "generalization_query": bool(generalization_query_pattern.search(normalized)),
        "scope_query": bool(scope_query_pattern.search(normalized)),
        "motivation_query": bool(motivation_query_pattern.search(normalized)),
        "temporal_query": bool(temporal_query_pattern.search(normalized)),
        "question_keywords": question_keywords,
        "section_terms": extract_topic_terms_fn(clean_question, max_topic_tokens),
        "article_refs": extract_article_refs_fn(clean_question),
        "it_section_refs": extract_it_section_refs_fn(clean_question),
    }


def extract_core_terms(
    question_keywords: set[str],
    *,
    clean_question: str = "",
    normalize_text: Callable[[str], str],
    stopwords: set[str],
) -> List[str]:
    ranked = sorted(question_keywords, key=lambda term: (len(term), term), reverse=True)
    terms = ranked[:4]
    if clean_question:
        words = normalize_text(clean_question).split()
        for index in range(len(words) - 1):
            bigram = f"{words[index]} {words[index + 1]}"
            if len(bigram) >= 10 and bigram not in terms and words[index] not in stopwords and words[index + 1] not in stopwords:
                terms.append(bigram)
                if len(terms) >= 6:
                    break
    return terms
