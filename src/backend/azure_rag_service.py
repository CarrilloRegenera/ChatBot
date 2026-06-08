import hashlib
import logging
import unicodedata
import re
import threading
from typing import Dict, List, Tuple

import fitz
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SearchableField,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)
from azure.search.documents.models import VectorizedQuery
from azure.storage.blob import BlobServiceClient
from blob_scope_config import configured_blob_scopes
from sentence_transformers.util import cos_sim

from config import (
    AZURE_SEARCH_ENDPOINT,
    AZURE_SEARCH_INDEX_NAME,
    AZURE_SEARCH_KEY,
    AZURE_SEARCH_VECTOR_FIELD,
    BLOB_CONTAINER_NAME,
    BLOB_PREFIX,
    BLOB_PREFIX_ALTA_TENSION,
    BLOB_PREFIX_BAJA_TENSION,
    BLOB_PREFIX_FOTOVOLTAICA_OM,
    BLOB_PREFIX_GRUPOS_ELECTROGENOS,
    BLOB_PREFIX_GUIAS_TECNICAS,
    BLOB_PREFIX_RITE,
    BLOB_STORAGE_CONNECTION_STRING,
    ENABLE_RERANK,
    MAX_CHUNKS_PER_SOURCE,
    TOP_K_RESULTS,
)
from rag_service import (
    OCR_MIN_TEXT_CHARS_PER_PAGE,
    RERANK_MODEL,
    _clean_question,
    _embedding_fn,
    _encode_passage,
    _encode_query,
    _EF_VERSION,
    _expected_domains,
    _decode_chunk_corruption,
    _extract_text_blocks,
    _chunk_profile_metadata,
    _document_profile_metadata,
    _normalize_text,
    _ocr_page_text,
    _sanitize_section_label,
    _source_domain_key,
    _split_text,
    _st_model,
    _looks_like_table_block,
)

_VECTOR_DIMS = 384  # multilingual-e5-small


logger = logging.getLogger(__name__)
_index_schema_lock = threading.Lock()
_index_schema_checked = False


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


def _configured_blob_scopes() -> List[Tuple[str, str]]:
    return configured_blob_scopes(
        BLOB_PREFIX,
        BLOB_PREFIX_ALTA_TENSION,
        BLOB_PREFIX_BAJA_TENSION,
        BLOB_PREFIX_FOTOVOLTAICA_OM,
        BLOB_PREFIX_GRUPOS_ELECTROGENOS,
        BLOB_PREFIX_GUIAS_TECNICAS,
        BLOB_PREFIX_RITE,
    )


def _category_for_blob(blob_name: str, configured_category: str) -> str:
    if configured_category:
        return configured_category
    return _source_domain_key(blob_name)


