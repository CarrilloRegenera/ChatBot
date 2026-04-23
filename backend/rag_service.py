import logging
import os
import re
import unicodedata
from pathlib import Path
from typing import List, Tuple

import chromadb
import fitz
from sentence_transformers import SentenceTransformer
from sentence_transformers.util import cos_sim

from config import (
    CHROMA_DB_PATH,
    COLLECTION_NAME,
    DOCUMENTS_PATH,
    ENABLE_RERANK,
    MAX_CHUNKS_PER_SOURCE,
    RECURSIVE_PDF_SCAN,
    RERANK_MODEL,
    RERANK_WEIGHT,
    STOPWORDS,
    TOP_K_RESULTS,
)


CHUNK_SIZE = 700
CHUNK_OVERLAP = 120
MIN_CHUNK_LENGTH = 80
CORE_TERM_PENALTY = 8


logger = logging.getLogger(__name__)
chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
collection = chroma_client.get_or_create_collection(name=COLLECTION_NAME)
rerank_model = None
if ENABLE_RERANK:
    try:
        rerank_model = SentenceTransformer(RERANK_MODEL)
    except Exception as exc:
        logger.warning("Rerank deshabilitado (no se pudo cargar '%s'): %s", RERANK_MODEL, str(exc))


def _split_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    clean_text = " ".join(text.split())
    if not clean_text:
        return []

    chunks = []
    step = max(chunk_size - overlap, 1)
    for start in range(0, len(clean_text), step):
        chunk = clean_text[start:start + chunk_size].strip()
        if len(chunk) >= MIN_CHUNK_LENGTH:
            chunks.append(chunk)
    return chunks


def _collection_has_documents() -> bool:
    return collection.count() > 0


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[^\W\d_]{4,}", text.lower(), flags=re.UNICODE)


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn").lower()


