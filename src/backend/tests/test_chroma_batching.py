import sys
import tempfile
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rag_service
from rag_service import _collection_add_batched, _iter_add_batches


def test_iter_add_batches_splits_large_payload():
    documents = [f"doc-{i}" for i in range(5)]
    metadatas = [{"idx": i} for i in range(5)]
    ids = [f"id-{i}" for i in range(5)]

    batches = list(_iter_add_batches(documents, metadatas, ids, batch_size=2))

    assert [len(batch_docs) for batch_docs, _, _ in batches] == [2, 2, 1]
    assert batches[0][2] == ["id-0", "id-1"]
    assert batches[2][0] == ["doc-4"]


def test_iter_add_batches_rejects_mismatched_lengths():
    try:
        list(_iter_add_batches(["a"], [], ["id-1"], batch_size=2))
        assert False, "Expected ValueError for mismatched payload lengths"
    except ValueError as exc:
        assert "misma longitud" in str(exc)


def test_collection_add_batched_calls_collection_in_chunks():
    class DummyCollection:
        def __init__(self):
            self.calls = []

        def add(self, *, documents, metadatas, ids):
            self.calls.append(
                {
                    "documents": list(documents),
                    "metadatas": list(metadatas),
                    "ids": list(ids),
                }
            )

    collection = DummyCollection()
    documents = [f"doc-{i}" for i in range(5)]
    metadatas = [{"idx": i} for i in range(5)]
    ids = [f"id-{i}" for i in range(5)]

    _collection_add_batched(
        collection,
        documents=documents,
        metadatas=metadatas,
        ids=ids,
        batch_size=2,
    )

    assert [len(call["documents"]) for call in collection.calls] == [2, 2, 1]
    assert collection.calls[1]["ids"] == ["id-2", "id-3"]


def _reset_cache_globals():
    rag_service._embedding_cache.reset_runtime_state()


def test_chunk_cache_key_is_deterministic():
    from rag_service import _chunk_cache_key
    assert _chunk_cache_key("texto A") == _chunk_cache_key("texto A")
    assert _chunk_cache_key("texto A") != _chunk_cache_key("texto B")


def test_save_and_load_embedding_cache_roundtrip():
    from rag_service import _load_embedding_cache, _save_embedding_cache
    _reset_cache_globals()
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_file = Path(tmpdir) / "_embedding_cache.pkl"
        test_cache = rag_service.EmbeddingCache(cache_file, ef_version="test-v1", max_entries=10)
        with mock.patch.object(rag_service, "_embedding_cache", test_cache):
            rag_service._embedding_cache.put_many([("abc", [0.1, 0.2])])
            _save_embedding_cache()
            assert cache_file.exists()
            assert not rag_service._embedding_cache.dirty

            rag_service._embedding_cache.reset_runtime_state()
            _load_embedding_cache()
            assert rag_service._embedding_cache.get_many(["abc"]) == [[0.1, 0.2]]


def test_collection_add_batched_cache_miss_then_hit():
    """Primera llamada codifica; segunda reutiliza caché sin codificar."""
    _reset_cache_globals()

    class DummyCollection:
        def __init__(self):
            self.calls = []

        def add(self, *, documents, metadatas, ids, embeddings=None):
            self.calls.append({"documents": list(documents), "embeddings": embeddings})

    fake_emb = [0.5, 0.6]
    encode_calls = []

    def fake_encode(texts):
        encode_calls.append(texts)
        return [fake_emb[:] for _ in texts]

    col = DummyCollection()
    docs = ["chunk uno", "chunk dos"]
    metas = [{}, {}]
    ids = ["id-0", "id-1"]

    with (
        mock.patch.object(rag_service, "_embedding_fn", new=object()),
        mock.patch.object(rag_service, "_encode_passages", side_effect=fake_encode),
        mock.patch.object(
            rag_service,
            "_embedding_cache",
            rag_service.EmbeddingCache(Path("/nonexistent/_embedding_cache.pkl"), ef_version="test-v1", max_entries=10),
        ),
    ):
        # Primera llamada: cache miss → debe codificar
        _collection_add_batched(col, documents=docs, metadatas=metas, ids=ids, use_embedding_cache=True)
        assert len(encode_calls) == 1
        assert encode_calls[0] == docs

        # Segunda llamada con los mismos documentos: cache hit → no codifica
        col2 = DummyCollection()
        _collection_add_batched(col2, documents=docs, metadatas=metas, ids=ids, use_embedding_cache=True)
        assert len(encode_calls) == 1  # sin llamadas adicionales

        # Las embeddings llegan a ChromaDB en ambos casos
        assert col.calls[0]["embeddings"] == [fake_emb, fake_emb]
        assert col2.calls[0]["embeddings"] == [fake_emb, fake_emb]


def test_embedding_cache_prunes_old_entries():
    cache = rag_service.EmbeddingCache(Path("/nonexistent/cache.pkl"), ef_version="test-v1", max_entries=2)
    cache.put_many([
        ("a", [0.1]),
        ("b", [0.2]),
    ])
    cache.put_many([("c", [0.3])])

    assert cache.get_many(["a", "b", "c"]) == [None, [0.2], [0.3]]
