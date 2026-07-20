from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any


def looks_like_recent_listing_query(question_text: str) -> bool:
    return any(token in question_text for token in ("recientes", "mas recientes", "ultimas", "ultimos"))


def looks_like_ranked_listing_query(question_text: str) -> bool:
    return any(token in question_text for token in ("top", "ranking"))


def looks_like_active_production_listing_query(question_text: str) -> bool:
    return any(token in question_text for token in ("en curso", "actualmente", "activa", "activas"))


def sort_estudios_listing_matches(
    matches: Sequence[Mapping[str, Any]],
    *,
    question_text: str,
    parse_decimal: Callable[[Any], float | None],
) -> list[Mapping[str, Any]]:
    if looks_like_recent_listing_query(question_text):
        return sorted(
            matches,
            key=lambda item: (
                item.get("FechaAdjudicacion")
                or item.get("FechaPresentacion")
                or item.get("UpdatedDate")
                or item.get("CreatedDate")
                or datetime.min
            ),
            reverse=True,
        )
    if looks_like_ranked_listing_query(question_text):
        return sorted(
            matches,
            key=lambda item: (
                parse_decimal(item.get("ImporteContratado")) or 0.0,
                parse_decimal(item.get("Produccion")) or 0.0,
                parse_decimal(item.get("Plan2026")) or 0.0,
            ),
            reverse=True,
        )
    return list(matches)


def sort_produccion_listing_matches(
    matches: Sequence[Mapping[str, Any]],
    *,
    question_text: str,
    normalize: Callable[[str], str],
    parse_decimal: Callable[[Any], float | None],
) -> list[Mapping[str, Any]]:
    ordered = list(matches)
    if looks_like_active_production_listing_query(question_text):
        ordered = [
            item
            for item in ordered
            if item.get("Finalizada") in (False, 0, None)
            and normalize(str(item.get("Estado") or "")).strip() not in {"completada", "finalizada"}
        ]
    if looks_like_ranked_listing_query(question_text):
        return sorted(
            ordered,
            key=lambda item: (
                parse_decimal(item.get("ImporteContratado")) or 0.0,
                parse_decimal(item.get("Cartera2026")) or 0.0,
                parse_decimal(item.get("Pendiente2026")) or 0.0,
            ),
            reverse=True,
        )
    if looks_like_recent_listing_query(question_text):
        return sorted(
            ordered,
            key=lambda item: item.get("UpdatedDate") or item.get("CreatedDate") or datetime.min,
            reverse=True,
        )
    return ordered