def _build_index_fields() -> List:
    """Schema completo del índice. Añadir campos nuevos aquí; nunca quitar los existentes."""
    vector_field = SearchField(
        name=AZURE_SEARCH_VECTOR_FIELD,
        type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
        searchable=True,
        vector_search_dimensions=_VECTOR_DIMS,
        vector_search_profile_name="hnsw-profile",
    )
    return [
        SimpleField(name="chunk_id", type=SearchFieldDataType.String, key=True, filterable=True),
        SimpleField(name="document_id", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="document_name", type=SearchFieldDataType.String),
        SearchableField(name="file_name", type=SearchFieldDataType.String),
        SimpleField(name="source_path", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="blob_path", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="content", type=SearchFieldDataType.String),
        vector_field,
        SimpleField(name="page_number", type=SearchFieldDataType.Int32, filterable=True),
        SimpleField(name="page", type=SearchFieldDataType.Int32, filterable=True),
        SimpleField(name="chunk_number", type=SearchFieldDataType.Int32),
        SimpleField(name="domain", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SimpleField(name="category", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="department", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SimpleField(name="document_type", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SimpleField(name="confidentiality", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="regulation", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SearchableField(name="section", type=SearchFieldDataType.String),
        SimpleField(name="section_type", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SearchableField(name="topics", type=SearchFieldDataType.String),
        SimpleField(name="chunk_kind", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="content_intent", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SearchableField(name="scope_hint", type=SearchFieldDataType.String),
        SearchableField(name="table_hint", type=SearchFieldDataType.String),
        SimpleField(name="table_signal_count", type=SearchFieldDataType.Int32),
        SimpleField(name="file_hash", type=SearchFieldDataType.String, filterable=True),
        # Campos enriquecidos (schema v4+)
        SearchableField(name="itc_refs", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="exact_refs", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="article_ref", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="section_level", type=SearchFieldDataType.Int32, filterable=True),
    ]


def ensure_azure_index() -> None:
    """Crea o actualiza el índice de Azure AI Search con el schema actual.

    Si el índice ya existe, añade únicamente los campos que falten (nunca modifica
    ni elimina campos existentes, lo que garantiza compatibilidad con datos en vuelo).
    Si no existe, lo crea completo con configuración HNSW para búsqueda vectorial.
    """
    _require_azure_config()
    index_client = SearchIndexClient(
        endpoint=AZURE_SEARCH_ENDPOINT,
        credential=AzureKeyCredential(AZURE_SEARCH_KEY),
    )
    all_fields = _build_index_fields()
    try:
        existing = index_client.get_index(AZURE_SEARCH_INDEX_NAME)
        existing_names = {f.name for f in existing.fields}
        new_fields = [f for f in all_fields if f.name not in existing_names]
        if new_fields:
            existing.fields.extend(new_fields)
            index_client.create_or_update_index(existing)
            logger.info(
                "Índice '%s': %d campos nuevos añadidos: %s",
                AZURE_SEARCH_INDEX_NAME,
                len(new_fields),
                [f.name for f in new_fields],
            )
        else:
            logger.info("Índice '%s': schema ya actualizado", AZURE_SEARCH_INDEX_NAME)
    except ResourceNotFoundError:
        vector_search = VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name="hnsw-config")],
            profiles=[VectorSearchProfile(name="hnsw-profile", algorithm_configuration_name="hnsw-config")],
        )
        index = SearchIndex(
            name=AZURE_SEARCH_INDEX_NAME,
            fields=all_fields,
            vector_search=vector_search,
        )
        index_client.create_index(index)
        logger.info("Índice '%s' creado desde cero", AZURE_SEARCH_INDEX_NAME)


def _ensure_index_schema_for_search() -> bool:
    """Asegura una vez por proceso que el indice admite campos enriquecidos.

    Si Azure no permite actualizar el schema en ese momento, la busqueda sigue
    funcionando con el select legacy para no romper el chat desplegado.
    """
    global _index_schema_checked
    if _index_schema_checked:
        return True
    with _index_schema_lock:
        if _index_schema_checked:
            return True
        try:
            ensure_azure_index()
        except Exception as exc:
            logger.warning("No se pudo validar schema enriquecido de Azure Search: %s", exc)
            return False
        _index_schema_checked = True
        return True


def _file_hash(content: bytes) -> str:
    return hashlib.md5(content).hexdigest()


def _indexed_hash(content_hash: str) -> str:
    return f"{content_hash}:{_EF_VERSION}"


def _document_id(source_path: str) -> str:
    return hashlib.md5(source_path.encode("utf-8")).hexdigest()


def _chunk_id(source_path: str, page_number: int, chunk_number: int) -> str:
    raw = f"{source_path}:{page_number}:{chunk_number}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _extract_page_text(page) -> str:
    """Extrae texto usando coordenadas de palabras (bbox).

    Reconstruye espacios entre palabras a partir de las posiciones reales en el PDF,
    evitando palabras pegadas en documentos con fuentes mal embebidas. Inserta líneas
    en blanco entre bloques de texto distintos para preservar la estructura de párrafo.
    """
    words = page.get_text("words", sort=True)
    if words:
        lines: List[str] = []
        current_block: int = words[0][5]
        current_line: int = words[0][6]
        current_words: List[str] = []
        for _x0, _y0, _x1, _y1, word, block_no, line_no, _word_no in words:
            word = unicodedata.normalize("NFC", word)
            if block_no != current_block or line_no != current_line:
                if current_words:
                    lines.append(" ".join(current_words))
                if block_no != current_block:
                    lines.append("")
                current_words = [word]
                current_block = block_no
                current_line = line_no
            else:
                current_words.append(word)
        if current_words:
            lines.append(" ".join(current_words))
        text = "\n".join(lines)
    else:
        text = ""

    if len(text.strip()) < OCR_MIN_TEXT_CHARS_PER_PAGE:
        ocr_text = _ocr_page_text(page)
        if ocr_text and len(ocr_text.strip()) > len(text.strip()):
            text = ocr_text
    return text


def _iter_pdf_chunks(blob_name: str, content: bytes, file_hash: str, category: str) -> List[Dict]:
    docs = []
    document_id = _document_id(blob_name)
    domain = category or _source_domain_key(blob_name)
    document_profile = _document_profile_metadata(blob_name, domain)
    file_name = blob_name.rsplit("/", 1)[-1]
    pdf = fitz.open(stream=content, filetype="pdf")
    try:
        for page_index, page in enumerate(pdf):
            page_number = page_index + 1
            text = _extract_page_text(page)
            text = _decode_chunk_corruption(text, blob_name)
            page_blocks = _extract_text_blocks(text)
            if not page_blocks:
                continue
            for chunk_index, chunk in enumerate(_split_text(text), start=1):
                section_name = ""
                for block in page_blocks:
                    if block["text"][:60] in chunk:
                        section_name = block["section"]
                        break
                if _looks_like_table_block(chunk):
                    chunk_kind = "table"
                elif re.search(r"\b\d+(?:[.,]\d+)?\b", chunk):
                    chunk_kind = "numeric"
                else:
                    chunk_kind = "text"
                chunk_profile = _chunk_profile_metadata(
                    blob_name, section_name, chunk, chunk_kind
                )
                docs.append({
                    "chunk_id": _chunk_id(blob_name, page_number, chunk_index),
                    "document_id": document_id,
                    "document_name": file_name,
                    "file_name": file_name,
                    "source_path": blob_name,
                    "blob_path": blob_name,
                    "content": chunk,
                    AZURE_SEARCH_VECTOR_FIELD: _encode_passage(chunk),
                    "page_number": page_number,
                    "page": page_number,
                    "chunk_number": chunk_index,
                    **document_profile,
                    **chunk_profile,
                    "file_hash": file_hash,
                    "article_ref": chunk_profile["itc_refs"],
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
    ensure_azure_index()
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
        current_indexed_hash = _indexed_hash(current_hash)

        if indexed.get(blob.name) == current_indexed_hash:
            continue
        if blob.name in indexed:
            updated += 1
            _delete_source_chunks(search_client, blob.name)
            logger.info("Actualizando Azure AI Search: %s", blob.name)
        else:
            added += 1
            logger.info("Indexando en Azure AI Search: %s category=%s", blob.name, category)

        docs = _iter_pdf_chunks(blob.name, content, current_indexed_hash, category)
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
    vector = _encode_query(question)
    vector_query = VectorizedQuery(
        vector=vector,
        k_nearest_neighbors=max(n_results * 4, 20),
        fields=AZURE_SEARCH_VECTOR_FIELD,
    )
    clean_question = _clean_question(question)
    domains = _expected_domains(clean_question)
    domain_filter = (
        "(" + " or ".join(f"domain eq '{_odata_escape(d)}'" for d in domains) + ")"
        if domains else None
    )
    legacy_select = [
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
    ]
    enriched_select = legacy_select + [
        "department",
        "document_type",
        "regulation",
        "section_type",
        "content_intent",
        "scope_hint",
        "itc_refs",
        "exact_refs",
    ]
    select_fields = enriched_select if _ensure_index_schema_for_search() else legacy_select
    try:
        results = _search_client().search(
            search_text=question,
            vector_queries=[vector_query],
            filter=domain_filter,
            top=max(n_results * 2, 12),
            select=select_fields,
        )
    except HttpResponseError:
        if select_fields == legacy_select:
            raise
        logger.warning("Azure Search no acepta campos enriquecidos; reintentando con select legacy")
        results = _search_client().search(
            search_text=question,
            vector_queries=[vector_query],
            filter=domain_filter,
            top=max(n_results * 2, 12),
            select=legacy_select,
        )

    candidates = list(results)
    if ENABLE_RERANK and _st_model is not None and candidates:
        query_emb = _st_model.encode(clean_question, convert_to_tensor=True)
        candidate_texts = [
            _decode_chunk_corruption(item.get("content", ""), item.get("source_path", ""))
            for item in candidates
        ]
        candidate_embs = _st_model.encode(candidate_texts, convert_to_tensor=True)
        scores = cos_sim(query_emb, candidate_embs)[0].tolist()
        candidates = [
            item for _, item in sorted(zip(scores, candidates), key=lambda x: x[0], reverse=True)
        ]

    selected = []
    source_counts: Dict[str, int] = {}
    for item in candidates:
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
        regulation = str(item.get("regulation", "") or "")
        regulation_suffix = f", {regulation}" if regulation else ""
        section_suffix = f", {section}" if section else ""
        source_label = f"{source} (pag. {page}{section_suffix})"
        if regulation_suffix and regulation not in source_label:
            source_label = f"{source} (pag. {page}{regulation_suffix}{section_suffix})"
        content = item.get("content", "")
        content = _decode_chunk_corruption(content, source)
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
        "selected_departments": sorted({
            _normalize_text(str(item.get("department") or ""))
            for item in selected
            if item.get("department")
        }),
        "selected_document_types": sorted({
            _normalize_text(str(item.get("document_type") or ""))
            for item in selected
            if item.get("document_type")
        }),
        "selected_regulations": sorted({
            _normalize_text(str(item.get("regulation") or ""))
            for item in selected
            if item.get("regulation")
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
