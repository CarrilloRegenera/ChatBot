import hashlib
import logging
import os
import pickle
from collections import OrderedDict
from pathlib import Path
from typing import Iterable


logger = logging.getLogger(__name__)


def _rag_index_version_tag(value: str) -> str:
    import re

    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value or "").strip("-") or "1"


class EmbeddingCache:
    def __init__(
        self,
        cache_file: str | Path,
        *,
        ef_version: str,
        max_entries: int = 20000,
        enabled: bool = True,
    ) -> None:
        self.cache_file = Path(cache_file)
        self.ef_version = ef_version
        self.max_entries = max(1, int(max_entries))
        self.enabled = enabled
        self._cache: "OrderedDict[str, list[float]]" = OrderedDict()
        self._loaded = False
        self._dirty = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def dirty(self) -> bool:
        return self._dirty

    @property
    def size(self) -> int:
        return len(self._cache)

    def key_for_text(self, text: str) -> str:
        return hashlib.sha256(f"{self.ef_version}|{text}".encode("utf-8")).hexdigest()

    def load(self) -> None:
        if self._loaded or not self.enabled:
            self._loaded = True
            return
        try:
            with self.cache_file.open("rb") as fh:
                data = pickle.load(fh)
            if isinstance(data, dict):
                ordered = OrderedDict()
                for key, value in data.items():
                    if isinstance(key, str) and isinstance(value, list):
                        ordered[key] = value
                self._cache = ordered
                self._prune_if_needed()
                logger.debug("Cache de embeddings cargada: %d entradas", len(self._cache))
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning("No se pudo cargar cache de embeddings: %s", exc)
        self._loaded = True

    def save(self) -> None:
        if not self.enabled or not self._dirty:
            return
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.cache_file.with_suffix(f"{self.cache_file.suffix}.tmp")
            with tmp_path.open("wb") as fh:
                pickle.dump(dict(self._cache), fh, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp_path, self.cache_file)
            self._dirty = False
            logger.debug("Cache de embeddings guardada: %d entradas", len(self._cache))
        except Exception as exc:
            logger.warning("No se pudo guardar cache de embeddings: %s", exc)

    def reset_runtime_state(self) -> None:
        self._cache = OrderedDict()
        self._loaded = False
        self._dirty = False

    def get_many(self, keys: Iterable[str]) -> list[list[float] | None]:
        self.load()
        values: list[list[float] | None] = []
        for key in keys:
            value = self._cache.get(key)
            if value is not None:
                self._cache.move_to_end(key)
            values.append(value)
        return values

    def put_many(self, items: Iterable[tuple[str, list[float]]]) -> None:
        self.load()
        changed = False
        for key, value in items:
            self._cache[key] = value
            self._cache.move_to_end(key)
            changed = True
        if changed:
            self._dirty = True
            self._prune_if_needed()

    def _prune_if_needed(self) -> None:
        while len(self._cache) > self.max_entries:
            self._cache.popitem(last=False)
