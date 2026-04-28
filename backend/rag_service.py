import hashlib
import logging
import os
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Tuple

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


CHUNK_SIZE = 950
CHUNK_OVERLAP = 200
MIN_CHUNK_LENGTH = 80
CORE_TERM_PENALTY = 4
MAX_TOPIC_TOKENS = 6
SECTION_LABEL_MAX_LENGTH = 80
HEADING_LINE_MAX_WORDS = 10
NUMERIC_PRIORITY_BOOST = 6
REFERENCE_PRIORITY_BOOST = 5
SECTION_PRIORITY_BOOST = 8
SECTION_TITLE_BOOST = 6
COMPARISON_PRIORITY_BOOST = 3
LOW_SIGNAL_PENALTY = 5
CHUNK_SENTENCE_GRACE = 180
HEADING_PATTERN = re.compile(r"^(?:\d+(?:\.\d+)*[\.\)]?\s+)?[A-ZÁÉÍÓÚÜÑ][A-ZÁÉÍÓÚÜÑa-záéíóúüñ\s\-/,:()]{3,}$")
REFERENCE_PATTERN = re.compile(r"\b(?:itc[-\s]*bt[-\s]*\d+|art(?:iculo)?\.?\s*\d+|tabla\s*\d+)\b", re.IGNORECASE)
NUMERIC_PATTERN = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:m2|mm2|kw|w|v|a|ma|kv|hz|ohmios?|ohm|%)?\b", re.IGNORECASE)


logger = logging.getLogger(__name__)

_st_model = None
try:
    _st_model = SentenceTransformer(RERANK_MODEL)
except Exception as exc:
    logger.warning("No se pudo cargar modelo multilingüe '%s': %s", RERANK_MODEL, str(exc))

rerank_model = _st_model if ENABLE_RERANK else None


class _MultilingualEF:
    def name(self) -> str:
        return "multilingual-minilm"

    def _encode(self, input: List[str]) -> List[List[float]]:
        if _st_model is None:
            raise RuntimeError("Modelo de embeddings no disponible")
        return _st_model.encode(input, convert_to_numpy=True).tolist()

    def __call__(self, input: List[str]) -> List[List[float]]:
        return self._encode(input)

    def embed_documents(self, input: List[str]) -> List[List[float]]:
        return self._encode(input)

    def embed_query(self, input: List[str]) -> List[List[float]]:
        return self._encode(input)


_EF_VERSION = f"multilingual-minilm-v1-c{CHUNK_SIZE}-o{CHUNK_OVERLAP}"


def _get_or_reset_collection(client: chromadb.PersistentClient, name: str, ef) -> chromadb.Collection:
    try:
        col = client.get_or_create_collection(name=name, embedding_function=ef, metadata={"ef_version": _EF_VERSION})
        if (col.metadata or {}).get("ef_version") != _EF_VERSION:
            raise ValueError("ef_version mismatch")
        return col
    except Exception:
        logger.info("Embedding distinto al indexado — borrando y recreando colección")
        try:
            client.delete_collection(name)
        except Exception:
            pass
        return client.create_collection(name=name, embedding_function=ef, metadata={"ef_version": _EF_VERSION})


chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
_embedding_fn = _MultilingualEF() if _st_model else None
collection = _get_or_reset_collection(chroma_client, COLLECTION_NAME, _embedding_fn)


def _find_chunk_boundary(text: str, chunk_size: int = CHUNK_SIZE, grace: int = CHUNK_SENTENCE_GRACE) -> int:
    if len(text) <= chunk_size:
        return len(text)

    forward_limit = min(len(text), chunk_size + grace)
    for idx in range(chunk_size, forward_limit):
        if text[idx] in ".;:!?" and (idx + 1 == len(text) or text[idx + 1].isspace()):
            return idx + 1

    backward_limit = max(MIN_CHUNK_LENGTH, chunk_size - grace)
    for idx in range(chunk_size - 1, backward_limit - 1, -1):
        if text[idx] in ".;:!?" and (idx + 1 == len(text) or text[idx + 1].isspace()):
            return idx + 1

    for idx in range(chunk_size, min(len(text), chunk_size + 80)):
        if text[idx].isspace():
            return idx

    for idx in range(chunk_size - 1, max(MIN_CHUNK_LENGTH, chunk_size - 80) - 1, -1):
        if text[idx].isspace():
            return idx

    return chunk_size


