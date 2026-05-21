import hashlib
import html
import logging
import re
from typing import Dict, List, Tuple

import fitz
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from azure.storage.blob import BlobServiceClient

from config import (
    AZURE_SEARCH_ENDPOINT,
    AZURE_SEARCH_INDEX_NAME,
    AZURE_SEARCH_KEY,
    AZURE_SEARCH_VECTOR_FIELD,
    BLOB_CONTAINER_NAME,
    BLOB_PREFIX,
    BLOB_PREFIX_ALTA_TENSION,
    BLOB_PREFIX_BAJA_TENSION,
    BLOB_PREFIX_GUIAS_TECNICAS,
    BLOB_PREFIX_RITE,
    BLOB_STORAGE_CONNECTION_STRING,
    MAX_CHUNKS_PER_SOURCE,
    TOP_K_RESULTS,
)
from rag_service import (
    OCR_MIN_TEXT_CHARS_PER_PAGE,
    RERANK_MODEL,
    _embedding_fn,
    _extract_text_blocks,
    _extract_topic_terms,
    _normalize_text,
    _ocr_page_text,
    _sanitize_section_label,
    _source_domain_key,
    _split_text,
    _st_model,
    _table_signal_count,
    _looks_like_table_block,
)


logger = logging.getLogger(__name__)


def _require_azure_config() -> None:
    missing = []
    if not AZURE_SEARCH_ENDPOINT:
        missing.append("AZURE_SEARCH_ENDPOINT")
    if not AZURE_SEARCH_KEY:
        missing.append("AZURE_SEARCH_KEY")
    if not AZURE_SEARCH_INDEX_NAME:
        missing.append("AZURE_SEARCH_INDEX_NAME")
    if not BLOB_STORAGE_CONNECTION_STRING:
        missing.append("BLOB_STORAGE_CONNECTION_STRING")
    if not BLOB_CONTAINER_NAME:
        missing.append("BLOB_CONTAINER_NAME")
    if missing:
        raise RuntimeError("Configuracion Azure RAG incompleta: " + ", ".join(missing))
    if _embedding_fn is None or _st_model is None:
        raise RuntimeError(f"Embedding model no disponible: {RERANK_MODEL}")


def _search_client() -> SearchClient:
    return SearchClient(
        endpoint=AZURE_SEARCH_ENDPOINT,
        index_name=AZURE_SEARCH_INDEX_NAME,
        credential=AzureKeyCredential(AZURE_SEARCH_KEY),
    )


def _container_client():
    blob_service = BlobServiceClient.from_connection_string(BLOB_STORAGE_CONNECTION_STRING)
    return blob_service.get_container_client(BLOB_CONTAINER_NAME)


def _normalize_prefix(prefix: str) -> str:
    cleaned = (prefix or "").strip().strip("/")
    return f"{cleaned}/" if cleaned else ""


def _configured_blob_scopes() -> List[Tuple[str, str]]:
    configured = [
        ("alta_tension", _normalize_prefix(BLOB_PREFIX_ALTA_TENSION)),
        ("baja_tension", _normalize_prefix(BLOB_PREFIX_BAJA_TENSION)),
        ("guias_tecnicas", _normalize_prefix(BLOB_PREFIX_GUIAS_TECNICAS)),
        ("rite", _normalize_prefix(BLOB_PREFIX_RITE)),
    ]
    active = [(category, prefix) for category, prefix in configured if prefix]
    if active:
        return active
    fallback_prefix = _normalize_prefix(BLOB_PREFIX)
    return [("", fallback_prefix)]


def _category_for_blob(blob_name: str, configured_category: str) -> str:
    if configured_category:
        return configured_category
    return _source_domain_key(blob_name)


def _file_hash(content: bytes) -> str:
    return hashlib.md5(content).hexdigest()


def _document_id(source_path: str) -> str:
    return hashlib.md5(source_path.encode("utf-8")).hexdigest()


