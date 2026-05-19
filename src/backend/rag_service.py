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

from chroma_client import get_chroma_client
from config import (
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
CHUNK_OVERLAP = 375
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
DEFINITION_QUERY_PATTERN = re.compile(
    r"(?:\bcomo\s+se\s+denomina\b|\bque\s+es\b|\bque\s+se\s+entiende\s+por\b|\bdefinicion\b|"
    r"\bque\s+funcion\s+cumple[n]?\b|\bpara\s+que\s+sirve[n]?\b|\bcual\s+es\s+su\s+funcion\b|"
    r"\bque\s+papel\s+cumple[n]?\b)",
    re.IGNORECASE,
)
DEFINITION_CUE_PATTERN = re.compile(
    r"\b(?:se denomina|se define como|es la|es el|recibe el nombre de|definicion|"
    r"tiene por objeto|objeto|campo de aplicacion|condiciones tecnicas|garantias de seguridad|"
    r"ejecucion|verificaciones|inspecciones|instrucciones tecnicas complementarias)\b",
    re.IGNORECASE,
)
LIST_QUERY_PATTERN = re.compile(
    r"(?:\bcuales\s+son\b|\benumera\b|\blista\b|\btipos\s+de\b|\bclases\s+de\b|\bpueden\s+ser\b)",
    re.IGNORECASE,
)
SUMMARY_QUERY_PATTERN = re.compile(
    r"(?:\bresume\b|\bresumen\b|\bresumir\b|\bsintetiza\b|\bsintesis\b)",
    re.IGNORECASE,
)
TABLE_QUERY_PATTERN = re.compile(
    r"(?:\btabla\b|\bcircuitos?\s+minimos?\b|\bcircuitos?\s+mínimos?\b|\brelacion\s+de\b|\brelación\s+de\b|\blista\s+completa\b)",
    re.IGNORECASE,
)
LIST_CUE_PATTERN = re.compile(r"(?:^|\n)(?:[-*]\s+|\d+\.\s+)", re.IGNORECASE)
CIRCUIT_LIST_CUE_PATTERN = re.compile(r"\bC(?:1[0-3]?|[1-9])\b", re.IGNORECASE)
COMPARISON_QUERY_PATTERN = re.compile(
    r"(?:\bdiferencia\b|\bdiferencias\b|\bcompara\b|\bcomparar\b|\bfrente\s+a\b|\bversus\b)",
    re.IGNORECASE,
)
COMPARISON_CUE_PATTERN = re.compile(
    r"\b(?:mientras que|por el contrario|en cambio|a diferencia de|frente a)\b",
    re.IGNORECASE,
)
PROCEDURE_QUERY_PATTERN = re.compile(
    r"(?:\bcomo\s+se\s+calcula\b|\bcomo\s+debe\b|\bcomo\s+puede\b|\bprocedimiento\b|\bpasos\b)",
    re.IGNORECASE,
)
GENERALIZATION_QUERY_PATTERN = re.compile(
    r"(?:\ben\s+general\b|\bprincipales\b|\bcriterios?\b|\brequisitos?\b|\bexcepciones?\b|"
    r"\balcance\b|\baplicacion\b|\baplicación\b|\bfuncion\b|\bfunción\b|\bfinalidad\b|"
    r"\bobjetivo\b|\bcambios?\b|\bmodifica\b|\bintroduce\b|\bafecta\b|\bimplica\b)",
    re.IGNORECASE,
)
PROCEDURE_CUE_PATTERN = re.compile(
    r"\b(?:paso|primero|segundo|a continuacion|debe|deben|se debe|se deben)\b",
    re.IGNORECASE,
)
TEMPORAL_QUERY_PATTERN = re.compile(
    r"(?:\bcuando\b|\bplazo\b|\bperiodicidad\b|\brevisar\b|\brevisione?s\b|\bfrecuencia\b)",
    re.IGNORECASE,
)
TEMPORAL_CUE_PATTERN = re.compile(
    r"\b(?:periodicidad|plazo|cada\s+\d|bienal|anual|trimestral|quinquenal|semestral|revisiones?)\b",
    re.IGNORECASE,
)
TEMPORAL_INJECT_TERMS = ("periodicidad", "plazo", "revision")
LABELED_QUERY_PATTERNS = {
    "clase": re.compile(r"\bclase\s+(i{1,3}|iv|v|vi|vii|viii|ix|x|\d+|[a-z])\b", re.IGNORECASE),
    "tipo": re.compile(r"\btipo\s+([a-z0-9]+)\b", re.IGNORECASE),
    "categoria": re.compile(r"\bcategor(?:ia|ía)\s+([a-z0-9]+)\b", re.IGNORECASE),
    "grado_ip": re.compile(r"\bip\s?(\d{2}[a-z]?)\b", re.IGNORECASE),
    "grado_ik": re.compile(r"\bik\s?(\d{2})\b", re.IGNORECASE),
    "esquema": re.compile(r"\b(tt|tn(?:-?[sc])?|it)\b", re.IGNORECASE),
}
DEFINITION_PRIORITY_BOOST = 10
LIST_PRIORITY_BOOST = 6
SUMMARY_PRIORITY_BOOST = 5
TABLE_PRIORITY_BOOST = 8
COMPARISON_PRIORITY_BOOST_INTENT = 6
PROCEDURE_PRIORITY_BOOST = 5
TEMPORAL_PRIORITY_BOOST = 7
LABELED_MATCH_PRIORITY_BOOST = 12
LABELED_CONTEXT_PENALTY = 10
DOMAIN_SOURCE_HINTS = {
    "alta_tension": ("a16436-16554", "alta tension", "alta_tension", "itc-lat", "lat"),
    "rite": ("a35931-35984", "rite", "instalaciones termicas", "termicas"),
    "baja_tension": ("boe-326_reglamento_electrotecnico_para_baja_tension_e_itc", "rebt", "baja tension", "baja_tension", "itc-bt"),
    "guias_tecnicas": ("guia", "guias", "guia_bt", "bt-40", "iluminacion", "une-12464", "12464"),
    "fotovoltaica_om": ("fotovoltaica", "fotovoltaico", "fv", "operacion", "mantenimiento", "o&m", "om-fv"),
    "grupos_electrogenos": ("grupo electrogeno", "grupos electrogenos", "iso-8528", "8528", "generating sets"),
}
DOMAIN_FOLDER_PREFIXES = {
    "alta_tension": ("alta_tension", "lat", "lineas_alta_tension"),
    "rite": ("rite", "instalaciones_termicas", "termicas"),
    "baja_tension": ("baja_tension", "rebt"),
    "guias_tecnicas": ("guias_tecnicas", "guias", "iluminacion"),
    "fotovoltaica_om": ("fotovoltaica_om", "fotovoltaica", "fv", "operacion_mantenimiento"),
    "grupos_electrogenos": ("grupos_electrogenos", "grupos", "electrogenos"),
    "pendiente_ocr": ("pendiente_ocr", "ocr"),
}


logger = logging.getLogger(__name__)

_st_model = None
try:
    _st_model = SentenceTransformer(RERANK_MODEL, local_files_only=True)
except Exception as exc:
    logger.warning("Modelo '%s' no disponible en cache local: %s", RERANK_MODEL, str(exc))
    try:
        _st_model = SentenceTransformer(RERANK_MODEL)
    except Exception as net_exc:
        logger.warning("No se pudo cargar modelo multilingüe '%s': %s", RERANK_MODEL, str(net_exc))

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


_EF_VERSION = f"multilingual-minilm-v2-domain-c{CHUNK_SIZE}-o{CHUNK_OVERLAP}"


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


chroma_client = get_chroma_client()
_embedding_fn = _MultilingualEF() if _st_model else None
collection = _get_or_reset_collection(chroma_client, COLLECTION_NAME, _embedding_fn)


def _find_chunk_boundary(text: str, chunk_size: int = CHUNK_SIZE, grace: int = CHUNK_SENTENCE_GRACE) -> int:
    if len(text) <= chunk_size:
        return len(text)

    forward_limit = min(len(text), chunk_size + grace)
    for idx in range(chunk_size, forward_limit):
        if text[idx] in ".;!?" and (idx + 1 == len(text) or text[idx + 1].isspace()):
            return idx + 1

    backward_limit = max(MIN_CHUNK_LENGTH, chunk_size - grace)
    for idx in range(chunk_size - 1, backward_limit - 1, -1):
        if text[idx] in ".;!?" and (idx + 1 == len(text) or text[idx + 1].isspace()):
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


def _extract_labeled_terms(text: str) -> Dict[str, set[str]]:
    extracted: Dict[str, set[str]] = {}
    for family, pattern in LABELED_QUERY_PATTERNS.items():
        matches = {
            re.sub(r"\s+", "", match.group(1).lower())
            for match in pattern.finditer(text or "")
            if match.group(1)
        }
        if matches:
            extracted[family] = matches
    return extracted


def _extract_disambiguation_terms(text: str, *, exclude_labeled: bool = True, limit: int = 6) -> List[str]:
    stopwords = STOPWORDS.union({
        "clase", "tipo", "categoria", "categoría", "grado", "esquema",
        "cual", "cuales", "que", "qué", "como", "cómo", "caracteriza",
        "caracteristicas", "características", "define", "definicion", "definición",
        "materiales",
    })
    labeled_values = set()
    if exclude_labeled:
        for values in _extract_labeled_terms(text).values():
            labeled_values.update(values)

    terms = []
    seen = set()
    for token in _tokenize(text):
        normalized = _normalize_text(token)
        normalized = re.sub(r"\s+", "", normalized)
        if (
            not normalized
            or normalized in stopwords
            or normalized in labeled_values
            or len(normalized) < 5
            or normalized in seen
        ):
            continue
        seen.add(normalized)
        terms.append(normalized)
        if len(terms) >= limit:
            break
    return terms


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
        "labeled_terms": _extract_labeled_terms(clean_question),
        "disambiguation_terms": _extract_disambiguation_terms(clean_question),
        "comparison": any(term in normalized for term in ("compara", "diferencia", "frente", "versus")),
        "expects_numeric": bool(re.search(r"\b(cuanto|cuantos|cuantas|valor|limite|potencia|resistencia|ohm|kw|mm2|m2|volt|amper|porcentaje)\b", normalized)),
        "definition_query": bool(DEFINITION_QUERY_PATTERN.search(normalized)),
        "list_query": bool(LIST_QUERY_PATTERN.search(normalized)),
        "summary_query": bool(SUMMARY_QUERY_PATTERN.search(normalized)),
        "table_query": bool(TABLE_QUERY_PATTERN.search(normalized)),
        "comparison_query": bool(COMPARISON_QUERY_PATTERN.search(normalized)),
        "procedure_query": bool(PROCEDURE_QUERY_PATTERN.search(normalized)),
        "generalization_query": bool(GENERALIZATION_QUERY_PATTERN.search(normalized)),
        "temporal_query": bool(TEMPORAL_QUERY_PATTERN.search(normalized)),
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
            for key in ("source", "folder", "domain", "section", "topics")
        )
    )