def _clean_question(question: str) -> str:
    cleaned = question
    cleaned = re.sub(r"\b[\w\-/]+\.pdf\s*\(pag\.\s*\d+\)\s*", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b[\w\-/]+\.pdf\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or question.strip()


def _extract_core_terms(question_keywords: set[str]) -> List[str]:
    ranked = sorted(question_keywords, key=lambda term: (len(term), term), reverse=True)
    return ranked[:4]


def _candidate_window(base_results: int, question_keywords: set[str], clean_question: str) -> int:
    complexity_bonus = 0
    if len(question_keywords) >= 4:
        complexity_bonus += 10
    if any(ch.isdigit() for ch in clean_question):
        complexity_bonus += 8
    return min(max(base_results * 4, 16) + complexity_bonus, 70)


def _focus_hits(document: str, core_terms: List[str]) -> int:
    if not core_terms:
        return 0
    doc_norm = _normalize_text(document)
    return sum(1 for core in core_terms if core in doc_norm)


def reset_documents() -> None:
    chroma_client.delete_collection(COLLECTION_NAME)
    global collection
    collection = chroma_client.get_or_create_collection(name=COLLECTION_NAME)


def load_documents(folder_path: str = DOCUMENTS_PATH, reset: bool = False) -> int:
    if reset and _collection_has_documents():
        reset_documents()

    if not os.path.isdir(folder_path):
        raise FileNotFoundError(f"No existe la carpeta de documentos: {folder_path}")

    if _collection_has_documents() and not reset:
        return collection.count()

    documents = []
    metadatas = []
    ids = []
    count = 0

    root_path = Path(folder_path)
    pdf_paths = sorted(root_path.rglob("*.pdf")) if RECURSIVE_PDF_SCAN else sorted(root_path.glob("*.pdf"))

    for filepath in pdf_paths:
        source_name = str(filepath.relative_to(root_path)).replace("\\", "/")
        pdf = fitz.open(str(filepath))
        try:
            for page_index, page in enumerate(pdf):
                text = page.get_text("text")
                for chunk_index, chunk in enumerate(_split_text(text), start=1):
                    count += 1
                    documents.append(chunk)
                    metadatas.append(
                        {
                            "source": source_name,
                            "folder": str(filepath.parent).replace("\\", "/"),
                            "page": page_index + 1,
                            "chunk": chunk_index,
                        }
                    )
                    ids.append(f"{source_name}-{page_index + 1}-{chunk_index}")
        finally:
            pdf.close()

    if documents:
        collection.add(documents=documents, metadatas=metadatas, ids=ids)

    return count


def search_documents(question: str, n_results: int = TOP_K_RESULTS) -> Tuple[str, List[str]]:
    if not question.strip():
        return "", []

    clean_question = _clean_question(question)
    if collection.count() == 0:
        try:
            load_documents()
        except Exception:
            return "", []
        if collection.count() == 0:
            return "", []

    n_results = max(n_results, 5)
    question_tokens = set(_tokenize(clean_question))
    question_keywords = {token for token in question_tokens if token not in STOPWORDS and len(token) >= 5}
    core_terms = [_normalize_text(term) for term in _extract_core_terms(question_keywords)]
    normalized_question = _normalize_text(clean_question)

    candidate_count = _candidate_window(n_results, question_keywords, clean_question)
    results = collection.query(query_texts=[clean_question], n_results=candidate_count)
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    ids = results.get("ids", [[]])[0]

    ranked_items = []
    seen_ids = set()

    for doc_id, document, metadata in zip(ids, documents, metadatas):
        if not document or not metadata:
            continue

        doc_norm = _normalize_text(document)
        doc_tokens = set(_tokenize(document))
        overlap_score = len(question_tokens.intersection(doc_tokens))
        keyword_hits = sum(1 for kw in question_keywords if _normalize_text(kw) in doc_norm)
        core_hits = sum(1 for core in core_terms if core in doc_norm)

        score = overlap_score + (keyword_hits * 2) + (core_hits * 3)
        if normalized_question and normalized_question in doc_norm:
            score += 6
        if core_terms and core_hits == 0:
            score -= CORE_TERM_PENALTY
        if overlap_score == 0 and core_hits == 0:
            score -= 6

        ranked_items.append((score, doc_id, document, metadata))
        seen_ids.add(doc_id)

    for term in core_terms:
        try:
            lexical_hits = collection.get(
                where_document={"$contains": term},
                include=["documents", "metadatas"],
                limit=8,
            )
        except Exception:
            continue

        for doc_id, document, metadata in zip(
            lexical_hits.get("ids", []),
            lexical_hits.get("documents", []),
            lexical_hits.get("metadatas", []),
        ):
            if doc_id in seen_ids or not document or not metadata:
                continue
            doc_norm = _normalize_text(document)
            lexical_score = 8
            if core_terms and not any(core in doc_norm for core in core_terms):
                lexical_score -= CORE_TERM_PENALTY
            ranked_items.append((lexical_score, doc_id, document, metadata))
            seen_ids.add(doc_id)

    if not ranked_items:
        return "", []

    if rerank_model:
        query_embedding = rerank_model.encode(clean_question, convert_to_tensor=True)
        candidate_texts = [item[2] for item in ranked_items]
        candidate_embeddings = rerank_model.encode(candidate_texts, convert_to_tensor=True)
        similarities = cos_sim(query_embedding, candidate_embeddings)[0].tolist()

        reranked = []
        for item, sem_score in zip(ranked_items, similarities):
            base_score, doc_id, document, metadata = item
            final_score = base_score + (float(sem_score) * RERANK_WEIGHT)
            reranked.append((final_score, doc_id, document, metadata))
        ranked_items = reranked

    ranked_items.sort(key=lambda item: item[0], reverse=True)

    selected = []
    selected_ids = set()
    source_counts = {}
    for item in ranked_items:
        _, doc_id, _, metadata = item
        source_name = metadata.get("source", "unknown")
        if source_counts.get(source_name, 0) >= MAX_CHUNKS_PER_SOURCE:
            continue
        selected.append(item)
        selected_ids.add(doc_id)
        source_counts[source_name] = source_counts.get(source_name, 0) + 1
        if len(selected) >= n_results:
            break

    if len(selected) < n_results:
        for item in ranked_items:
            _, doc_id, _, _ = item
            if doc_id in selected_ids:
                continue
            selected.append(item)
            selected_ids.add(doc_id)
            if len(selected) >= n_results:
                break

    # Focus pass: prioritize chunks that directly contain core terms.
    if core_terms and selected:
        focused = []
        non_focused = []
        for item in selected:
            _, _, document, _ = item
            if _focus_hits(document, core_terms) > 0:
                focused.append(item)
            else:
                non_focused.append(item)

        if focused:
            focused.sort(key=lambda item: _focus_hits(item[2], core_terms), reverse=True)
            selected = (focused + non_focused)[:n_results]

    context_parts = []
    sources = []
    seen_sources = set()
    for _, _, document, metadata in selected:
        context_parts.append(document)
        source_label = f"{metadata['source']} (pag. {metadata['page']})"
        if source_label not in seen_sources:
            sources.append(source_label)
            seen_sources.add(source_label)

    return "\n\n".join(context_parts), sources
