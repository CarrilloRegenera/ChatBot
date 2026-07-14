import re
import unicodedata


def normalize_for_matching(text: str, punctuation_pattern: str) -> str:
    normalized = unicodedata.normalize("NFD", text or "")
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    normalized = normalized.lower().strip()
    normalized = re.sub(punctuation_pattern, " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized
