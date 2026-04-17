import os
import re
import unicodedata
from typing import List, Tuple

import chromadb
import fitz

from config import CHROMA_DB_PATH, DOCUMENTS_PATH, TOP_K_RESULTS


CHUNK_SIZE = 700
CHUNK_OVERLAP = 120
MIN_CHUNK_LENGTH = 80
COLLECTION_NAME = "reglamentos"
STOPWORDS = {
    "como", "donde", "cuando", "cuales", "cuanto", "sobre", "segun",
    "para", "desde", "hasta", "esta", "este", "estas", "estos", "debe",
    "deben", "instalacion", "instalaciones", "baja", "tension",
    "reglamento", "reglamentos", "normativa", "normativas", "deber",
}


chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
collection = chroma_client.get_or_create_collection(name=COLLECTION_NAME)


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

    pdf_files = sorted(name for name in os.listdir(folder_path) if name.lower().endswith(".pdf"))

    for filename in pdf_files:
        filepath = os.path.join(folder_path, filename)
        pdf = fitz.open(filepath)

        try:
            for page_index, page in enumerate(pdf):
                text = page.get_text("text")
                chunks = _split_text(text)

                for chunk_index, chunk in enumerate(chunks, start=1):
                    count += 1
                    documents.append(chunk)
                    metadatas.append(
                        {
                            "source": filename,
                            "page": page_index + 1,
                            "chunk": chunk_index,
                        }
                    )
                    ids.append(f"{filename}-{page_index + 1}-{chunk_index}")
        finally:
            pdf.close()

    if documents:
        collection.add(documents=documents, metadatas=metadatas, ids=ids)

    return count


def search_documents(question: str, n_results: int = TOP_K_RESULTS) -> Tuple[str, List[str]]:
    if not question.strip():
        return "", []

    if collection.count() == 0:
        try:
            load_documents()
        except Exception:
            return "", []
        if collection.count() == 0:
            return "", []

    n_results = max(n_results, 5)
    candidate_count = min(max(n_results * 3, 12), 25)
    results = collection.query(query_texts=[question], n_results=candidate_count)

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    ids = results.get("ids", [[]])[0]

    question_tokens = set(_tokenize(question))
    question_keywords = {t for t in question_tokens if t not in STOPWORDS and len(t) >= 6}
    ranked_items = []
    seen_ids = set()

    for doc_id, document, metadata in zip(ids, documents, metadatas):
        if not document or not metadata:
            continue

        doc_tokens = set(_tokenize(document))
        overlap_score = len(question_tokens.intersection(doc_tokens))
        doc_norm = _normalize_text(document)
        keyword_hits = sum(1 for kw in question_keywords if _normalize_text(kw) in doc_norm)
        score = overlap_score + (keyword_hits * 3)
        ranked_items.append((score, document, metadata))
        seen_ids.add(doc_id)

    # Keyword fallback: pull extra chunks that literally contain key terms.
    for keyword in list(question_keywords)[:4]:
        normalized_kw = _normalize_text(keyword)
        try:
            lexical_hits = collection.get(
                where_document={"$contains": normalized_kw},
                include=["documents", "metadatas"],
                limit=8,
            )
        except Exception:
            continue

        lex_docs = lexical_hits.get("documents", [])
        lex_meta = lexical_hits.get("metadatas", [])
        lex_ids = lexical_hits.get("ids", [])

        for doc_id, document, metadata in zip(lex_ids, lex_docs, lex_meta):
            if doc_id in seen_ids or not document or not metadata:
                continue
            ranked_items.append((10, document, metadata))
            seen_ids.add(doc_id)

    if not ranked_items:
        return "", []

    ranked_items.sort(key=lambda item: item[0], reverse=True)
    selected = ranked_items[:n_results]

    context_parts = []
    sources = []
    seen_sources = set()

    for _, document, metadata in selected:
        context_parts.append(document)

        source_label = f"{metadata['source']} (pag. {metadata['page']})"
        if source_label not in seen_sources:
            sources.append(source_label)
            seen_sources.add(source_label)

    return "\n\n".join(context_parts), sources
