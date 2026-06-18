import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