def _domain_from_source(source_name: str) -> str:
    normalized_source = _normalize_text((source_name or "").replace("/", " "))
    for domain_key, hints in DOMAIN_SOURCE_HINTS.items():
        if any(hint in normalized_source for hint in hints):
            return domain_key
    return "general"


def _domain_from_folder(source_name: str) -> str:
    parts = [
        _normalize_text(part)
        for part in (source_name or "").replace("\\", "/").split("/")[:-1]
        if part.strip()
    ]
    for part in parts:
        compact = re.sub(r"^\d+[_\-\s]*", "", part).replace("-", "_").replace(" ", "_")
        for domain_key, prefixes in DOMAIN_FOLDER_PREFIXES.items():
            if any(prefix in compact for prefix in prefixes):
                return domain_key
    return ""


def _source_domain_key(source_name: str, metadata: Dict[str, object] | None = None) -> str:
    if metadata:
        explicit_domain = str(metadata.get("domain", "") or "").strip()
        if explicit_domain:
            return explicit_domain
    return _domain_from_folder(source_name) or _domain_from_source(source_name)


def _expected_domains(question: str) -> List[str]:
    normalized = _normalize_text(question or "")
    domains = []
    if any(term in normalized for term in ("alta tension", "itc-lat", "lineas electricas de alta", "lat", "linea de at", "lineas de at", "instalacion at", "instalaciones at")):
        domains.append("alta_tension")
    if any(term in normalized for term in ("rite", "instalaciones termicas", "termicas", "climatizacion", "calefaccion")):
        domains.append("rite")
    if any(term in normalized for term in ("rebt", "baja tension", "itc-bt")):
        domains.append("baja_tension")
    if any(term in normalized for term in ("bt-40", "guia bt 40", "guia-bt-40", "instalaciones generadoras", "generadoras de baja tension", "iluminacion", "alumbrado", "une 12464", "12464")):
        domains.append("guias_tecnicas")
    if any(term in normalized for term in ("fotovoltaica", "fotovoltaico", "paneles solares", "planta solar", "operacion y mantenimiento", "mantenimiento fv", "o&m")):
        domains.append("fotovoltaica_om")
    if any(term in normalized for term in ("grupo electrogeno", "grupos electrogenos", "generador diesel", "iso 8528", "8528")):
        domains.append("grupos_electrogenos")
    return domains


