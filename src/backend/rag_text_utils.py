import re
import unicodedata
from typing import List


def tokenize(text: str) -> List[str]:
    return re.findall(r"[^\W\d_]{4,}", text.lower(), flags=re.UNICODE)


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text)
    return "".join(character for character in normalized if unicodedata.category(character) != "Mn").lower()
