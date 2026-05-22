from typing import List, Tuple


def normalize_prefix(prefix: str) -> str:
    cleaned = (prefix or "").strip().strip("/")
    return f"{cleaned}/" if cleaned else ""


def configured_blob_scopes(
    default_prefix: str,
    alta_tension: str,
    baja_tension: str,
    guias_tecnicas: str,
    rite: str,
) -> List[Tuple[str, str]]:
    configured = [
        ("alta_tension", normalize_prefix(alta_tension)),
        ("baja_tension", normalize_prefix(baja_tension)),
        ("guias_tecnicas", normalize_prefix(guias_tecnicas)),
        ("rite", normalize_prefix(rite)),
    ]
    active = [(category, prefix) for category, prefix in configured if prefix]
    if active:
        return active
    return [("", normalize_prefix(default_prefix))]
