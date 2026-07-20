import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any


def is_explicit_client_field_filter(question_text: str) -> bool:
    return any(
        phrase in question_text
        for phrase in (
            "en su cliente",
            "en cliente",
            "cliente contiene",
            "cliente contenga",
            "del cliente ",
            "para el cliente ",
            "para cliente ",
        )
    )


def is_client_contains_query(question_text: str) -> bool:
    return is_explicit_client_field_filter(question_text) and any(
        token in question_text
        for token in ("contiene", "contienen", "incluye", "incluyen", "palabra", "texto", "cadena")
    )


def is_exact_client_target_query(question_text: str) -> bool:
    normalized = f" {question_text.strip()} "
    if any(
        phrase in normalized
        for phrase in (
            " del cliente ",
            " para el cliente ",
            " para la cliente ",
            " para cliente ",
            " cliente es ",
            " cliente sea ",
        )
    ):
        return True
    return bool(re.search(r"(?:^|\s)(?:y\s+)?para\s+[a-z][a-z0-9 .&/-]{1,40}\??\s*$", question_text))


def canonicalize_client_name(text: str, *, normalize: Callable[[str], str]) -> str:
    canonical = normalize(text).replace(".", " ")
    canonical = re.sub(
        r"\b(s a|sa|s l|sl|s l u|slu|s a u|sau|sociedad anonima|sociedad limitada|sociedad limitada unipersonal)\b",
        " ",
        canonical,
    )
    return re.sub(r"\s+", " ", canonical).strip()


def apply_client_filter(
    matches: Sequence[Mapping[str, Any]],
    filter_text: str,
    *,
    exact_client_target: bool,
    normalize: Callable[[str], str],
) -> list[Mapping[str, Any]]:
    if not matches:
        return []
    needle = normalize(filter_text)
    if not needle:
        return list(matches)
    if not exact_client_target:
        return [item for item in matches if needle in normalize(str(item.get("Cliente") or ""))]

    canonical_needle = canonicalize_client_name(filter_text, normalize=normalize)
    exact_matches = [
        item
        for item in matches
        if canonicalize_client_name(str(item.get("Cliente") or ""), normalize=normalize) == canonical_needle
    ]
    if exact_matches:
        return exact_matches

    return [
        item
        for item in matches
        if canonical_needle
        and re.search(
            rf"(?<!\w){re.escape(canonical_needle)}(?!\w)",
            canonicalize_client_name(str(item.get("Cliente") or ""), normalize=normalize),
        )
    ]