def _split_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    blocks = _extract_text_blocks(text)
    if not blocks:
        return []

    chunks = []
    current = ""
    current_section = ""

    for block in blocks:
        block_text = block["text"]
        block_section = block["section"]
        candidate = f"{current} {block_text}".strip() if current else block_text
        if len(candidate) <= chunk_size:
            current = candidate
            current_section = current_section or block_section
            continue

        if current and len(current) >= MIN_CHUNK_LENGTH:
            chunks.append(_format_chunk(current, current_section))

        overlap_tail = current[-overlap:].strip() if current else ""
        current = f"{overlap_tail} {block_text}".strip() if overlap_tail else block_text
        current_section = block_section

        while len(current) > chunk_size:
            boundary = _find_chunk_boundary(current, chunk_size=chunk_size)
            partial = current[:boundary].strip()
            if len(partial) >= MIN_CHUNK_LENGTH:
                chunks.append(_format_chunk(partial, current_section))
            next_start = max(boundary - overlap, 1)
            current = current[next_start:].strip()

    if current and len(current) >= MIN_CHUNK_LENGTH:
        chunks.append(_format_chunk(current, current_section))

    return chunks



def _tokenize(text: str) -> List[str]:
    return re.findall(r"[^\W\d_]{4,}", text.lower(), flags=re.UNICODE)


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn").lower()


def _clean_line(line: str) -> str:
    return " ".join(line.split()).strip()


def _is_heading(line: str) -> bool:
    cleaned = _clean_line(line)
    if not cleaned:
        return False
    if len(cleaned.split()) > HEADING_LINE_MAX_WORDS:
        return False
    if len(cleaned) > SECTION_LABEL_MAX_LENGTH:
        return False
    if cleaned.endswith("."):
        return False
    if "," in cleaned and len(cleaned.split()) > 6:
        return False
    return bool(HEADING_PATTERN.match(cleaned))


def _sanitize_section_label(section: str) -> str:
    cleaned = _clean_line(section)
    if not cleaned:
        return ""
    if len(cleaned) > SECTION_LABEL_MAX_LENGTH:
        return ""
    if cleaned.endswith("."):
        return ""
    if "," in cleaned and len(cleaned.split()) > 6:
        return ""
    return cleaned


def _extract_text_blocks(text: str) -> List[Dict[str, str]]:
    lines = [line.rstrip() for line in text.splitlines()]
    blocks = []
    current_lines = []
    current_section = ""

    def flush_block() -> None:
        nonlocal current_lines, current_section
        block_text = _clean_line(" ".join(current_lines))
        # No se filtra por MIN_CHUNK_LENGTH aquí: bloques cortos (celdas de tabla,
        # valores numéricos aislados) se acumulan en _split_text con el texto circundante.
        if block_text:
            blocks.append({"text": block_text, "section": current_section})
        current_lines = []

    for raw_line in lines:
        line = _clean_line(raw_line)
        if not line:
            if current_lines:
                flush_block()
            continue

        if _is_heading(line):
            if current_lines:
                flush_block()
            current_section = _sanitize_section_label(line[:SECTION_LABEL_MAX_LENGTH])
            continue

        current_lines.append(line)

    if current_lines:
        flush_block()

    if not blocks:
        clean_text = " ".join(text.split())
        if clean_text:
            blocks.append({"text": clean_text, "section": ""})

    return blocks


def _format_chunk(text: str, section: str) -> str:
    clean_text = " ".join(text.split()).strip()
    if not section:
        return clean_text
    if clean_text.lower().startswith(section.lower()):
        return clean_text
    return f"{section}. {clean_text}"


def _clean_question(question: str) -> str:
    cleaned = question
    cleaned = re.sub(r"\b[\w\-/]+\.pdf\s*\(pag\.\s*\d+\)\s*", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b[\w\-/]+\.pdf\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or question.strip()


def _extract_numeric_terms(text: str) -> List[str]:
    return [match.group(0).strip().lower() for match in NUMERIC_PATTERN.finditer(text or "")]


def _extract_reference_terms(text: str) -> List[str]:
    return [match.group(0).strip().lower() for match in REFERENCE_PATTERN.finditer(text or "")]


def _extract_topic_terms(text: str, limit: int = MAX_TOPIC_TOKENS) -> List[str]:
    tokens = []
    seen = set()
    for token in _tokenize(text):
        normalized = _normalize_text(token)
        if normalized in STOPWORDS or len(normalized) < 5 or normalized in seen:
            continue
        seen.add(normalized)
        tokens.append(normalized)
        if len(tokens) >= limit:
            break
    return tokens


def _build_query_profile(clean_question: str, question_keywords: set[str]) -> Dict[str, object]:
    normalized = _normalize_text(clean_question)
    return {
        "normalized_question": normalized,
        "numeric_terms": _extract_numeric_terms(clean_question),
        "reference_terms": _extract_reference_terms(clean_question),
        "comparison": any(term in normalized for term in ("compara", "diferencia", "frente", "versus")),
        "expects_numeric": bool(re.search(r"\b(cuanto|cuantos|cuantas|valor|limite|potencia|resistencia|ohm|kw|mm2|m2|volt|amper|porcentaje)\b", normalized)),
        "question_keywords": question_keywords,
        "section_terms": _extract_topic_terms(clean_question, limit=MAX_TOPIC_TOKENS),
    }


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


def _metadata_text(metadata: Dict[str, object]) -> str:
    return _normalize_text(
        " ".join(
            str(metadata.get(key, ""))
            for key in ("source", "section", "topics")
        )
    )


def reset_documents() -> None:
    chroma_client.delete_collection(COLLECTION_NAME)
    global collection
    collection = chroma_client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=_embedding_fn,
    )


