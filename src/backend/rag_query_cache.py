import threading
import time
from collections import OrderedDict
from typing import Callable, List, Tuple


class QueryCache:
    """Cache LRU thread-safe con TTL por entrada."""

    def __init__(self, *, normalize_text: Callable[[str], str], max_size: int, ttl: float):
        self._normalize_text = normalize_text
        self._cache: OrderedDict[str, Tuple[float, object]] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl
        self._lock = threading.Lock()

    def _key(
        self,
        question: str,
        n_results: int,
        domain: str = "",
        hint_domains: List[str] | None = None,
        hint_document_variants: List[str] | None = None,
        hint_article_refs: List[str] | None = None,
        hint_it_section_refs: List[str] | None = None,
    ) -> str:
        normalized = self._normalize_text(question.strip())
        hints = ",".join(sorted(hint_domains)) if hint_domains else ""
        variant_hints = ",".join(sorted(hint_document_variants)) if hint_document_variants else ""
        article_hints = ",".join(sorted(hint_article_refs)) if hint_article_refs else ""
        it_hints = ",".join(sorted(hint_it_section_refs)) if hint_it_section_refs else ""
        return f"{normalized}::{n_results}::{self._normalize_text(domain)}::{hints}::{variant_hints}::{article_hints}::{it_hints}"

    def get(
        self,
        question: str,
        n_results: int,
        domain: str = "",
        hint_domains: List[str] | None = None,
        hint_document_variants: List[str] | None = None,
        hint_article_refs: List[str] | None = None,
        hint_it_section_refs: List[str] | None = None,
    ):
        key = self._key(question, n_results, domain, hint_domains, hint_document_variants, hint_article_refs, hint_it_section_refs)
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            timestamp, value = entry
            if time.monotonic() - timestamp > self._ttl:
                self._cache.pop(key, None)
                return None
            self._cache.move_to_end(key)
            return value

    def put(
        self,
        question: str,
        n_results: int,
        value: object,
        domain: str = "",
        hint_domains: List[str] | None = None,
        hint_document_variants: List[str] | None = None,
        hint_article_refs: List[str] | None = None,
        hint_it_section_refs: List[str] | None = None,
    ) -> None:
        key = self._key(question, n_results, domain, hint_domains, hint_document_variants, hint_article_refs, hint_it_section_refs)
        with self._lock:
            self._cache[key] = (time.monotonic(), value)
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
