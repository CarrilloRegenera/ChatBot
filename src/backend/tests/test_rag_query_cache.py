import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag_query_cache import QueryCache


def _normalize(value: str) -> str:
    return value.lower().strip()


def test_query_cache_normalizes_keys_and_includes_hints():
    cache = QueryCache(normalize_text=_normalize, max_size=2, ttl=60)
    cache.put(" Consulta ", 3, "cached", hint_domains=["rite"])

    assert cache.get("consulta", 3, hint_domains=["rite"]) == "cached"
    assert cache.get("consulta", 3, hint_domains=["rebt"]) is None


def test_query_cache_evicts_the_least_recently_used_value():
    cache = QueryCache(normalize_text=_normalize, max_size=2, ttl=60)
    cache.put("one", 1, 1)
    cache.put("two", 1, 2)
    assert cache.get("one", 1) == 1
    cache.put("three", 1, 3)

    assert cache.get("two", 1) is None
    assert cache.get("one", 1) == 1
    assert cache.get("three", 1) == 3
