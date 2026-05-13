from typing import Optional

import chromadb
from config import CHROMA_DB_PATH

_client: Optional[chromadb.api.ClientAPI] = None


def get_chroma_client() -> chromadb.api.ClientAPI:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    return _client