def reset_documents() -> None:
    chroma_client.delete_collection(COLLECTION_NAME)
    global collection
    collection = chroma_client.create_collection(
        name=COLLECTION_NAME,
        embedding_function=_embedding_fn,
        metadata={"ef_version": _EF_VERSION},
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
    domain = _source_domain_key(source_name)
    documents, metadatas, ids = [], [], []

    pdf = fitz.open(str(filepath))
    try:
        for page_index, page in enumerate(pdf):
            import html as _html
            raw_html = page.get_text("html")
            p_pat = re.compile(r'<p style="top:([\d.]+)pt[^"]*line-height:([\d.]+)pt[^"]*"[^>]*>(.*?)</p>', re.DOTALL)
            lines = []
            prev_top = None
            prev_lh = 10.0
            for top_s, lh_s, content in p_pat.findall(raw_html):
                top, lh = float(top_s), float(lh_s)
                txt = _html.unescape(re.sub(r"<[^>]+>", "", content)).strip()
                if txt:
                    if prev_top is not None and (top - prev_top) > prev_lh + 3.0:
                        lines.append("")
                    lines.append(txt)
                    prev_top = top
                    prev_lh = lh
            text = "\n".join(lines)
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
                    "domain": domain,
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
    if _embedding_fn is None:
        raise RuntimeError(
            "Embedding model no disponible. Descarga/cacha localmente "
            f"'{RERANK_MODEL}' antes de indexar."
        )
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


def search_documents_detailed(question: str, n_results: int = TOP_K_RESULTS) -> Tuple[str, List[str], Dict[str, object]]:
    if not question.strip():
        return "", [], {"selected_count": 0, "source_diversity": 0, "expected_domains": [], "domain_match_ratio": 0.0}
    if _embedding_fn is None:
        logger.error("Busqueda RAG deshabilitada: embedding model '%s' no disponible", RERANK_MODEL)
        return "", [], {"selected_count": 0, "source_diversity": 0, "expected_domains": [], "domain_match_ratio": 0.0}

    clean_question = _clean_question(question)
    if collection.count() == 0:
        try:
            sync_documents()
        except Exception:
            return "", [], {"selected_count": 0, "source_diversity": 0, "expected_domains": [], "domain_match_ratio": 0.0}
        if collection.count() == 0:
            return "", [], {"selected_count": 0, "source_diversity": 0, "expected_domains": [], "domain_match_ratio": 0.0}

    n_results = max(n_results, 6)
    question_tokens = set(_tokenize(clean_question))
    question_keywords = {token for token in question_tokens if token not in STOPWORDS and len(token) >= 5}
    core_terms = [_normalize_text(term) for term in _extract_core_terms(question_keywords)]
    query_profile = _build_query_profile(clean_question, question_keywords)
    normalized_question = query_profile["normalized_question"]
    if query_profile["temporal_query"]:
        for t in TEMPORAL_INJECT_TERMS:
            if t not in core_terms:
                core_terms.append(t)
    if query_profile["definition_query"] and any(term in normalized_question for term in ("funcion", "sirve", "papel")):
        for t in ("objeto", "aplicacion", "condiciones", "ejecucion", "verificaciones", "inspecciones"):
            if t not in core_terms:
                core_terms.append(t)
    if query_profile["generalization_query"]:
        for t in ("objeto", "ambito", "aplicacion", "condiciones", "requisitos", "excepciones", "criterios"):
            if t not in core_terms:
                core_terms.append(t)
    expected_domains = _expected_domains(clean_question)
    broad_query = any((
        query_profile["definition_query"],
        query_profile["list_query"],
        query_profile["summary_query"],
        query_profile["table_query"],
        query_profile["comparison_query"],
        query_profile["generalization_query"],
    ))
    if query_profile["summary_query"] or query_profile["list_query"] or query_profile["table_query"]:
        n_results = max(n_results, 8 if not query_profile["table_query"] else 10)
    elif query_profile["definition_query"] or query_profile["comparison_query"] or query_profile["generalization_query"]:
        n_results = max(n_results, 7)
    if query_profile["temporal_query"]:
        n_results = max(n_results, 8)

    candidate_count = _candidate_window(n_results, question_keywords, clean_question)
    if broad_query:
        candidate_count = min(candidate_count + 12, 80)
    results = collection.query(query_texts=[clean_question], n_results=candidate_count)
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    ids = results.get("ids", [[]])[0]

    if expected_domains:
        existing_ids = set(ids)
        for domain in expected_domains:
            try:
                forced_n = min(n_results + 6, 14)
                domain_results = collection.query(
                    query_texts=[clean_question],
                    n_results=forced_n,
                    where={"domain": domain},
                )
                for doc, meta, fid in zip(
                    domain_results.get("documents", [[]])[0],
                    domain_results.get("metadatas", [[]])[0],
                    domain_results.get("ids", [[]])[0],
                ):
                    if fid not in existing_ids:
                        documents.append(doc)
                        metadatas.append(meta)
                        ids.append(fid)
                        existing_ids.add(fid)
            except Exception as exc:
                logger.warning("Domain-forced retrieval failed for %s: %s", domain, exc)

    ranked_items = []
    seen_ids = set()

    for doc_id, document, metadata in zip(ids, documents, metadatas):
        if not document or not metadata:
            continue

        doc_norm = _normalize_text(document)
        doc_tokens = set(_tokenize(document))
        metadata_norm = _metadata_text(metadata)
        section_title = _normalize_text(str(metadata.get("section", "")))
        source_title = _normalize_text(str(metadata.get("source", "")).replace("/", " "))
        source_domain = _source_domain_key(str(metadata.get("source", "")), metadata)
        document_labeled_terms = _extract_labeled_terms(f"{metadata.get('section', '')} {document}")
        overlap_score = len(question_tokens.intersection(doc_tokens))
        keyword_hits = sum(1 for kw in question_keywords if _normalize_text(kw) in doc_norm)
        core_hits = sum(1 for core in core_terms if core in doc_norm)
        numeric_hits = sum(1 for term in query_profile["numeric_terms"] if term in doc_norm)
        reference_hits = sum(1 for term in query_profile["reference_terms"] if term in doc_norm or term in metadata_norm)
        section_hits = sum(1 for term in query_profile["section_terms"] if term in metadata_norm)
        section_title_hits = sum(1 for term in query_profile["section_terms"] if term in section_title)
        source_title_hits = sum(1 for term in query_profile["section_terms"] if term in source_title)
        definition_hits = len(DEFINITION_CUE_PATTERN.findall(document)) if query_profile["definition_query"] else 0
        list_hits = 1 if query_profile["list_query"] and LIST_CUE_PATTERN.search(document) else 0
        summary_hits = 1 if query_profile["summary_query"] and (LIST_CUE_PATTERN.search(document) or metadata.get("section")) else 0
        table_hits = 0
        if query_profile["table_query"]:
            table_hits = sum((
                1 if "tabla" in doc_norm or "tabla" in metadata_norm or "tabla" in section_title else 0,
                1 if LIST_CUE_PATTERN.search(document) else 0,
                1 if CIRCUIT_LIST_CUE_PATTERN.search(document) else 0,
            ))
        comparison_hits = len(COMPARISON_CUE_PATTERN.findall(document)) if query_profile["comparison_query"] else 0
        procedure_hits = len(PROCEDURE_CUE_PATTERN.findall(document)) if query_profile["procedure_query"] else 0
        temporal_hits = len(TEMPORAL_CUE_PATTERN.findall(document)) if query_profile["temporal_query"] else 0
        labeled_match_hits = 0
        for family, asked_values in query_profile["labeled_terms"].items():
            labeled_match_hits += sum(1 for value in asked_values if value in document_labeled_terms.get(family, set()))
        disambiguation_hits = sum(
            1 for term in query_profile["disambiguation_terms"]
            if term in doc_norm or term in metadata_norm or term in section_title
        )

        score = overlap_score + (keyword_hits * 2) + (core_hits * 4)
        if normalized_question and normalized_question in doc_norm:
            score += 6
        if definition_hits:
            score += definition_hits * DEFINITION_PRIORITY_BOOST
        if list_hits:
            score += list_hits * LIST_PRIORITY_BOOST
        if summary_hits:
            score += summary_hits * SUMMARY_PRIORITY_BOOST
        if table_hits:
            score += table_hits * TABLE_PRIORITY_BOOST
        if comparison_hits:
            score += comparison_hits * COMPARISON_PRIORITY_BOOST_INTENT
        if procedure_hits:
            score += procedure_hits * PROCEDURE_PRIORITY_BOOST
        if temporal_hits:
            score += temporal_hits * TEMPORAL_PRIORITY_BOOST
        if labeled_match_hits:
            score += labeled_match_hits * LABELED_MATCH_PRIORITY_BOOST
            if query_profile["disambiguation_terms"] and disambiguation_hits == 0:
                score -= LABELED_CONTEXT_PENALTY
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
        if source_title_hits:
            score += source_title_hits * 5
        if expected_domains:
            if source_domain in expected_domains:
                score += 12
            else:
                score -= 10
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
            metadata_norm = _metadata_text(metadata)
            section_title = _normalize_text(str(metadata.get("section", "")))
            lexical_score = 8
            if core_terms and not any(core in doc_norm for core in core_terms):
                lexical_score -= CORE_TERM_PENALTY
            if query_profile["reference_terms"] and any(ref in doc_norm for ref in query_profile["reference_terms"]):
                lexical_score += REFERENCE_PRIORITY_BOOST
            if query_profile["definition_query"] and DEFINITION_CUE_PATTERN.search(document):
                lexical_score += DEFINITION_PRIORITY_BOOST
            if query_profile["list_query"] and LIST_CUE_PATTERN.search(document):
                lexical_score += LIST_PRIORITY_BOOST
            if query_profile["summary_query"] and (LIST_CUE_PATTERN.search(document) or metadata.get("section")):
                lexical_score += SUMMARY_PRIORITY_BOOST
            if query_profile["table_query"]:
                if "tabla" in doc_norm or "tabla" in metadata_norm or "tabla" in section_title:
                    lexical_score += TABLE_PRIORITY_BOOST
                if LIST_CUE_PATTERN.search(document):
                    lexical_score += TABLE_PRIORITY_BOOST
                if CIRCUIT_LIST_CUE_PATTERN.search(document):
                    lexical_score += TABLE_PRIORITY_BOOST
            if query_profile["comparison_query"] and COMPARISON_CUE_PATTERN.search(document):
                lexical_score += COMPARISON_PRIORITY_BOOST_INTENT
            if query_profile["procedure_query"] and PROCEDURE_CUE_PATTERN.search(document):
                lexical_score += PROCEDURE_PRIORITY_BOOST
            if query_profile["temporal_query"] and TEMPORAL_CUE_PATTERN.search(document):
                lexical_score += TEMPORAL_PRIORITY_BOOST
            document_labeled_terms = _extract_labeled_terms(f"{metadata.get('section', '')} {document}")
            doc_norm = _normalize_text(document)
            labeled_match_hits = 0
            for family, asked_values in query_profile["labeled_terms"].items():
                labeled_match_hits += sum(1 for value in asked_values if value in document_labeled_terms.get(family, set()))
            if labeled_match_hits:
                lexical_score += labeled_match_hits * LABELED_MATCH_PRIORITY_BOOST
                disambiguation_hits = sum(
                    1 for term in query_profile["disambiguation_terms"]
                    if term in doc_norm or term in metadata_norm or term in section_title
                )
                if query_profile["disambiguation_terms"] and disambiguation_hits == 0:
                    lexical_score -= LABELED_CONTEXT_PENALTY
            ranked_items.append((lexical_score, doc_id, document, metadata))
            seen_ids.add(doc_id)

    if not ranked_items:
        return "", [], {
            "candidate_count": candidate_count,
            "selected_count": 0,
            "source_diversity": 0,
            "expected_domains": expected_domains,
            "domain_match_ratio": 0.0,
        }

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
    source_cap = MAX_CHUNKS_PER_SOURCE + (2 if broad_query else 0)
    section_cap = 3 if broad_query else 2
    diversity_target = 0
    if query_profile["comparison_query"] or query_profile["summary_query"] or query_profile["list_query"]:
        diversity_target = min(3, len({item[3].get("source", "unknown") for item in ranked_items}))
    for item in ranked_items:
        _, doc_id, _, metadata = item
        source_name = metadata.get("source", "unknown")
        section_name = metadata.get("section", "") or "__none__"
        if diversity_target and len(source_counts) < diversity_target and source_name in source_counts:
            continue
        if source_counts.get(source_name, 0) >= source_cap:
            continue
        if section_counts.get((source_name, section_name), 0) >= section_cap:
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
    matched_domains = 0
    for _, _, document, metadata in selected:
        clean_section = _sanitize_section_label(str(metadata.get("section", "")))
        section_suffix = f", {clean_section}" if clean_section else ""
        source_label = f"{metadata['source']} (pag. {metadata['page']}{section_suffix})"
        context_parts.append(f"[{source_label}]\n{document}")
        if source_label not in seen_sources:
            sources.append(source_label)
            seen_sources.add(source_label)
        if expected_domains and _source_domain_key(str(metadata.get("source", "")), metadata) in expected_domains:
            matched_domains += 1

    source_names = [str(item[3].get("source", "unknown")) for item in selected]
    unique_source_names = sorted(set(source_names))
    selected_domains = sorted({
        _source_domain_key(str(item[3].get("source", "")), item[3])
        for item in selected
    })
    retrieval_stats = {
        "candidate_count": candidate_count,
        "selected_count": len(selected),
        "source_diversity": len(unique_source_names),
        "top_sources": unique_source_names[:5],
        "selected_domains": selected_domains,
        "expected_domains": expected_domains,
        "domain_match_ratio": round(matched_domains / max(len(selected), 1), 4) if expected_domains else 1.0,
        "broad_query": broad_query,
    }
    return "\n\n".join(context_parts), sources, retrieval_stats


def search_documents(question: str, n_results: int = TOP_K_RESULTS) -> Tuple[str, List[str]]:
    context, sources, _ = search_documents_detailed(question, n_results=n_results)
    return context, sources