def _chunk_id(source_path: str, page_number: int, chunk_number: int) -> str:
    raw = f"{source_path}:{page_number}:{chunk_number}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _extract_page_text(page) -> str:
    raw_html = page.get_text("html")
    paragraph_pattern = re.compile(
        r'<p style="top:([\d.]+)pt[^"]*line-height:([\d.]+)pt[^"]*"[^>]*>(.*?)</p>',
        re.DOTALL,
    )
    lines = []
    prev_top = None
    prev_lh = 10.0
    for top_s, lh_s, content in paragraph_pattern.findall(raw_html):
        top, lh = float(top_s), float(lh_s)
        text = html.unescape(re.sub(r"<[^>]+>", "", content)).strip()
        if text:
            if prev_top is not None and (top - prev_top) > prev_lh + 3.0:
                lines.append("")
            lines.append(text)
            prev_top = top
            prev_lh = lh
    text = "\n".join(lines)
    if len(text.strip()) < OCR_MIN_TEXT_CHARS_PER_PAGE:
        ocr_text = _ocr_page_text(page)
        if ocr_text and len(ocr_text.strip()) > len(text.strip()):
            text = ocr_text
    return text


def _iter_pdf_chunks(blob_name: str, content: bytes, file_hash: str, category: str) -> List[Dict]:
    docs = []
    document_id = _document_id(blob_name)
    domain = category or _source_domain_key(blob_name)
    file_name = blob_name.rsplit("/", 1)[-1]
    pdf = fitz.open(stream=content, filetype="pdf")
    try:
        for page_index, page in enumerate(pdf):
            page_number = page_index + 1
            text = _extract_page_text(page)
            page_blocks = _extract_text_blocks(text)
            if not page_blocks:
                continue
            for chunk_index, chunk in enumerate(_split_text(text), start=1):
                section_name = ""
                for block in page_blocks:
                    if block["text"][:60] in chunk:
                        section_name = block["section"]
                        break
                chunk_topics = _extract_topic_terms(chunk)
                if _looks_like_table_block(chunk):
                    chunk_kind = "table"
                elif re.search(r"\b\d+(?:[.,]\d+)?\b", chunk):
                    chunk_kind = "numeric"
                else:
                    chunk_kind = "text"
                docs.append({
                    "chunk_id": _chunk_id(blob_name, page_number, chunk_index),
                    "document_id": document_id,
                    "document_name": file_name,
                    "file_name": file_name,
                    "source_path": blob_name,
                    "blob_path": blob_name,
                    "content": chunk,
                    AZURE_SEARCH_VECTOR_FIELD: _st_model.encode(chunk, convert_to_numpy=True).tolist(),
                    "page_number": page_number,
                    "page": page_number,
                    "chunk_number": chunk_index,
                    "domain": domain,
                    "category": domain,
                    "section": _sanitize_section_label(section_name),
                    "topics": ", ".join(chunk_topics),
                    "chunk_kind": chunk_kind,
                    "table_signal_count": _table_signal_count(chunk),
                    "file_hash": file_hash,
                })
    finally:
        pdf.close()
    return docs


def _search_all_indexed_sources(client: SearchClient) -> Dict[str, str]:
    indexed: Dict[str, str] = {}
    results = client.search(
        search_text="*",
        select=["source_path", "file_hash"],
        top=1000,
        include_total_count=False,
    )
    for item in results:
        source = item.get("source_path")
        if source and source not in indexed:
            indexed[source] = item.get("file_hash", "")
    return indexed


def _odata_escape(value: str) -> str:
    return value.replace("'", "''")


def _delete_source_chunks(client: SearchClient, source_path: str) -> int:
    deleted = 0
    while True:
        results = client.search(
            search_text="*",
            filter=f"source_path eq '{_odata_escape(source_path)}'",
            select=["chunk_id"],
            top=1000,
        )
        docs = [{"chunk_id": item["chunk_id"]} for item in results if item.get("chunk_id")]
        if not docs:
            return deleted
        client.delete_documents(documents=docs)
        deleted += len(docs)