def _file_hash(filepath: str) -> str:
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for block in iter(lambda: f.read(8192), b""):
            h.update(block)
    return h.hexdigest()


def _get_indexed_sources() -> Dict[str, str]:
    if collection.count() == 0:
        return {}
    results = collection.get(include=["metadatas"])
    sources: Dict[str, str] = {}
    for meta in (results.get("metadatas") or []):
        source = meta.get("source", "")
        if source and source not in sources:
            sources[source] = meta.get("file_hash", "")
    return sources


def _delete_source_chunks(source_name: str) -> None:
    results = collection.get(where={"source": source_name}, include=[])
    if results["ids"]:
        collection.delete(ids=results["ids"])


def _index_file(filepath: Path, root_path: Path, file_hash: str) -> int:
    source_name = str(filepath.relative_to(root_path)).replace("\\", "/")
    documents, metadatas, ids = [], [], []

    pdf = fitz.open(str(filepath))
    try:
        for page_index, page in enumerate(pdf):
            text = page.get_text("text")
            page_blocks = _extract_text_blocks(text)
            if not page_blocks:
                continue
            for chunk_index, chunk in enumerate(_split_text(text), start=1):
                section_name = ""
                chunk_topics = _extract_topic_terms(chunk)
                for block in page_blocks:
                    if block["text"][:60] in chunk:
                        section_name = block["section"]
                        break
                chunk_kind = "numeric" if _extract_numeric_terms(chunk) else "text"
                documents.append(chunk)
                metadatas.append({
                    "source": source_name,
                    "folder": str(filepath.parent).replace("\\", "/"),
                    "page": page_index + 1,
                    "chunk": chunk_index,
                    "section": _sanitize_section_label(section_name[:SECTION_LABEL_MAX_LENGTH]),
                    "topics": ", ".join(chunk_topics),
                    "chunk_kind": chunk_kind,
                    "file_hash": file_hash,
                })
                ids.append(f"{source_name}-{page_index + 1}-{chunk_index}")
    finally:
        pdf.close()

    if documents:
        collection.add(documents=documents, metadatas=metadatas, ids=ids)
    return len(documents)


def sync_documents(folder_path: str = DOCUMENTS_PATH) -> Dict[str, int]:
    if not os.path.isdir(folder_path):
        raise FileNotFoundError(f"No existe la carpeta de documentos: {folder_path}")

    root_path = Path(folder_path)
    pdf_paths = sorted(root_path.rglob("*.pdf")) if RECURSIVE_PDF_SCAN else sorted(root_path.glob("*.pdf"))
    current_files = {
        str(p.relative_to(root_path)).replace("\\", "/"): p
        for p in pdf_paths
    }

    indexed = _get_indexed_sources()
    if indexed and all(v == "" for v in indexed.values()):
        logger.info("Índice sin file_hash detectado — reindexando con embedding multilingüe")
        reset_documents()
        indexed = {}

    added = updated = removed = 0

    for source_name in list(indexed):
        if source_name not in current_files:
            _delete_source_chunks(source_name)
            removed += 1
            logger.info("Eliminado del índice: %s", source_name)

    for source_name, filepath in current_files.items():
        current_hash = _file_hash(str(filepath))
        if source_name in indexed:
            if indexed[source_name] == current_hash:
                continue
            _delete_source_chunks(source_name)
            updated += 1
            logger.info("Actualizando índice: %s", source_name)
        else:
            added += 1
            logger.info("Indexando nuevo archivo: %s", source_name)
        _index_file(filepath, root_path, current_hash)

    logger.info("Sync completado — añadidos:%d actualizados:%d eliminados:%d", added, updated, removed)
    return {"added": added, "updated": updated, "removed": removed}


