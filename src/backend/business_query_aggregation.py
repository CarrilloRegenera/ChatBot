from collections.abc import Callable
from typing import Mapping, Sequence


def detect_aggregate_metric(
    text: str,
    fields: Sequence[str],
    *,
    module: str,
    year: int | None,
    closure_field_hints: Mapping[str, str],
    studies_aggregate_keywords: Mapping[str, Sequence[str]],
    production_aggregate_keywords: Mapping[str, Sequence[str]],
    production_month_fields: Mapping[int, str],
    schema_field_synonyms: Callable[[str, str], Sequence[str]],
    contains_cierre_hint: Callable[[str], bool],
) -> str | None:
    if "cierre" in text or "cierres" in text or contains_cierre_hint(text):
        for label, field in closure_field_hints.items():
            if label in text:
                return f"cierre:{field}"
    if module == "estudios":
        for metric, aliases in studies_aggregate_keywords.items():
            if any(alias in text for alias in aliases) or any(alias in text for alias in schema_field_synonyms("estudios", metric)):
                return metric
        if any(token in text for token in ("importe medio", "importe promedio", "media de importe", "promedio de importe")):
            return "importecontratado"
        if year and any(field.startswith("importeContratado") for field in fields):
            return "importecontratado"
        return None
    if module == "produccion":
        for metric, aliases in production_aggregate_keywords.items():
            if any(alias in text for alias in aliases) or any(alias in text for alias in schema_field_synonyms("produccion", metric)):
                return metric
        for field in fields:
            if field in {"produccionTotal", *production_month_fields.values()}:
                return field.lower()
        if "produccion" in text:
            return "producciontotal"
    return None


def is_count_request(text: str) -> bool:
    count_markers = (
        "cuantas ",
        "cuantos ",
        "numero de ",
        "n de ",
        "cantidad de ",
        "cuenta de ",
    )
    if not any(marker in text for marker in count_markers):
        return False
    if any(entity in text for entity in ("ofertas", "licitaciones", "obras", "cierres", "estudios")):
        return True

    numeric_metric_markers = (
        "importe",
        "media",
        "medio",
        "promedio",
        "total",
        "suma",
        "cartera",
        "rentabilidad",
        "presupuesto",
        "coste",
        "costes",
    )
    return not any(marker in text for marker in numeric_metric_markers)


def detect_count_metric(text: str, module: str, *, contains_cierre_hint: Callable[[str], bool]) -> str:
    if "cierre" in text or "cierres" in text or contains_cierre_hint(text):
        return "cierre:count"
    return "obras" if module == "produccion" else "licitaciones"