def sync_documents_from_blob() -> Dict[str, int]:
    _require_azure_config()
    search_client = _search_client()
    container = _container_client()

    scoped_blobs = []
    seen_blob_names = set()
    for category, prefix in _configured_blob_scopes():
        list_kwargs = {"name_starts_with": prefix} if prefix else {}
        blobs = [
            blob
            for blob in container.list_blobs(**list_kwargs)
            if blob.name.lower().endswith(".pdf")
        ]
        logger.info(
            "Blob scope category=%s prefix=%s pdfs=%d",
            category or "(auto)",
            prefix or "(contenedor completo)",
            len(blobs),
        )
        for blob in blobs:
            if blob.name in seen_blob_names:
                continue
            seen_blob_names.add(blob.name)
            scoped_blobs.append((blob, _category_for_blob(blob.name, category)))

    current_names = {blob.name for blob, _ in scoped_blobs}
    indexed = _search_all_indexed_sources(search_client)

    added = updated = removed = chunks_indexed = 0

    for source_name in list(indexed):
        if source_name not in current_names:
            removed += 1
            _delete_source_chunks(search_client, source_name)
            logger.info("Eliminado de Azure AI Search: %s", source_name)

    for blob, category in scoped_blobs:
        downloader = container.download_blob(blob.name)
        content = downloader.readall()
        current_hash = _file_hash(content)

        if indexed.get(blob.name) == current_hash:
            continue
        if blob.name in indexed:
            updated += 1
            _delete_source_chunks(search_client, blob.name)
            logger.info("Actualizando Azure AI Search: %s", blob.name)
        else:
            added += 1
            logger.info("Indexando en Azure AI Search: %s category=%s", blob.name, category)

        docs = _iter_pdf_chunks(blob.name, content, current_hash, category)
        for start in range(0, len(docs), 500):
            batch = docs[start:start + 500]
            if batch:
                results = search_client.upload_documents(documents=batch)
                failed = [result for result in results if not result.succeeded]
                if failed:
                    raise RuntimeError(
                        f"Fallaron {len(failed)} chunks al subir a Azure AI Search para {blob.name}"
                    )
        chunks_indexed += len(docs)

    logger.info(
        "Azure sync completado - anadidos:%d actualizados:%d eliminados:%d chunks:%d",
        added,
        updated,
        removed,
        chunks_indexed,
    )
    return {"added": added, "updated": updated, "removed": removed, "chunks_indexed": chunks_indexed}


def search_documents_detailed_azure(question: str, n_results: int = TOP_K_RESULTS) -> Tuple[str, List[str], Dict[str, object]]:
    _require_azure_config()
    if not question.strip():
        return "", [], {"selected_count": 0, "source_diversity": 0, "backend": "azure_search"}

    n_results = max(n_results, 6)
    vector = _st_model.encode(question, convert_to_numpy=True).tolist()
    vector_query = VectorizedQuery(
        vector=vector,
        k_nearest_neighbors=max(n_results * 4, 20),
        fields=AZURE_SEARCH_VECTOR_FIELD,
    )
    results = _search_client().search(
        search_text=question,
        vector_queries=[vector_query],
        top=max(n_results * 2, 12),
        select=[
            "chunk_id",
            "source_path",
            "blob_path",
            "file_name",
            "content",
            "page_number",
            "page",
            "section",
            "domain",
            "category",
            "chunk_kind",
            "table_signal_count",
        ],
    )

    selected = []
    source_counts: Dict[str, int] = {}
    for item in results:
        source = item.get("source_path", "unknown")
        if source_counts.get(source, 0) >= MAX_CHUNKS_PER_SOURCE:
            continue
        selected.append(item)
        source_counts[source] = source_counts.get(source, 0) + 1
        if len(selected) >= n_results:
            break

    context_parts = []
    sources = []
    seen_sources = set()
    table_selected_chunks = 0
    table_selected_signal_count = 0
    for item in selected:
        source = item.get("blob_path") or item.get("source_path", "unknown")
        page = item.get("page") or item.get("page_number", "?")
        section = _sanitize_section_label(str(item.get("section", "") or ""))
        section_suffix = f", {section}" if section else ""
        source_label = f"{source} (pag. {page}{section_suffix})"
        content = item.get("content", "")
        context_parts.append(f"[{source_label}]\n{content}")
        if source_label not in seen_sources:
            sources.append(source_label)
            seen_sources.add(source_label)
        if str(item.get("chunk_kind", "")) == "table":
            table_selected_chunks += 1
        table_selected_signal_count += int(item.get("table_signal_count", 0) or 0)

    retrieval_stats = {
        "backend": "azure_search",
        "selected_count": len(selected),
        "source_diversity": len(source_counts),
        "top_sources": sorted(source_counts)[:5],
        "selected_domains": sorted({
            _normalize_text(str(item.get("domain") or item.get("category") or ""))
            for item in selected
            if item.get("domain") or item.get("category")
        }),
        "expected_domains": [],
        "domain_match_ratio": 1.0,
        "broad_query": False,
        "table_selected_chunks": table_selected_chunks,
        "table_selected_signal_count": table_selected_signal_count,
        "table_candidate_signal_count": table_selected_signal_count,
        "table_coverage_ratio": 1.0,
    }
    return "\n\n".join(context_parts), sources, retrieval_stats