def load_documents(folder_path: str = DOCUMENTS_PATH, reset: bool = False) -> int:
    if reset:
        reset_documents()
    sync_documents(folder_path)
    return collection.count()


def search_documents(question: str, n_results: int = TOP_K_RESULTS) -> Tuple[str, List[str]]:
    if not question.strip():
        return "", []

    clean_question = _clean_question(question)
    if collection.count() == 0:
        try:
            sync_documents()
        except Exception:
            return "", []
        if collection.count() == 0:
            return "", []

    n_results = max(n_results, 5)
    question_tokens = set(_tokenize(clean_question))
    question_keywords = {token for token in question_tokens if token not in STOPWORDS and len(token) >= 5}
    core_terms = [_normalize_text(term) for term in _extract_core_terms(question_keywords)]
    query_profile = _build_query_profile(clean_question, question_keywords)
    normalized_question = query_profile["normalized_question"]

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
        metadata_norm = _metadata_text(metadata)
        section_title = _normalize_text(str(metadata.get("section", "")))
        overlap_score = len(question_tokens.intersection(doc_tokens))
        keyword_hits = sum(1 for kw in question_keywords if _normalize_text(kw) in doc_norm)
        core_hits = sum(1 for core in core_terms if core in doc_norm)
        numeric_hits = sum(1 for term in query_profile["numeric_terms"] if term in doc_norm)
        reference_hits = sum(1 for term in query_profile["reference_terms"] if term in doc_norm or term in metadata_norm)
        section_hits = sum(1 for term in query_profile["section_terms"] if term in metadata_norm)
        section_title_hits = sum(1 for term in query_profile["section_terms"] if term in section_title)

        score = overlap_score + (keyword_hits * 2) + (core_hits * 4)
        if normalized_question and normalized_question in doc_norm:
            score += 6
        if numeric_hits:
            score += numeric_hits * NUMERIC_PRIORITY_BOOST
        elif query_profile["expects_numeric"] and not _extract_numeric_terms(document):
            score -= LOW_SIGNAL_PENALTY
        if reference_hits:
            score += reference_hits * REFERENCE_PRIORITY_BOOST
        if section_hits:
            score += section_hits * SECTION_PRIORITY_BOOST
        if section_title_hits >= 2:
            score += section_title_hits * SECTION_TITLE_BOOST
        if query_profile["comparison"] and len(doc_tokens.intersection(question_tokens)) >= 2:
            score += COMPARISON_PRIORITY_BOOST
        if core_terms and core_hits == 0:
            score -= CORE_TERM_PENALTY
        if overlap_score == 0 and core_hits == 0 and metadata.get("chunk_kind") != "numeric":
            score -= 6

        ranked_items.append((score, doc_id, document, metadata))
        seen_ids.add(doc_id)

    def _fetch_lexical(term: str):
        try:
            return collection.get(
                where_document={"$contains": term},
                include=["documents", "metadatas"],
                limit=8,
            )
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=min(len(core_terms), 4)) as executor:
        lexical_results = list(executor.map(_fetch_lexical, core_terms))

    for lexical_hits in lexical_results:
        if not lexical_hits:
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
            if query_profile["reference_terms"] and any(ref in doc_norm for ref in query_profile["reference_terms"]):
                lexical_score += REFERENCE_PRIORITY_BOOST
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
    section_counts = {}
    for item in ranked_items:
        _, doc_id, _, metadata = item
        source_name = metadata.get("source", "unknown")
        section_name = metadata.get("section", "") or "__none__"
        if source_counts.get(source_name, 0) >= MAX_CHUNKS_PER_SOURCE:
            continue
        if section_counts.get((source_name, section_name), 0) >= 2:
            continue
        selected.append(item)
        selected_ids.add(doc_id)
        source_counts[source_name] = source_counts.get(source_name, 0) + 1
        section_counts[(source_name, section_name)] = section_counts.get((source_name, section_name), 0) + 1
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
        clean_section = _sanitize_section_label(str(metadata.get("section", "")))
        section_suffix = f", {clean_section}" if clean_section else ""
        source_label = f"{metadata['source']} (pag. {metadata['page']}{section_suffix})"
        context_parts.append(f"[{source_label}]\n{document}")
        if source_label not in seen_sources:
            sources.append(source_label)
            seen_sources.add(source_label)

    return "\n\n".join(context_parts), sources
