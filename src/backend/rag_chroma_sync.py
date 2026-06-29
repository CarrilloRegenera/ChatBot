import hashlib
from pathlib import Path
from typing import Callable, Dict, Iterable, List


def file_hash(filepath: str) -> str:
    digest = hashlib.md5()
    with open(filepath, "rb") as fh:
        for block in iter(lambda: fh.read(8192), b""):
            digest.update(block)
    return digest.hexdigest()


def get_indexed_sources(collection, *, batch_size: int = 5000) -> Dict[str, str]:
    total = collection.count()
    if total == 0:
        return {}
    sources: Dict[str, str] = {}
    for offset in range(0, total, batch_size):
        results = collection.get(include=["metadatas"], limit=batch_size, offset=offset)
        for meta in (results.get("metadatas") or []):
            source = meta.get("source", "")
            if source and source not in sources:
                sources[source] = meta.get("file_hash", "")
    return sources


def delete_source_chunks(source_name: str, *, collections: Iterable) -> None:
    for collection in collections:
        results = collection.get(where={"source": source_name}, include=[])
        if results["ids"]:
            collection.delete(ids=results["ids"])


def iter_add_batches(
    documents: List[str],
    metadatas: List[Dict[str, object]],
    ids: List[str],
    *,
    batch_size: int,
):
    if len(documents) != len(metadatas) or len(documents) != len(ids):
        raise ValueError("documents, metadatas e ids deben tener la misma longitud")
    safe_batch_size = max(1, int(batch_size or 1))
    for start in range(0, len(documents), safe_batch_size):
        end = start + safe_batch_size
        yield documents[start:end], metadatas[start:end], ids[start:end]


def add_batched_to_collection(
    target_collection,
    *,
    documents: List[str],
    metadatas: List[Dict[str, object]],
    ids: List[str],
    batch_size: int,
    use_embedding_cache: bool,
    embedding_fn,
    embedding_cache,
    encode_passages: Callable[[List[str]], List[List[float]]],
    cache_key_for_text: Callable[[str], str],
) -> None:
    if use_embedding_cache and embedding_fn is not None:
        if len(documents) != len(metadatas) or len(documents) != len(ids):
            raise ValueError("documents, metadatas e ids deben tener la misma longitud")
        embedding_cache.load()
        keys = [cache_key_for_text(doc) for doc in documents]
        precomputed = embedding_cache.get_many(keys)
        miss_indices = [i for i, emb in enumerate(precomputed) if emb is None]
        if miss_indices:
            new_embs = encode_passages([documents[i] for i in miss_indices])
            cache_updates = []
            for idx, emb in zip(miss_indices, new_embs):
                precomputed[idx] = emb
                cache_updates.append((keys[idx], emb))
            embedding_cache.put_many(cache_updates)
        safe_batch = max(1, int(batch_size or 1))
        for start in range(0, len(documents), safe_batch):
            end = start + safe_batch
            target_collection.add(
                documents=documents[start:end],
                metadatas=metadatas[start:end],
                ids=ids[start:end],
                embeddings=precomputed[start:end],
            )
        return

    for docs_batch, meta_batch, ids_batch in iter_add_batches(
        documents,
        metadatas,
        ids,
        batch_size=batch_size,
    ):
        target_collection.add(
            documents=docs_batch,
            metadatas=meta_batch,
            ids=ids_batch,
        )


def discover_current_files(root_path: Path, *, recursive_pdf_scan: bool) -> Dict[str, Path]:
    pdf_paths = sorted(root_path.rglob("*.pdf")) if recursive_pdf_scan else sorted(root_path.glob("*.pdf"))
    md_paths = sorted(root_path.rglob("*.md")) if recursive_pdf_scan else sorted(root_path.glob("*.md"))
    md_by_stem = {(p.parent, p.stem): p for p in md_paths}
    filtered_pdf_paths = [p for p in pdf_paths if (p.parent, p.stem) not in md_by_stem]
    return {
        str(p.relative_to(root_path)).replace("\\", "/"): p
        for p in sorted(filtered_pdf_paths + list(md_paths))
    }
