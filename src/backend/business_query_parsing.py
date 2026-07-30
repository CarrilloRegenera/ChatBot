import re
from typing import Callable, Dict, List


def looks_like_follow_up(text: str, *, follow_up_prefixes: tuple[str, ...]) -> bool:
    return any(text.startswith(prefix) for prefix in follow_up_prefixes) or len(text.split()) <= 4


def mentions_previous_reference(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            " este proyecto",
            " esta licitacion",
            " este estudio",
            " esta obra",
            "este proyecto ",
            "esta licitacion ",
            "esta obra ",
            " su ",
            " sus ",
            "este ",
            "esta ",
        )
    )


def can_inherit_reference_from_context(text: str) -> bool:
    if re.search(r"\b(?:cuanto|cuanta|que)\s+(?:tenemos|hay)\b", text):
        return False
    if any(
        token in text
        for token in (
            "top ",
            "ranking",
            "con mas",
            "con mayor",
            "media de ",
            "promedio de ",
            "numero de ",
            "cuantas ",
            "cuantos ",
            "total de ",
            "suma de ",
            " backlog",
            " pipeline",
        )
    ):
        return False
    if any(token in text for token in ("licitaciones", "obras", "proyectos", "estudios", "cierres")):
        return False
    return any(
        token in text
        for token in (
            " importe ",
            " importe contratado",
            " produccion",
            " backlog",
            " pipeline",
            " cliente",
            " concurso",
            " fecha ",
            " tipologia",
            " tipo ",
            " n oferta",
            " numero oferta",
            " numero de oferta",
            " estado",
            " apertura",
            " adjudicacion",
            " presentacion",
            " tiene ",
        )
    )


def extract_reference(original_question: str, normalized: str) -> str | None:
    reference_patterns = (
        r"\b(est[-\s]?\d{1,4}[-\s]?20\d{2})\b",
        r"\b([a-z]{2,6}-\d{1,5}-20\d{2})\b",
        r"\b(?:proyecto|obra|licitacion|licitacion|oferta|estudio)\s+([a-z0-9-]*\d[a-z0-9-]{3,29})\b",
    )
    for pattern in reference_patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper().replace(" ", "-")

    numeric_tokens = re.findall(r"\b\d{4,8}\b", normalized)
    non_year_tokens = [token for token in numeric_tokens if not re.fullmatch(r"20\d{2}", token)]
    if non_year_tokens:
        return non_year_tokens[0]

    quoted = re.search(r'"([^"]+)"|\'([^\']+)\'', original_question)
    if quoted:
        value = (quoted.group(1) or quoted.group(2) or "").strip()
        return value or None
    return None


def strip_reference_for_year_detection(normalized: str, reference: str | None, *, normalize: Callable[[str], str]) -> str:
    if not reference:
        return normalized
    normalized_reference = normalize(reference).replace(" ", "-")
    return re.sub(rf"\b{re.escape(normalized_reference)}\b", " ", normalized)


def extract_explicit_years(text: str, reference: str | None, *, normalize: Callable[[str], str]) -> List[int]:
    text_without_reference = strip_reference_for_year_detection(text, reference, normalize=normalize)
    years = [int(match) for match in re.findall(r"\b(20\d{2})\b", text_without_reference)]
    return list(dict.fromkeys(years))


def extract_cuatrimestre(text: str) -> int | None:
    patterns = (
        (r"\b(c1|primer cuatrimestre|1er cuatrimestre|cuatrimestre 1|cuatrimestre uno)\b", 1),
        (r"\b(c2|segundo cuatrimestre|2do cuatrimestre|cuatrimestre 2|cuatrimestre dos)\b", 2),
        (r"\b(c3|tercer cuatrimestre|3er cuatrimestre|cuatrimestre 3|cuatrimestre tres)\b", 3),
    )
    for pattern, value in patterns:
        if re.search(pattern, text):
            return value
    return None


def extract_month(text: str, *, month_aliases: Dict[str, int]) -> int | None:
    for name, month in month_aliases.items():
        if re.search(rf"\b{name}\b", text):
            return month
    return None


def is_per_month_request(text: str) -> bool:
    return any(token in text for token in ("cada mes", "por mes", "mes a mes", "en cada mes", "todos los meses"))


def is_per_year_request(text: str) -> bool:
    return any(token in text for token in ("cada ano", "por ano", "anual", "en cada ano", "todos los anos"))


def extract_periodo(text: str) -> str | None:
    match = re.search(r"\b(20\d{2}-\d{2})\b", text)
    return match.group(1) if match else None


def extract_area(text: str) -> str | None:
    area_patterns = {
        "OI": (r"\bo\s*i\b", r"\boi\b"),
        "MAN": (r"\bman\b",),
        "MT": (r"\bmt\b",),
        "BT": (r"\bbt\b",),
    }
    for area, patterns in area_patterns.items():
        if any(re.search(pattern, text) for pattern in patterns):
            return area
    return None
