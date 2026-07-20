import hashlib
import html as _html
import json
import logging
import os
import re
import threading
import time
import unicodedata
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, List, Tuple

import chromadb
import fitz
from sentence_transformers import SentenceTransformer
from sentence_transformers.util import cos_sim

from chroma_client import get_chroma_client
from config import (
    CHROMA_DB_PATH,
    COLLECTION_NAME,
    CROSS_DOMAIN_MIN_SCORE,
    DOCUMENTS_PATH,
    EMBEDDING_CACHE_MAX_ENTRIES,
    ENABLE_RERANK,
    MAX_CHUNKS_PER_SOURCE,
    MIN_CHUNK_SCORE,
    RECURSIVE_PDF_SCAN,
    RAG_BACKEND,
    RAG_INDEX_VERSION,
    EMBEDDING_FP16,
    EMBEDDING_QUERY_PREFIX,
    EMBEDDING_PASSAGE_PREFIX,
    RERANK_MODEL,
    RERANK_MODEL_REVISION,
    RERANK_BM25_WEIGHT,
    RERANK_WEIGHT,
    STOPWORDS,
    TABLE_COLLECTION_NAME,
    TOP_K_RESULTS,
)
from embedding_cache import EmbeddingCache
from rag_chroma_sync import (
    add_batched_to_collection,
    delete_source_chunks,
    discover_current_files,
    file_hash,
    get_indexed_sources,
    iter_add_batches,
)
from rag_query_helpers import (
    build_query_profile as _build_query_profile_impl,
    extract_article_refs as _extract_article_refs_impl,
    extract_core_terms as _extract_core_terms_impl,
    extract_disambiguation_terms as _extract_disambiguation_terms_impl,
    extract_exact_refs as _extract_exact_refs_impl,
    extract_it_section_refs as _extract_it_section_refs_impl,
    extract_labeled_terms as _extract_labeled_terms_impl,
    extract_location_target as _extract_location_target_impl,
    extract_page_refs as _extract_page_refs_impl,
    extract_reference_terms as _extract_reference_terms_impl,
    extract_topic_terms as _extract_topic_terms_impl,
)
from rag_scoring_service import (
    bm25_score as _bm25_score_impl,
    document_layer_boost as _document_layer_boost_impl,
    domain_exclusion_penalty as _domain_exclusion_penalty_impl,
)
from rag_chunking_service import (
    find_chunk_boundary as _find_chunk_boundary_impl,
    split_text as _split_text_impl,
)
from rag_pdf_utils import decode_chunk_corruption, is_noise_chunk, normalize_rite_table31_text, ocr_page_text


CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", "900"))
CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "180"))

_DOMAINS_CONFIG_PATH = Path(__file__).parent / "domains.json"


def _load_domain_config() -> dict:
    try:
        with open(_DOMAINS_CONFIG_PATH, encoding="utf-8") as _f:
            return json.load(_f)
    except Exception as _exc:
        logging.getLogger(__name__).error("Failed to load domains.json: %s", _exc)
        return {"domains": {}, "filename_overrides": {}}


_DOMAIN_CFG = _load_domain_config()

_DOMAIN_EXCLUSIONS = _DOMAIN_CFG.get("domain_exclusions", [])

# Tokens de fuente de dominios que usan encoding shift+31 (fuentes PDF mal embebidas).
# Se deriva de domains.json en tiempo de carga; no requiere cambios en código para nuevos dominios.
_SHIFT31_SOURCE_TOKENS: frozenset[str] = frozenset(
    token
    for cfg in _DOMAIN_CFG.get("domains", {}).values()
    if cfg.get("encoding", {}).get("type") == "shift31"
    for token in cfg.get("source_tokens", [])
)
# Subconjunto con strategy=full_text: todo el cuerpo del chunk está codificado
# (a diferencia de RITE donde solo las filas de tabla están codificadas).
_SHIFT31_FULLTEXT_SOURCE_TOKENS: frozenset[str] = frozenset(
    token
    for cfg in _DOMAIN_CFG.get("domains", {}).values()
    if cfg.get("encoding", {}).get("type") == "shift31"
    and cfg.get("encoding", {}).get("strategy") == "full_text"
    for token in cfg.get("source_tokens", [])
)

# OCR opcional para PDFs escaneados. Activar con RAG_OCR_ENABLED=1.
# Requiere Tesseract instalado en el sistema y `pytesseract` + `Pillow` en pip.
# Si no están disponibles, el pipeline OCR se desactiva en caliente con un
# warning (sin romper indexación).
OCR_ENABLED = os.getenv("RAG_OCR_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
OCR_MIN_TEXT_CHARS_PER_PAGE = int(os.getenv("RAG_OCR_MIN_CHARS", "60"))
OCR_LANGUAGES = os.getenv("RAG_OCR_LANGS", "spa+eng")
OCR_RENDER_DPI = int(os.getenv("RAG_OCR_DPI", "220"))
_OCR_TESSERACT_CMD = os.getenv("TESSERACT_CMD", "")

_pytesseract = None
_PIL_Image = None
if OCR_ENABLED:
    try:
        import pytesseract as _pytesseract  # type: ignore
        from PIL import Image as _PIL_Image  # type: ignore
        if _OCR_TESSERACT_CMD:
            _pytesseract.pytesseract.tesseract_cmd = _OCR_TESSERACT_CMD
    except Exception:  # pragma: no cover
        _pytesseract = None
        _PIL_Image = None


def _ocr_page_text(page) -> str:
    """OCR de una pagina PyMuPDF. Devuelve "" si no hay Tesseract disponible."""
    return ocr_page_text(
        page,
        enabled=OCR_ENABLED,
        pytesseract_module=_pytesseract,
        pil_image_module=_PIL_Image,
        render_dpi=OCR_RENDER_DPI,
        languages=OCR_LANGUAGES,
    )


MIN_CHUNK_LENGTH = 80
CORE_TERM_PENALTY = 4
CHROMA_ADD_BATCH_SIZE = int(os.getenv("CHROMA_ADD_BATCH_SIZE", "1000"))

# ---------------------------------------------------------------------------
# Scoring por dominio específico (BT-40 / generadoras).
# Valores configurables en domains.json → domains.guias_tecnicas.scoring
# ---------------------------------------------------------------------------
_gt_scoring = _DOMAIN_CFG["domains"].get("guias_tecnicas", {}).get("scoring", {})
BT40_DOMAIN_BOOST       = _gt_scoring.get("bt40_domain_boost", 30)
BT40_DOC_TERM_BOOST     = _gt_scoring.get("bt40_doc_term_boost", 25)
BT40_SECTION_BOOST      = _gt_scoring.get("bt40_section_boost", 20)
BT40_MISMATCH_PENALTY   = _gt_scoring.get("bt40_mismatch_penalty", 20)
GENERATORS_DOMAIN_BOOST = _gt_scoring.get("generators_domain_boost", 18)
GENERATORS_ITC_BOOST    = _gt_scoring.get("generators_itc_boost", 24)
GENERATORS_TERM_BOOST   = _gt_scoring.get("generators_term_boost", 8)
GENERATORS_LEXICAL_DOMAIN = _gt_scoring.get("generators_lexical_domain", 10)
GENERATORS_LEXICAL_TERM   = _gt_scoring.get("generators_lexical_term", 16)

def _document_layer_boost(
    layer: str,
    *,
    normative_intent: bool = False,
    procedure_intent: bool = False,
) -> float:
    return _document_layer_boost_impl(
        layer,
        normalize_text=_normalize_text,
        normative_intent=normative_intent,
        procedure_intent=procedure_intent,
    )


def _is_noise_chunk(text: str) -> bool:
    return is_noise_chunk(text)


def _normalize_rite_table31_text(text: str) -> str:
    """Hace legibles filas compactadas de la Tabla 3.1 del RITE."""
    return normalize_rite_table31_text(text)


def _decode_chunk_corruption(text: str, source: str = "") -> str:
    """Decodifica corrupcion de desplazamiento +31 bytes en PDFs con fuentes mal embebidas."""
    return decode_chunk_corruption(
        text,
        source,
        shift31_source_tokens=_SHIFT31_SOURCE_TOKENS,
        shift31_fulltext_source_tokens=_SHIFT31_FULLTEXT_SOURCE_TOKENS,
    )


def _bm25_score(query_tokens: set, doc_text: str, avg_doc_len: float, k1: float = 1.5, b: float = 0.75) -> float:
    return _bm25_score_impl(
        query_tokens,
        doc_text,
        avg_doc_len,
        normalize_text=_normalize_text,
        k1=k1,
        b=b,
    )


MAX_TOPIC_TOKENS = 6
SECTION_LABEL_MAX_LENGTH = 80
HEADING_LINE_MAX_WORDS = 10
NUMERIC_PRIORITY_BOOST = 6
REFERENCE_PRIORITY_BOOST = 5
SECTION_PRIORITY_BOOST = 8
SECTION_TITLE_BOOST = 6
COMPARISON_PRIORITY_BOOST = 3
LOW_SIGNAL_PENALTY = 5
CHUNK_SENTENCE_GRACE = int(os.getenv("RAG_CHUNK_SENTENCE_GRACE", "260"))
HEADING_PATTERN = re.compile(r"^(?:\d+(?:\.\d+)*[\.\)]?\s+)?[A-ZÁÉÍÓÚÜÑ][A-ZÁÉÍÓÚÜÑa-záéíóúüñ\s\-/,:()]{3,}$")
NORMATIVE_HEADING_PATTERN = re.compile(
    r"^(?:"
    r"art(?:iculo|[ií]culo|\.?)\s+\d+[.\s-]+.+|"
    r"it\s+\d+(?:\.\d+){0,2}[.\s-]+.+|"
    r"itc[-\s]*(?:bt|lat|rat)[-\s]*\d+.*|"
    r"\d+(?:\.\d+){0,4}[.)]?\s+[A-Z].+|"
    r"(?:disposicion|disposición|anexo|capitulo|capítulo|titulo|título|instruccion|instrucción)\s+.+"
    r")$",
    re.IGNORECASE,
)
REFERENCE_PATTERN = re.compile(r"\b(?:itc[-\s]*(?:bt|lat|rat)[-\s]*\d+|art(?:iculo)?\.?\s*\d+|tabla\s*\d+)\b", re.IGNORECASE)
NUMERIC_PATTERN = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:a/mm2|a/mm²|mm2|mm²|m2|m²|kva|kw|ma|kv|cm|mm|m|bar|hz|ohmios?|ohm|w|v|a|%)?\b",
    re.IGNORECASE,
)
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
    r"(?:\btabla\b|\bcircuitos?\s+minimos?\b|\bcircuitos?\s+mínimos?\b|"
    r"\bcircuitos?\s+(?:que\s+)?no\s+pueden?\s+faltar\b|"
    r"\brelacion\s+de\b|\brelación\s+de\b|\blista\s+completa\b)",
    re.IGNORECASE,
)
PAGE_REFERENCE_PATTERN = re.compile(r"\b(?:pag(?:ina)?|p[áa]g(?:ina)?|page)\.?\s*(\d{1,4})\b", re.IGNORECASE)
ITC_REFERENCE_PATTERN = re.compile(r"\b(?:itc|guia|gu[ií]a)[-\s]*(bt|lat|rat)?[-\s]*(\d{1,2})\b", re.IGNORECASE)
# Refs a instrucciones técnicas numeradas del RITE: "IT 3", "IT 3.3", "IT 1.1.1"
IT_SECTION_REFERENCE_PATTERN = re.compile(r"\bit\s+(\d+(?:\.\d+){0,2})\b", re.IGNORECASE)
TABLE_REFERENCE_PATTERN = re.compile(r"\btabla\s*(\d{1,3}(?:\.\d+)?)\b", re.IGNORECASE)
ARTICLE_REFERENCE_PATTERN = re.compile(
    r"\bart(?:(?:iculo)|(?:[^\da-z\s]{1,4}culo)|(?:\.))?\s*(\d{1,3})\b",
    re.IGNORECASE,
)
LOCATION_QUERY_PATTERN = re.compile(
    r"\b(?:pagina|pag|page|donde\s+(?:aparece|esta|se\s+encuentra)|ubicacion|apartado\s+de)\b",
    re.IGNORECASE,
)
UNIT_QUERY_PATTERN = re.compile(
    r"\b(?:mm2|mm²|m2|m²|cm|mm|kw|kva|w|v|a|ma|ohmios?|ohm|%|bar|hz|a/mm2|a/mm²)\b|Ω",
    re.IGNORECASE,
)
LIST_CUE_PATTERN = re.compile(r"(?:^|\n)(?:[-*]\s+|\d+\.\s+)", re.IGNORECASE)
# Detección de corrupción +31 en PDFs con fuentes mal embebidas (ej. RITE Tabla 3.1).
# Firma: línea que empieza con guion o dígito seguido de 3+ letras mayúsculas ASCII.
_LINE_CORRUPT_PATTERN = re.compile(r"^[-0-9][A-Z\[\]]{3,}")
# Códigos de periodicidad al final de fila (p.ej. " U U", " T", " 2U") — no se decodifican.
_TRAILING_CODES_RE = re.compile(r"(\s+[A-Z0-9]{1,3}){1,4}\s*$")
CIRCUIT_LIST_CUE_PATTERN = re.compile(r"\bC(?:1[0-3]?|[1-9])\b", re.IGNORECASE)
TABLE_ROW_CUE_PATTERN = re.compile(
    r"(?:\b(?:c\d{1,2}|itc[-\s]*[a-z]{1,4}[-\s]*\d+|ip\d{2}|ik\d{2})\b|\s{2,}|[;|]{1}|\b(?:fase|circuito|uso|proteccion|potencia|seccion|denominacion|descripcion)\b)",
    re.IGNORECASE,
)
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
SCOPE_QUERY_PATTERN = re.compile(
    r"(?:\bobjeto\b|\bobjetivo\b|\bambito\b|\bámbito\b|\balcance\b|\bfinalidad\b|\bfuncion\b|\bfunción\b|\bpapel\b)",
    re.IGNORECASE,
)
MOTIVATION_QUERY_PATTERN = re.compile(
    r"(?:\bpor\s+que\b|\bpor\s+qué\b|\bmotivo\b|\bjustificacion\b|\bjustificación\b|"
    r"\bexposicion\s+de\s+motivos\b|\bexposición\s+de\s+motivos\b|\bpreambulo\b|\bpreámbulo\b)",
    re.IGNORECASE,
)
MOTIVATION_CUE_PATTERN = re.compile(
    r"\b(?:preambulo|preámbulo|exposicion\s+de\s+motivos|exposición\s+de\s+motivos|"
    r"la\s+necesidad\s+de|se\s+hace\s+necesario|con\s+el\s+fin\s+de|con\s+objeto\s+de|"
    r"para\s+adaptar|directiva|eficiencia\s+energetica|eficiencia\s+energética|"
    r"seguridad|ahorro\s+de\s+energia|ahorro\s+de\s+energía)\b",
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
DOCUMENT_VARIANT_BOOST = int(os.getenv("RAG_DOCUMENT_VARIANT_BOOST", "18"))
DOCUMENT_VARIANT_MISMATCH_PENALTY = int(os.getenv("RAG_DOCUMENT_VARIANT_MISMATCH_PENALTY", "10"))
SOURCE_MENTION_BOOST = int(os.getenv("RAG_SOURCE_MENTION_BOOST", "14"))
DOCUMENT_VARIANT_ORDER_BOOST = int(os.getenv("RAG_DOCUMENT_VARIANT_ORDER_BOOST", "4"))


def _domain_exclusion_penalty(expected_domains: list, source_domain: str, question_lower: str) -> int:
    return _domain_exclusion_penalty_impl(
        expected_domains,
        source_domain,
        question_lower,
        exclusions=_DOMAIN_EXCLUSIONS,
    )
IT_SECTION_BOOST = 20
ARTICLE_REF_BOOST = int(os.getenv("RAG_ARTICLE_REF_BOOST", "26"))
LABELED_CONTEXT_PENALTY = 10
TECHNICAL_EQUIVALENT_BOOST = 18
NORMATIVE_INTENT_BOOST = 16
NORMATIVE_INTENT_PATTERN = re.compile(
    r"\b(?:valid[oa]s?|permitid[oa]s?|admitid[oa]s?|aplica(?:n|ble)?|corresponde(?:n)?|"
    r"sistemas?|esquemas?|tipos?|clases?|categorias?|categorias?|requisitos?|prescripciones?)\b",
    re.IGNORECASE,
)
NORMATIVE_APPLICATION_PHRASES = (
    "aplicacion de los tres tipos de esquemas",
    "campo de aplicacion",
    "ambito de aplicacion",
    "prescripciones generales",
    "condiciones generales",
    "requisitos generales",
    "excepciones",
    "red de distribucion publica",
    "instalaciones receptoras alimentadas directamente",
    "esquema de distribucion para instalaciones receptoras",
)
NORMATIVE_CLASSIFICATION_PHRASES = (
    "tipos de esquemas",
    "esquemas de distribucion",
    "clasificacion",
    "se distinguen",
    "se establecen en funcion",
    "definicion",
)
TECHNICAL_EQUIVALENT_RULES = (
    {
        "if_any": ("vehiculo electrico", "coche electrico", "recarga", "punto de recarga", "estacion de recarga"),
        "emit": (
            "ITC-BT-52",
            "infraestructura para la recarga",
            "recarga de vehiculos electricos",
            "sistemas de conexion del neutro",
            "contactos indirectos",
        ),
    },
    {
        "if_any": ("puesta a tierra", "toma de tierra", "conductor de proteccion", "masas", "neutro"),
        "emit": (
            "sistemas de conexion del neutro",
            "esquema TT",
            "esquema TN",
            "esquema IT",
            "TN-S",
            "contactos indirectos",
        ),
    },
    {
        "if_any": ("proteccion diferencial", "diferencial", "contactos indirectos", "contacto indirecto"),
        "emit": (
            "dispositivo de proteccion diferencial",
            "dispositivos de proteccion diferencial",
            "corriente diferencial-residual",
            "contactos indirectos",
        ),
    },
    {
        "if_any": ("mantenimiento", "periodicidad", "cada cuanto", "revision", "limpieza"),
        "emit": (
            "operaciones de mantenimiento preventivo",
            "periodicidad",
            "tabla 3.1",
        ),
    },
)
# Configuración de dominios cargada desde domains.json.
# Para añadir un dominio nuevo edita ese fichero — no este código.
DOMAIN_FILENAME_OVERRIDES: dict = _DOMAIN_CFG.get("filename_overrides", {})
DOMAIN_PATH_PROFILES: tuple = tuple(_DOMAIN_CFG.get("path_profiles", []))

DOMAIN_SOURCE_TOKEN_HINTS: dict = {
    name: set(cfg.get("source_tokens", []))
    for name, cfg in _DOMAIN_CFG["domains"].items()
}

DOMAIN_SOURCE_PHRASE_HINTS: dict = {
    name: tuple(cfg.get("source_phrases", []))
    for name, cfg in _DOMAIN_CFG["domains"].items()
}

DOMAIN_FOLDER_PREFIXES: dict = {
    name: tuple(cfg.get("folder_prefixes", []))
    for name, cfg in _DOMAIN_CFG["domains"].items()
}

DOMAIN_TAXONOMY: dict = {
    name: {
        "department": str(cfg.get("department", "general") or "general").strip() or "general",
        "document_type": str(cfg.get("document_type", "documento") or "documento").strip() or "documento",
        "document_layer": str(cfg.get("document_layer", "") or "").strip(),
        "confidentiality": str(cfg.get("confidentiality", "internal") or "internal").strip() or "internal",
    }
    for name, cfg in _DOMAIN_CFG["domains"].items()
}


logger = logging.getLogger(__name__)

# --- Query cache LRU con TTL ---
_QUERY_CACHE_MAX = int(os.getenv("RAG_QUERY_CACHE_MAX", "64"))
_QUERY_CACHE_TTL = float(os.getenv("RAG_QUERY_CACHE_TTL", "300"))  # 5 min


class _QueryCache:
    """Cache LRU thread-safe con TTL por entrada."""

    def __init__(self, max_size: int = _QUERY_CACHE_MAX, ttl: float = _QUERY_CACHE_TTL):
        self._cache: OrderedDict[str, Tuple[float, object]] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl
        self._lock = threading.Lock()

    def _key(
        self,
        question: str,
        n_results: int,
        domain: str = "",
        hint_domains: List[str] | None = None,
        hint_document_variants: List[str] | None = None,
        hint_article_refs: List[str] | None = None,
        hint_it_section_refs: List[str] | None = None,
    ) -> str:
        normalized = _normalize_text(question.strip())
        hints = ",".join(sorted(hint_domains)) if hint_domains else ""
        variant_hints = ",".join(sorted(hint_document_variants)) if hint_document_variants else ""
        article_hints = ",".join(sorted(hint_article_refs)) if hint_article_refs else ""
        it_hints = ",".join(sorted(hint_it_section_refs)) if hint_it_section_refs else ""
        return f"{normalized}::{n_results}::{_normalize_text(domain)}::{hints}::{variant_hints}::{article_hints}::{it_hints}"

    def get(
        self,
        question: str,
        n_results: int,
        domain: str = "",
        hint_domains: List[str] | None = None,
        hint_document_variants: List[str] | None = None,
        hint_article_refs: List[str] | None = None,
        hint_it_section_refs: List[str] | None = None,
    ):
        key = self._key(
            question,
            n_results,
            domain,
            hint_domains,
            hint_document_variants,
            hint_article_refs,
            hint_it_section_refs,
        )
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            ts, value = entry
            if time.monotonic() - ts > self._ttl:
                self._cache.pop(key, None)
                return None
            self._cache.move_to_end(key)
            return value

    def put(
        self,
        question: str,
        n_results: int,
        value: object,
        domain: str = "",
        hint_domains: List[str] | None = None,
        hint_document_variants: List[str] | None = None,
        hint_article_refs: List[str] | None = None,
        hint_it_section_refs: List[str] | None = None,
    ) -> None:
        key = self._key(
            question,
            n_results,
            domain,
            hint_domains,
            hint_document_variants,
            hint_article_refs,
            hint_it_section_refs,
        )
        with self._lock:
            self._cache[key] = (time.monotonic(), value)
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


_query_cache = _QueryCache()


def _load_sentence_transformer(local_files_only: bool) -> SentenceTransformer:
    kwargs = {"local_files_only": local_files_only}
    if RERANK_MODEL_REVISION:
        kwargs["revision"] = RERANK_MODEL_REVISION
    return SentenceTransformer(RERANK_MODEL, **kwargs)


_st_model = None
try:
    _st_model = _load_sentence_transformer(local_files_only=True)
except Exception as exc:
    logger.info("Modelo '%s' no disponible en cache local; intentando descarga: %s", RERANK_MODEL, str(exc))
    try:
        _st_model = _load_sentence_transformer(local_files_only=False)
    except Exception as net_exc:
        logger.warning("No se pudo cargar modelo multilingüe '%s': %s", RERANK_MODEL, str(net_exc))

if _st_model is not None and EMBEDDING_FP16:
    _st_model = _st_model.half()
    logger.info("Modelo '%s' cargado en FP16 (~1.3 GB RAM)", RERANK_MODEL)

rerank_model = _st_model if ENABLE_RERANK else None


class _MultilingualEF:
    @staticmethod
    def name() -> str:
        return RERANK_MODEL.split("/")[-1].lower()

    def is_legacy(self) -> bool:
        return False

    def default_space(self) -> str:
        return "cosine"

    def supported_spaces(self) -> List[str]:
        return ["cosine", "l2", "ip"]

    def get_config(self) -> Dict[str, object]:
        return {
            "name": self.name(),
            "default_space": self.default_space(),
            "supported_spaces": self.supported_spaces(),
            "is_legacy": self.is_legacy(),
        }

    def _encode(self, input: List[str]) -> List[List[float]]:
        return _encode_passages(input)

    def __call__(self, input: List[str]) -> List[List[float]]:
        return self._encode(input)

    def embed_documents(self, input: List[str]) -> List[List[float]]:
        return self._encode(input)

    def embed_query(self, input: List[str]) -> List[List[float]]:
        return [_encode_query(text) for text in input]


def _apply_embedding_prefix(prefix: str, text: str) -> str:
    if not prefix:
        return text
    separator = "" if prefix.endswith((" ", "\t", "\n")) else " "
    return f"{prefix}{separator}{text}"


def _encode_passages(texts: List[str]) -> List[List[float]]:
    if _st_model is None:
        raise RuntimeError("Modelo de embeddings no disponible")
    prefixed_texts = [_apply_embedding_prefix(EMBEDDING_PASSAGE_PREFIX, text) for text in texts]
    return _st_model.encode(
        prefixed_texts,
        convert_to_numpy=True,
        batch_size=64,
        show_progress_bar=len(texts) > 100,
    ).tolist()


def _encode_passage(text: str) -> List[float]:
    return _encode_passages([text])[0]


def _encode_query(text: str) -> List[float]:
    """Codifica una query aplicando el prefijo de instrucción si el modelo lo requiere (ej. e5)."""
    if _st_model is None:
        raise RuntimeError("Modelo de embeddings no disponible")
    return _st_model.encode(
        _apply_embedding_prefix(EMBEDDING_QUERY_PREFIX, text),
        convert_to_numpy=True,
    ).tolist()


_model_tag = RERANK_MODEL.split("/")[-1].lower().replace("-", "")
_prefix_tag = hashlib.md5(
    f"{EMBEDDING_QUERY_PREFIX}|{EMBEDDING_PASSAGE_PREFIX}".encode("utf-8"),
    usedforsecurity=False,
).hexdigest()[:8]


def _rag_index_version_tag(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value or "").strip("-") or "1"


_index_version_tag = _rag_index_version_tag(RAG_INDEX_VERSION)
_EF_VERSION = f"{_model_tag}-v2-p{_prefix_tag}-c{CHUNK_SIZE}-o{CHUNK_OVERLAP}-i{_index_version_tag}"

_EMBEDDING_CACHE_FILE = Path(CHROMA_DB_PATH) / "_embedding_cache.json"
_embedding_cache = EmbeddingCache(
    _EMBEDDING_CACHE_FILE,
    ef_version=_EF_VERSION,
    max_entries=EMBEDDING_CACHE_MAX_ENTRIES,
)


def _chunk_cache_key(text: str) -> str:
    return _embedding_cache.key_for_text(text)


def _load_embedding_cache() -> None:
    _embedding_cache.load()


def _save_embedding_cache() -> None:
    _embedding_cache.save()


def _get_or_reset_collection(client: chromadb.PersistentClient, name: str, ef) -> chromadb.Collection:
    try:
        col = client.get_or_create_collection(name=name, embedding_function=ef, metadata={"ef_version": _EF_VERSION})
        if (col.metadata or {}).get("ef_version") != _EF_VERSION:
            raise ValueError("ef_version mismatch")
        return col
    except ValueError:
        # Solo borrar ante incompatibilidad de versión de embeddings — nunca ante errores transitorios.
        logger.info("Versión de embeddings distinta a la indexada — borrando y recreando colección '%s'", name)
        try:
            client.delete_collection(name)
        except Exception:
            pass
        return client.create_collection(name=name, embedding_function=ef, metadata={"ef_version": _EF_VERSION})


_ACTIVE_COLLECTION_FILE = Path(CHROMA_DB_PATH) / "_active_collection.txt"


def _read_active_collection_name() -> str:
    try:
        name = _ACTIVE_COLLECTION_FILE.read_text(encoding="utf-8").strip()
        return name or COLLECTION_NAME
    except (FileNotFoundError, OSError):
        return COLLECTION_NAME


def _write_active_collection_name(name: str) -> None:
    try:
        _ACTIVE_COLLECTION_FILE.write_text(name, encoding="utf-8")
    except OSError as exc:
        logger.warning("No se pudo persistir el nombre de colección activa: %s", exc)


chroma_client = get_chroma_client()
_embedding_fn = _MultilingualEF() if _st_model else None
collection = _get_or_reset_collection(chroma_client, _read_active_collection_name(), _embedding_fn)
table_collection = _get_or_reset_collection(chroma_client, TABLE_COLLECTION_NAME, _embedding_fn)


def _ensure_active_chroma_collections() -> None:
    global collection, table_collection
    try:
        collection.count()
        table_collection.count()
    except Exception:
        logger.warning("Coleccion Chroma no disponible; recreando handles locales")
        collection = _get_or_reset_collection(chroma_client, COLLECTION_NAME, _embedding_fn)
        table_collection = _get_or_reset_collection(chroma_client, TABLE_COLLECTION_NAME, _embedding_fn)


def _find_chunk_boundary(text: str, chunk_size: int = CHUNK_SIZE, grace: int = CHUNK_SENTENCE_GRACE) -> int:
    return _find_chunk_boundary_impl(
        text,
        chunk_size=chunk_size,
        grace=grace,
        min_chunk_length=MIN_CHUNK_LENGTH,
    )


def _split_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    return _split_text_impl(
        text,
        chunk_size=chunk_size,
        overlap=overlap,
        chunk_sentence_grace=CHUNK_SENTENCE_GRACE,
        min_chunk_length=MIN_CHUNK_LENGTH,
        extract_text_blocks=_extract_text_blocks,
        format_chunk=_format_chunk,
        find_chunk_boundary_fn=_find_chunk_boundary_impl,
    )


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
    if _is_normative_heading(cleaned):
        return len(cleaned) <= SECTION_LABEL_MAX_LENGTH
    if len(cleaned.split()) > HEADING_LINE_MAX_WORDS:
        return False
    if len(cleaned) > SECTION_LABEL_MAX_LENGTH:
        return False
    if cleaned.endswith("."):
        return False
    if "," in cleaned and len(cleaned.split()) > 6:
        return False
    return bool(HEADING_PATTERN.match(cleaned))


def _is_normative_heading(line: str) -> bool:
    cleaned = _clean_line(line)
    if not cleaned:
        return False
    normalized = _normalize_text(cleaned)
    return bool(NORMATIVE_HEADING_PATTERN.match(cleaned) or NORMATIVE_HEADING_PATTERN.match(normalized))


def _sanitize_section_label(section: str) -> str:
    cleaned = _clean_line(section)
    if not cleaned:
        return ""
    if len(cleaned) > SECTION_LABEL_MAX_LENGTH:
        return ""
    if _is_normative_heading(cleaned):
        return cleaned
    if cleaned.endswith("."):
        return ""
    if "," in cleaned and len(cleaned.split()) > 6:
        return ""
    return cleaned


def _split_inline_normative_heading(line: str) -> Tuple[str, str]:
    cleaned = _clean_line(line)
    if not cleaned or _is_heading(cleaned):
        return "", cleaned

    normalized = _normalize_text(cleaned)
    if re.search(r"\.\s*\.\s*\.\s*\.", cleaned):
        return "", cleaned
    if len(re.findall(r"\bart(?:iculo|[ií]culo|\.?)\s+\d+\b", normalized)) >= 2:
        return "", cleaned

    for match in re.finditer(r"[.:;]\s+", cleaned):
        boundary = match.start() + 1
        candidate = cleaned[:boundary].strip()
        remainder = cleaned[boundary:].lstrip(" .;:")
        normalized_candidate = _normalize_text(candidate)
        if not remainder or re.fullmatch(r"[\W\d_]+", remainder):
            continue
        if len(candidate) > SECTION_LABEL_MAX_LENGTH:
            continue
        if re.fullmatch(r"it\s+\d+(?:\.\d+){0,2}\.?", normalized_candidate):
            continue
        if _is_normative_heading(candidate):
            heading = _sanitize_section_label(candidate[:SECTION_LABEL_MAX_LENGTH])
            if heading:
                return heading, remainder
    return "", cleaned


def _extract_text_blocks(text: str) -> List[Dict[str, str]]:
    lines = [line.rstrip() for line in text.splitlines()]
    blocks = []
    current_lines = []
    current_section = ""

    def flush_block() -> None:
        nonlocal current_lines, current_section
        block_text = _clean_line(" ".join(current_lines))
        if block_text and current_section and not block_text.lower().startswith(current_section.lower()):
            sep = " " if current_section.endswith((".", ":", ";")) else ". "
            block_text = f"{current_section}{sep}{block_text}"
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

        inline_heading, inline_remainder = _split_inline_normative_heading(line)
        if inline_heading:
            if current_lines:
                flush_block()
            current_section = inline_heading
            current_lines.append(inline_remainder)
            continue

        current_lines.append(line)

    if current_lines:
        flush_block()

    if not blocks:
        clean_text = " ".join(text.split())
        if clean_text:
            blocks.append({"text": clean_text, "section": ""})

    return blocks


def _split_structured_blocks(
    blocks: List[Dict[str, str]],
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> List[Tuple[str, str]]:
    if not blocks:
        return []

    chunks: List[Tuple[str, str]] = []
    current = ""
    current_section = ""

    for block in blocks:
        block_text = str(block.get("text", "") or "").strip()
        block_section = str(block.get("section", "") or "").strip()
        if not block_text:
            continue

        candidate = f"{current} {block_text}".strip() if current else block_text
        if len(candidate) <= chunk_size:
            current = candidate
            current_section = current_section or block_section
            continue

        if current and len(current) >= MIN_CHUNK_LENGTH:
            chunks.append((current, current_section))

        overlap_tail = current[-overlap:].strip() if current else ""
        current = f"{overlap_tail} {block_text}".strip() if overlap_tail else block_text
        current_section = block_section

        while len(current) > chunk_size:
            boundary = _find_chunk_boundary_impl(
                current,
                chunk_size=chunk_size,
                grace=CHUNK_SENTENCE_GRACE,
                min_chunk_length=MIN_CHUNK_LENGTH,
            )
            partial = current[:boundary].strip()
            if len(partial) >= MIN_CHUNK_LENGTH:
                chunks.append((partial, current_section))
            next_start = max(boundary - overlap, 1)
            current = current[next_start:].strip()

    if current and len(current) >= MIN_CHUNK_LENGTH:
        chunks.append((current, current_section))

    return chunks


def _looks_like_table_block(text: str) -> bool:
    if not text:
        return False
    normalized = _normalize_text(text)
    row_cues = len(TABLE_ROW_CUE_PATTERN.findall(normalized))
    list_cues = len(LIST_CUE_PATTERN.findall(text))
    has_table_word = "tabla" in normalized
    has_circuit_codes = len(CIRCUIT_LIST_CUE_PATTERN.findall(text))
    return (
        has_table_word
        or has_circuit_codes >= 2
        or row_cues >= 3
        or (list_cues >= 2 and row_cues >= 2)
    )


def _table_signal_count(text: str) -> int:
    if not text:
        return 0
    count = 0
    count += len(CIRCUIT_LIST_CUE_PATTERN.findall(text))
    count += len(re.findall(r"\b(?:circuito|fase|uso|potencia|seccion|proteccion)\b", _normalize_text(text)))
    return count


SPECIFIC_SCOPE_TERMS = (
    "quirofano", "quirófano", "sala de intervencion",
    "recarga", "alta presion",
)
GENERAL_SCOPE_TERMS = (
    "local", "emplazamiento", "vivienda", "red subterranea", "redes subterraneas",
    "instalacion interior", "instalaciones interiores", "generadora", "generadoras",
    "exterior", "interior",
)
SCOPE_PENALTY_SPECIFIC = 18


def _scope_hint(text: str, section: str = "") -> str:
    normalized = _normalize_text(f"{section} {text}")
    hints = []
    for term in GENERAL_SCOPE_TERMS + SPECIFIC_SCOPE_TERMS:
        if _normalize_text(term) in normalized:
            hints.append(term)
    # Fallback: usar el heading/section como scope genérico si no hay match
    if not hints and section:
        section_clean = _normalize_text(section).strip()
        if section_clean and len(section_clean) >= 6:
            hints.append(section_clean[:60])
    return ", ".join(list(dict.fromkeys(hints))[:8])


def _question_mentions_specific_scope(normalized_question: str) -> set:
    mentioned = set()
    for term in SPECIFIC_SCOPE_TERMS:
        if _normalize_text(term) in normalized_question:
            mentioned.add(_normalize_text(term))
    return mentioned


def _content_intent(text: str, section: str = "", chunk_kind: str = "") -> str:
    combined = f"{section} {text}"
    normalized = _normalize_text(combined)
    if chunk_kind in {"table", "table_row"} or "tabla" in normalized:
        return "table"
    if NUMERIC_PATTERN.search(combined):
        return "numeric_value"
    if any(term in normalized for term in ("procedimiento", "pasos", "puesta en marcha", "ejecucion")):
        return "procedure"
    if any(term in normalized for term in ("objeto", "campo de aplicacion", "ambito")):
        return "scope"
    if any(term in normalized for term in ("debe", "deben", "distancia minima", "no sera superior", "no debera")):
        return "requirement"
    return "text"


def _extract_printed_page(text: str) -> str:
    candidates = re.findall(r"[–-]\s*(\d{1,4})\s*[–-]", text or "")
    return candidates[0] if candidates else ""


def _row_document(table_title: str, headers: List[str], row: List[str]) -> str:
    pairs = []
    for idx, value in enumerate(row):
        clean_value = " ".join(str(value or "").split())
        if not clean_value:
            continue
        header = " ".join(str(headers[idx] if idx < len(headers) else f"columna_{idx + 1}").split())
        header = header or f"columna_{idx + 1}"
        pairs.append(f"{header}: {clean_value}")
    if not pairs:
        return ""
    title = table_title or "Tabla"
    return f"FILA_TABLA. {title}. " + "; ".join(pairs) + "."


_LEGEND_PATTERN = re.compile(
    r"^(\d*\s*[a-z*])\s+(.+)",
    re.IGNORECASE,
)


def _extract_table_legend(data: List[List], n_cols: int) -> Dict[str, str]:
    """Extrae leyenda de abreviaturas de las filas finales de una tabla.

    Soporta dos formatos:
    - Una celda:  ['t una vez por temporada (AÑO).', '', '']
    - Dos celdas: ['t', 'una vez por temporada (AÑO).', '']  (formato RITE IT3)
    """
    legend: Dict[str, str] = {}
    for row in reversed(data):
        cells = [str(c or "").strip() for c in row]
        populated = [c for c in cells if c]

        if len(populated) == 1:
            # Formato una celda: "t una vez por temporada"
            m = _LEGEND_PATTERN.match(populated[0])
            if not m:
                break
            abbrev = re.sub(r"\s+", " ", m.group(1)).strip().lower()
            meaning = re.sub(r"\s+", " ", m.group(2)).strip().rstrip(".")
            legend[abbrev] = meaning

        elif len(populated) == 2 and cells[0] and cells[1]:
            # Formato dos celdas: ['2 t', 'dos veces por temporada']
            # La abreviatura debe ser corta (≤6 chars, solo letras/dígitos/*)
            abbrev_text = cells[0]
            if len(abbrev_text) <= 6 and re.match(r'^[\d\s]*[a-zA-Z*]+$', abbrev_text):
                abbrev = re.sub(r"\s+", " ", abbrev_text).strip().lower()
                meaning = re.sub(r"\s+", " ", cells[1]).strip().rstrip(".")
                legend[abbrev] = meaning
            else:
                break

        else:
            break

    return legend


def _expand_legend_value(value: str, legend: Dict[str, str]) -> str:
    if not legend or not value:
        return value
    key = value.strip().lower()
    if key in legend:
        return f"{value} ({legend[key]})"
    return value


def _table_headers_and_body(data: List[List]) -> Tuple[List[str], List[List]]:
    n_cols = len(data[0])
    raw_headers = [str(cell or "").strip() for cell in data[0]]
    filled_header_count = sum(1 for value in raw_headers if value)
    composite_header = " ".join(raw_headers)
    power_columns = re.search(
        r"<\s*70\s*kW.*?70\s*kW\s*<",
        composite_header,
        flags=re.IGNORECASE,
    )
    if n_cols == 4 and power_columns:
        raw_headers = ["numero", "operacion", "< 70 kW", "> 70 kW"]
        body = data[1:]
    elif filled_header_count < 2 and len(data) > 1:
        candidate_row1 = [str(cell or "").strip() for cell in data[1]]
        filled_row1 = sum(1 for value in candidate_row1 if value)
        if filled_row1 >= 2 and not any(
            value.isdigit() and len(value) <= 3
            for value in candidate_row1
            if value
        ):
            raw_headers = candidate_row1
            body = data[2:]
        else:
            raw_headers = [f"columna_{index + 1}" for index in range(n_cols)]
            body = data[1:]
    else:
        body = data[1:]
    headers = [
        re.sub(r"\s+", " ", value) or f"columna_{index + 1}"
        for index, value in enumerate(raw_headers)
    ]
    return headers, body


def _extract_table_row_chunks(page, page_text: str) -> List[Dict[str, object]]:
    try:
        tables = page.find_tables()
    except Exception:
        return []
    rows = []
    flat_page = " ".join((page_text or "").split())
    table_titles = re.findall(r"(Tabla\s+\d+[^.\n]*(?:\.[^.\n]*)?)", page_text or "", re.IGNORECASE)
    for table_index, table in enumerate(getattr(tables, "tables", []) or [], start=1):
        try:
            data = table.extract()
        except Exception:
            continue
        if not data or len(data) < 2:
            continue
        n_cols = len(data[0])
        legend = _extract_table_legend(data, n_cols)
        headers, body = _table_headers_and_body(data)
        table_title = table_titles[table_index - 1] if table_index <= len(table_titles) else ""
        if not table_title:
            title_match = re.search(r"Tabla\s+\d+[^.]{0,160}", flat_page, re.IGNORECASE)
            table_title = title_match.group(0) if title_match else f"Tabla {table_index}"
        legend_count = len(legend)
        body_end = len(body) - legend_count if legend_count else len(body)
        for row_index, row in enumerate(body[:body_end], start=1):
            values = [str(cell or "").strip() for cell in row]
            if sum(1 for value in values if value) < 2:
                continue
            expanded = [_expand_legend_value(v, legend) for v in values]
            doc = _row_document(table_title, headers, expanded)
            if doc:
                rows.append({
                    "document": doc,
                    "table_title": table_title,
                    "table_index": table_index,
                    "row_index": row_index,
                })
    return rows


def _format_chunk(text: str, section: str) -> str:
    clean_text = " ".join(text.split()).strip()
    if not section:
        return clean_text
    if clean_text.lower().startswith(section.lower()):
        return clean_text
    separator = " " if section.endswith((".", ":", ";")) else ". "
    return f"{section}{separator}{clean_text}"


def _clean_question(question: str) -> str:
    cleaned = question
    cleaned = re.sub(r"\b[\w\-/]+\.pdf\s*\(pag\.\s*\d+\)\s*", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b[\w\-/]+\.pdf\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or question.strip()


def _extract_numeric_terms(text: str) -> List[str]:
    return [match.group(0).strip().lower() for match in NUMERIC_PATTERN.finditer(text or "")]


def _text_without_reference_numbers(text: str) -> str:
    cleaned = text or ""
    cleaned = ITC_REFERENCE_PATTERN.sub(" ", cleaned)
    cleaned = TABLE_REFERENCE_PATTERN.sub(" ", cleaned)
    cleaned = PAGE_REFERENCE_PATTERN.sub(" ", cleaned)
    return cleaned


def _numeric_query_variants(text: str) -> List[str]:
    variants = []
    seen = set()
    for term in _extract_numeric_terms(text):
        compact = re.sub(r"\s+", "", term)
        spaced = re.sub(r"^(\d+(?:[.,]\d+)?)([^\d\s].*)$", r"\1 \2", compact)
        expanded_values = [term, compact, spaced]
        expanded_match = re.match(r"^(\d+[.,])(\d)([^\d]*)$", compact)
        if expanded_match:
            expanded_compact = f"{expanded_match.group(1)}{expanded_match.group(2)}0{expanded_match.group(3)}"
            expanded_spaced = re.sub(r"^(\d+(?:[.,]\d+)?)([^\d\s].*)$", r"\1 \2", expanded_compact)
            expanded_values.extend((expanded_compact, expanded_spaced))
        for value in expanded_values:
            value = value.strip().lower()
            if value and value not in seen:
                seen.add(value)
                variants.append(value)
    return variants[:12]


def _numeric_value_groups(text: str) -> List[List[str]]:
    groups: List[List[str]] = []
    seen_groups = set()
    for term in _extract_numeric_terms(_text_without_reference_numbers(text)):
        compact = re.sub(r"\s+", "", term.lower())
        match = re.match(r"^(\d+(?:[.,]\d+)?)(.*)$", compact)
        if not match:
            continue
        number = match.group(1)
        unit = match.group(2)
        numbers = [number]
        sep_match = re.match(r"^(\d+[.,])(\d)$", number)
        if sep_match:
            numbers.append(number + "0")
        terms = []
        for num in numbers:
            if unit:
                terms.append(f"{num}{unit}")
                terms.append(f"{num} {unit}")
            else:
                terms.append(num)
        clean_terms = []
        seen_terms = set()
        for value in terms:
            value = value.strip().lower()
            if value and value not in seen_terms:
                seen_terms.add(value)
                clean_terms.append(value)
        group_key = tuple(clean_terms)
        if clean_terms and group_key not in seen_groups:
            seen_groups.add(group_key)
            groups.append(clean_terms)
    return groups[:6]


def _matches_numeric_group(group: List[str], text: str) -> bool:
    if not group or not text:
        return False
    normalized = _normalize_text(text)
    compact = re.sub(r"\s+", "", normalized)
    for term in group:
        normalized_term = _normalize_text(term)
        compact_term = re.sub(r"\s+", "", normalized_term)
        if re.fullmatch(r"\d+(?:[.,]\d+)?", normalized_term):
            if re.search(rf"(?<![\d,.-]){re.escape(normalized_term)}(?![\d,.-])", normalized):
                return True
            continue
        if (
            re.search(rf"(?<![\d,.-]){re.escape(normalized_term)}(?![a-z0-9,.-])", normalized)
            or re.search(rf"(?<![\d,.-]){re.escape(compact_term)}(?![a-z0-9,.-])", compact)
        ):
            return True
    return False


def _standalone_numbers(text: str) -> List[str]:
    numbers = []
    seen = set()
    for value in re.findall(r"\d+(?:[.,]\d+)?", _text_without_reference_numbers(text)):
        if value not in seen:
            seen.add(value)
            numbers.append(value)
        # Expand single-decimal-digit forms: "0,2" → also "0,20"
        sep_match = re.match(r"^(\d+[.,])(\d)$", value)
        if sep_match:
            expanded = value + "0"
            if expanded not in seen:
                seen.add(expanded)
                numbers.append(expanded)
    return numbers[:10]


def _standalone_number_hits(numbers: List[str], text: str) -> int:
    if not numbers or not text:
        return 0
    return sum(
        1 for value in numbers
        if re.search(rf"(?<![\d,.-]){re.escape(value)}(?![\d,.-])", text)
    )


def _domain_phrase_queries(clean_question: str) -> List[str]:
    """Frases exactas a buscar por dominio, configuradas en domains.json.

    Cada regla tiene if_contains (todas deben estar presentes) y emit (frases a añadir).
    Reciben boost ×80 en scoring.
    """
    normalized = _normalize_text(clean_question)
    phrases: List[str] = []
    seen: set = set()
    for cfg in _DOMAIN_CFG["domains"].values():
        for rule in cfg.get("phrase_queries", []):
            if all(t in normalized for t in rule["if_contains"]):
                for phrase in rule["emit"]:
                    if phrase not in seen:
                        seen.add(phrase)
                        phrases.append(phrase)
    return phrases


# Palabras funcionales largas que no son términos técnicos (8+ chars pero genéricas).
def _technical_equivalent_phrases(clean_question: str) -> List[str]:
    """Expande lenguaje humano a frases normativas usadas en los documentos."""
    normalized = _normalize_text(clean_question)
    phrases: List[str] = []
    seen: set = set()
    for rule in TECHNICAL_EQUIVALENT_RULES:
        if any(trigger in normalized for trigger in rule["if_any"]):
            for phrase in rule["emit"]:
                if phrase not in seen:
                    seen.add(phrase)
                    phrases.append(phrase)
    return phrases


def _query_phrase_queries(clean_question: str) -> List[str]:
    phrases: List[str] = []
    seen: set = set()
    extra_phrases = []
    if _is_normative_intent_query(clean_question):
        extra_phrases = list(NORMATIVE_APPLICATION_PHRASES) + list(NORMATIVE_CLASSIFICATION_PHRASES)
    for phrase in list(_domain_phrase_queries(clean_question)) + list(_technical_equivalent_phrases(clean_question)) + extra_phrases:
        if phrase not in seen:
            seen.add(phrase)
            phrases.append(phrase)
    return phrases


def _is_normative_intent_query(clean_question: str) -> bool:
    normalized = _normalize_text(clean_question)
    return bool(NORMATIVE_INTENT_PATTERN.search(normalized))


def _normative_application_hit_count(text: str) -> int:
    normalized = _normalize_text(text)
    return sum(1 for phrase in NORMATIVE_APPLICATION_PHRASES if phrase in normalized)


def _normative_classification_hit_count(text: str) -> int:
    normalized = _normalize_text(text)
    return sum(1 for phrase in NORMATIVE_CLASSIFICATION_PHRASES if phrase in normalized)


def _normative_complement_kind(item: Tuple[float, str, str, Dict[str, object]]) -> str:
    _, _, document, metadata = item
    text = f"{metadata.get('content_intent', '')} {metadata.get('section', '')} {document}"
    if _normative_application_hit_count(text):
        return "application"
    if _normative_classification_hit_count(text):
        return "classification"
    return ""


_GENERIC_LONG_WORDS: frozenset[str] = frozenset([
    "instalaciones", "instalacion", "articulos", "documento", "documentos",
    "reglamento", "informacion", "descripcion", "diferencia", "relacionado",
    "siguientes", "siguiente", "anterior", "anteriores", "regulacion",
    "normativa", "normativas", "especifica", "especificas", "especifico",
    "preguntas", "respuesta", "respuestas", "indicadas", "indicados",
    "obligatorio", "obligatoria", "necesario", "necesaria", "requisitos",
    "requisito", "cualquier", "diferente", "distintos", "distintas",
    "tambien", "ademas", "segun", "mediante", "conforme",
])


def _auto_technical_terms(clean_question: str) -> List[str]:
    """Extrae términos técnicos de la pregunta para usarlos como búsqueda léxica adicional.

    Solo devuelve palabras de 8+ caracteres que no sean funcionales ni genéricas.
    Se usa cuando expected_domains no está vacío para acotar el ruido.
    """
    normalized = _normalize_text(clean_question)
    words = re.findall(r"[a-z]{8,}", normalized)
    seen: set = set()
    result = []
    for w in words:
        if w not in _GENERIC_LONG_WORDS and w not in seen:
            seen.add(w)
            result.append(w)
    return result


def _extract_reference_terms(text: str) -> List[str]:
    return _extract_reference_terms_impl(text, reference_pattern=REFERENCE_PATTERN)


def _extract_it_section_refs(text: str) -> List[str]:
    return _extract_it_section_refs_impl(text, it_section_reference_pattern=IT_SECTION_REFERENCE_PATTERN)


def _extract_article_refs(text: str) -> List[str]:
    return _extract_article_refs_impl(
        text,
        normalize_text=_normalize_text,
        article_reference_pattern=ARTICLE_REFERENCE_PATTERN,
    )


def _extract_exact_refs(text: str) -> List[str]:
    return _extract_exact_refs_impl(
        text,
        itc_reference_pattern=ITC_REFERENCE_PATTERN,
        table_reference_pattern=TABLE_REFERENCE_PATTERN,
    )


def _extract_page_refs(text: str) -> List[int]:
    return _extract_page_refs_impl(text, page_reference_pattern=PAGE_REFERENCE_PATTERN)


def _extract_location_target(text: str) -> str:
    return _extract_location_target_impl(
        text,
        normalize_text=_normalize_text,
        tokenize=_tokenize,
        stopwords=STOPWORDS,
    )


def _query_intent(clean_question: str) -> str:
    normalized = _normalize_text(clean_question)
    if PAGE_REFERENCE_PATTERN.search(clean_question) or LOCATION_QUERY_PATTERN.search(normalized):
        return "document_location"
    if TABLE_REFERENCE_PATTERN.search(clean_question) or "tabla" in normalized:
        return "table_lookup"
    if COMPARISON_QUERY_PATTERN.search(normalized):
        return "comparison"
    if NUMERIC_PATTERN.search(clean_question) and (
        UNIT_QUERY_PATTERN.search(clean_question)
        or any(term in normalized for term in ("densidad", "corriente", "valor", "limite", "distancia", "seccion"))
    ):
        return "numeric_value"
    if PROCEDURE_QUERY_PATTERN.search(normalized):
        return "procedure"
    if LIST_QUERY_PATTERN.search(normalized):
        return "list"
    if DEFINITION_QUERY_PATTERN.search(normalized):
        return "definition"
    return "general"


def _extract_labeled_terms(text: str) -> Dict[str, set[str]]:
    return _extract_labeled_terms_impl(text, labeled_query_patterns=LABELED_QUERY_PATTERNS)


def _extract_disambiguation_terms(text: str, *, exclude_labeled: bool = True, limit: int = 6) -> List[str]:
    return _extract_disambiguation_terms_impl(
        text,
        tokenize=_tokenize,
        normalize_text=_normalize_text,
        stopwords=STOPWORDS,
        extract_labeled_terms_fn=_extract_labeled_terms,
        exclude_labeled=exclude_labeled,
        limit=limit,
    )


def _extract_topic_terms(text: str, limit: int = MAX_TOPIC_TOKENS) -> List[str]:
    return _extract_topic_terms_impl(
        text,
        tokenize=_tokenize,
        normalize_text=_normalize_text,
        stopwords=STOPWORDS,
        limit=limit,
    )


def _build_query_profile(clean_question: str, question_keywords: set[str]) -> Dict[str, object]:
    return _build_query_profile_impl(
        clean_question,
        question_keywords,
        normalize_text=_normalize_text,
        extract_numeric_terms=_extract_numeric_terms,
        numeric_query_variants=_numeric_query_variants,
        numeric_value_groups=_numeric_value_groups,
        standalone_numbers=_standalone_numbers,
        extract_reference_terms_fn=_extract_reference_terms,
        extract_exact_refs_fn=_extract_exact_refs,
        extract_page_refs_fn=_extract_page_refs,
        extract_location_target_fn=_extract_location_target,
        query_phrase_queries=_query_phrase_queries,
        technical_equivalent_phrases=_technical_equivalent_phrases,
        is_normative_intent_query=_is_normative_intent_query,
        query_intent=_query_intent,
        extract_labeled_terms_fn=_extract_labeled_terms,
        extract_disambiguation_terms_fn=_extract_disambiguation_terms,
        definition_query_pattern=DEFINITION_QUERY_PATTERN,
        list_query_pattern=LIST_QUERY_PATTERN,
        summary_query_pattern=SUMMARY_QUERY_PATTERN,
        table_query_pattern=TABLE_QUERY_PATTERN,
        table_reference_pattern=TABLE_REFERENCE_PATTERN,
        comparison_query_pattern=COMPARISON_QUERY_PATTERN,
        procedure_query_pattern=PROCEDURE_QUERY_PATTERN,
        generalization_query_pattern=GENERALIZATION_QUERY_PATTERN,
        scope_query_pattern=SCOPE_QUERY_PATTERN,
        motivation_query_pattern=MOTIVATION_QUERY_PATTERN,
        temporal_query_pattern=TEMPORAL_QUERY_PATTERN,
        extract_topic_terms_fn=_extract_topic_terms,
        max_topic_tokens=MAX_TOPIC_TOKENS,
        extract_article_refs_fn=_extract_article_refs,
        extract_it_section_refs_fn=_extract_it_section_refs,
    )


def _extract_core_terms(question_keywords: set[str], clean_question: str = "") -> List[str]:
    return _extract_core_terms_impl(
        question_keywords,
        clean_question=clean_question,
        normalize_text=_normalize_text,
        stopwords=STOPWORDS,
    )


def _split_metadata_refs(value: object) -> set[str]:
    refs = set()
    if isinstance(value, (list, tuple, set)):
        raw_values = value
    else:
        raw_values = re.split(r"[,;]", str(value or ""))
    for ref in raw_values:
        normalized = _normalize_text(str(ref)).replace(" ", "-")
        match = re.search(r"\bitc-bt-\d{1,2}\b", normalized)
        if match:
            parts = match.group(0).split("-")
            refs.add(f"itc-bt-{int(parts[-1]):02d}")
    return refs


def _inferred_rebt_itc_by_page_section(metadata: Dict[str, object], document: str = "") -> set[str]:
    source = _normalize_text(str(metadata.get("source", "")))
    if "baja_tension" not in source and "rebt" not in source and "boe-326" not in source:
        return set()
    try:
        page = int(str(metadata.get("page", "") or "0"))
    except ValueError:
        page = 0
    text = _normalize_text(f"{metadata.get('section', '')} {document}")
    refs: set[str] = set()
    if (
        "10. puestas a tierra" in text
        and "vida de la instalacion" in text
        and "24 v" in text
    ) or 99 <= page <= 101:
        refs.add("itc-bt-09")
    if "resistencia de las tomas de tierra" in text or 123 <= page <= 130:
        refs.add("itc-bt-18")
    if (
        "tension de contacto limite convencional" in text
        or "proteccion contra los contactos indirectos" in text
        or 161 <= page <= 166
    ):
        refs.add("itc-bt-24")
    if "red de tierra para plazas de aparcamiento" in text or 287 <= page <= 294:
        refs.add("itc-bt-52")
    return refs


def _inferred_itc_refs(metadata: Dict[str, object], document: str = "") -> set[str]:
    refs = set()
    refs.update(_split_metadata_refs(metadata.get("itc_refs", "")))
    refs.update(_split_metadata_refs(metadata.get("exact_refs", "")))
    refs.update(_split_metadata_refs(f"{metadata.get('source', '')} {metadata.get('section', '')} {document}"))
    refs.update(_inferred_rebt_itc_by_page_section(metadata, document))
    return refs




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


def _query_support_metrics(
    query_terms: set[str],
    selected_items: List[Tuple[float, str, str, Dict[str, object]]],
) -> Tuple[float, float]:
    normalized_terms = {
        _normalize_text(term)
        for term in query_terms
        if len(_normalize_text(term)) >= 4
    }
    if not normalized_terms or not selected_items:
        return 1.0, 1.0 if selected_items else 0.0
    minimum_hits = 1 if len(normalized_terms) <= 2 else 2
    supported = 0
    max_coverage = 0.0
    for _, _, document, metadata in selected_items:
        text = _normalize_text(f"{metadata.get('source', '')} {metadata.get('section', '')} {document}")
        hits = sum(1 for term in normalized_terms if term in text)
        max_coverage = max(max_coverage, hits / len(normalized_terms))
        if hits >= minimum_hits:
            supported += 1
    return round(supported / len(selected_items), 4), round(max_coverage, 4)


def _extract_requested_abbreviation_definitions(
    question: str,
    selected_items: List[Tuple[float, str, str, Dict[str, object]]],
) -> Dict[str, str]:
    requested = {
        letter.lower()
        for letter in re.findall(r"(?<![a-z0-9])([a-z])(?![a-z0-9])", _normalize_text(question))
    }
    if not requested:
        return {}
    definitions: Dict[str, str] = {}
    for _, _, document, _ in selected_items:
        for match in re.finditer(
            r"(?<![a-z0-9])(?<!\d )([a-z])\s*\(([^)\n]{4,100}(?:\([^)\n]{1,30}\)[^)\n]{0,40})?)\)",
            document,
            flags=re.IGNORECASE,
        ):
            letter = match.group(1).lower()
            meaning = " ".join(match.group(2).split()).strip(" ;,.")
            if letter in requested and meaning:
                definitions.setdefault(letter, meaning)
    return definitions


def _extract_circuit_definitions(
    selected_items: List[Tuple[float, str, str, Dict[str, object]]],
) -> Dict[str, str]:
    definitions: Dict[str, str] = {}
    for _, _, document, _ in selected_items:
        clean = " ".join(document.split())
        for match in re.finditer(
            r"\b(C(?:1[0-3]?|[1-9]))\s+(circuito\b.{10,320}?)(?=\s+C(?:1[0-3]?|[1-9])\b|$)",
            clean,
            flags=re.IGNORECASE,
        ):
            code = match.group(1).upper()
            meaning = match.group(2).strip(" .;:")
            if "destinad" in _normalize_text(meaning):
                definitions.setdefault(code, meaning)
        # En tablas aplanadas, el primer valor numerico marca el final de la
        # denominacion del circuito.
        for match in re.finditer(
            r"\b(C(?:1[0-3]?|[1-9]))\s+([^\d.;]{3,100}?)(?=\.?\s+\(?\d)",
            clean,
            flags=re.IGNORECASE,
        ):
            code = match.group(1).upper()
            label = " ".join(match.group(2).split()).strip(" .;:")
            if label and len(label.split()) <= 14:
                definitions.setdefault(code, label)
    return definitions


def _clean_context_document(document: str, source_name: str) -> str:
    decoded = _decode_chunk_corruption(document, source_name)
    return re.sub(
        r"\n?Leyenda Tabla 3\.1 RITE: en el texto extraido, U corresponde a "
        r"una vez por temporada \([^)]*\)\. Si una fila aparece como U U, la "
        r"periodicidad es una vez por temporada para ambas columnas de potencia\.",
        "",
        decoded,
        flags=re.IGNORECASE,
    ).strip()


def _metadata_text(metadata: Dict[str, object]) -> str:
    return _normalize_text(
        " ".join(
            str(metadata.get(key, ""))
            for key in (
                "source", "folder", "department", "domain", "document_type", "confidentiality",
                "regulation", "document_variant", "itc_refs", "section",
                "section_type", "topics", "table_hint", "table_title", "content_intent",
                "scope_hint", "exact_refs", "chunk_kind",
            )
        )
    )


def _matches_structural_focus(
    metadata: Dict[str, object],
    document: str,
    *,
    exact_refs: List[str] | None = None,
    article_refs: List[str] | None = None,
    it_section_refs: List[str] | None = None,
) -> bool:
    refs = [
        *list(exact_refs or []),
        *list(article_refs or []),
        *list(it_section_refs or []),
    ]
    if not refs:
        return False
    text = _normalize_text(f"{metadata.get('section', '')} {document} {_metadata_text(metadata)}")
    return any(_reference_matches_text(text, ref) for ref in refs if ref)


def _reference_matches_text(text: str, ref: str) -> bool:
    normalized_text = _normalize_text(text or "")
    normalized_ref = _normalize_text(ref or "")
    if not normalized_text or not normalized_ref:
        return False
    if normalized_ref.startswith("articulo "):
        return re.search(rf"(?<![a-z0-9]){re.escape(normalized_ref)}(?!\.\d)(?![a-z0-9])", normalized_text) is not None
    return normalized_ref in normalized_text


def _reference_starts_text(text: str, ref: str) -> bool:
    normalized_text = _normalize_text(text or "").strip()
    normalized_ref = _normalize_text(ref or "").strip()
    if not normalized_text or not normalized_ref:
        return False
    if normalized_ref.startswith("articulo "):
        return re.search(rf"^{re.escape(normalized_ref)}(?!\.\d)(?![a-z0-9])", normalized_text) is not None
    return re.search(rf"^{re.escape(normalized_ref)}(?![a-z0-9])", normalized_text) is not None


def _structural_anchor_score(
    metadata: Dict[str, object],
    document: str,
    *,
    exact_refs: List[str] | None = None,
    article_refs: List[str] | None = None,
    it_section_refs: List[str] | None = None,
) -> int:
    refs = [
        *list(exact_refs or []),
        *list(article_refs or []),
        *list(it_section_refs or []),
    ]
    if not refs:
        return 0

    section_title = _normalize_text(str(metadata.get("section", "") or "")).strip()
    document_start = _normalize_text((document or "")[:240]).strip()
    score = 0
    for ref in refs:
        if _reference_starts_text(section_title, ref):
            score += 24
        elif _reference_starts_text(document_start, ref):
            score += 16
    return score


def _clean_structural_chunk_score(
    metadata: Dict[str, object],
    document: str,
    *,
    exact_refs: List[str] | None = None,
    article_refs: List[str] | None = None,
    it_section_refs: List[str] | None = None,
) -> int:
    score = _structural_anchor_score(
        metadata,
        document,
        exact_refs=exact_refs,
        article_refs=article_refs,
        it_section_refs=it_section_refs,
    )
    if score <= 0:
        return 0

    section_type = _normalize_text(str(metadata.get("section_type", "") or ""))
    if section_type in {"article", "technical_instruction", "itc", "chapter", "disposition", "numbered_section"}:
        score += 8
    if re.search(r"\.\s*\.\s*\.\s*\.", str(metadata.get("section", "") or "")):
        score -= 24
    if _looks_like_toc_chunk(document, metadata):
        score -= 40
    return score


def _structured_search_terms(query_profile: Dict[str, object]) -> List[str]:
    terms: List[str] = []
    for article_ref in list(query_profile.get("article_refs", []))[:3]:
        article_number = str(article_ref).split()[-1]
        terms.extend((
            str(article_ref),
            str(article_ref).replace("articulo ", "artículo "),
            f"art. {article_number}",
            f"Artículo {article_number}",
        ))
    for it_ref in list(query_profile.get("it_section_refs", []))[:3]:
        terms.extend((str(it_ref), str(it_ref).upper()))

    seen: set[str] = set()
    deduped: List[str] = []
    for term in terms:
        clean = str(term or "").strip()
        if not clean:
            continue
        marker = clean
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(clean)
    return deduped


def _domain_from_filename_override(source_name: str) -> str:
    """Override exacto/substring del filename (máxima prioridad)."""
    if not source_name:
        return ""
    normalized = _normalize_text(source_name.replace("\\", "/"))
    for pattern, domain_key in DOMAIN_FILENAME_OVERRIDES.items():
        if _normalize_text(pattern) in normalized:
            return domain_key
    return ""


def _path_profile(source_name: str) -> Dict[str, str]:
    """Devuelve el perfil de la ruta mas especifica configurada."""
    normalized = _normalize_text(_source_pdf_path(source_name).replace("\\", "/"))
    matches = []
    for profile in DOMAIN_PATH_PROFILES:
        prefix = _normalize_text(str(profile.get("path_prefix", "") or "").replace("\\", "/")).rstrip("/")
        if prefix and (normalized == prefix or normalized.startswith(f"{prefix}/")):
            matches.append((len(prefix), profile))
    if not matches:
        return {}
    _, profile = max(matches, key=lambda item: item[0])
    return {
        key: str(profile.get(key, "") or "").strip()
        for key in (
            "domain",
            "department",
            "document_type",
            "document_layer",
            "confidentiality",
            "document_variant",
        )
        if str(profile.get(key, "") or "").strip()
    }


def _filename_tokens(source_name: str) -> set:
    """Tokeniza un filename a palabras+combinaciones con guiones para matcheo por word boundary.

    Ej: 'docs/itc-bt-25_circuitos.pdf' →
    {'docs', 'itc', 'bt', '25', 'circuitos', 'pdf', 'itc-bt', 'itc-bt-25', 'bt-25'}.
    """
    normalized = _normalize_text((source_name or "").replace("\\", "/").replace("/", " "))
    raw_tokens = re.findall(r"[a-z0-9&]+", normalized)
    tokens = set(raw_tokens)
    # Compuestos con guion: itc-bt, itc-bt-25, bt-25, etc.
    parts = re.split(r"[\s_]+", normalized)
    for part in parts:
        sub = [s for s in part.split("-") if s]
        for i in range(len(sub)):
            for j in range(i + 1, len(sub) + 1):
                tokens.add("-".join(sub[i:j]))
    return tokens


_SOURCE_MENTION_NOISE = frozenset({
    "pdf", "baja", "tension", "alta", "manual", "boe", "reglamento",
    "guia", "para", "los", "las", "del", "con", "por", "que",
    "de", "en", "la", "el", "un", "una", "e", "y", "o",
    "v2", "v1", "esp", "1", "2", "3",
})


def _source_mention_score(question_tokens: set, source_name: str) -> int:
    if not question_tokens or not source_name:
        return 0
    file_tokens = _filename_tokens(_source_pdf_path(source_name))
    meaningful = file_tokens - _SOURCE_MENTION_NOISE
    if not meaningful:
        return 0
    hits = question_tokens & meaningful
    if not hits:
        return 0
    min_token_len = min(len(t) for t in hits)
    if len(hits) == 1 and min_token_len < 5:
        return 0
    return SOURCE_MENTION_BOOST


def _looks_like_toc_chunk(document: str, metadata: Dict[str, object]) -> bool:
    try:
        page = int(str(metadata.get("page", "") or "0"))
    except ValueError:
        page = 0
    if page <= 0 or page > 4:
        return False

    section = str(metadata.get("section", "") or "")
    raw_text = f"{section} {document or ''}"
    normalized = _normalize_text(raw_text)
    if not normalized:
        return False

    article_mentions = len(re.findall(r"articulo\s+\d+(?:\.\d+)?", normalized))
    chapter_mentions = len(re.findall(r"capitulo\s+[ivxlcdm]+", normalized))
    dot_leaders = bool(re.search(r"\.\s*\.\s*\.\s*\.", raw_text))
    return article_mentions >= 4 and (dot_leaders or chapter_mentions >= 1)


def _source_pdf_path(source_name: str) -> str:
    """Extrae la ruta real del PDF desde un source enriquecido con pagina/snippet."""
    clean = (source_name or "").strip().replace("\\", "/")
    if not clean:
        return ""
    pdf_match = re.search(r"(?i)^(.+?\.pdf)\b", clean)
    if pdf_match:
        return pdf_match.group(1)
    return clean.split(" (", 1)[0].strip()


def _domain_from_source(source_name: str) -> str:
    tokens = _filename_tokens(source_name)
    normalized_full = _normalize_text((source_name or "").replace("\\", "/").replace("/", " "))
    for domain_key, token_hints in DOMAIN_SOURCE_TOKEN_HINTS.items():
        if tokens & {_normalize_text(t) for t in token_hints}:
            return domain_key
        for phrase in DOMAIN_SOURCE_PHRASE_HINTS.get(domain_key, ()):
            if _normalize_text(phrase) in normalized_full:
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
    # Auto-detect: si ningún prefijo configurado coincide, usa el nombre de la
    # carpeta más profunda directamente como dominio. Así documents/prl/x.pdf
    # queda como domain="prl" sin necesitar entrada en domains.json.
    if parts:
        last = re.sub(r"^\d+[_\-\s]*", "", parts[-1]).replace("-", "_").replace(" ", "_")
        if last:
            return last
    return ""


def _domain_from_configured_folder(source_name: str) -> str:
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
    """Resuelve dominio en cascada:
    1) metadata['domain'] (asignado en indexación).
    2) DOMAIN_FILENAME_OVERRIDES (pin manual por filename).
    3) DOMAIN_FOLDER_PREFIXES (estructura de carpetas configurada).
    4) DOMAIN_SOURCE_TOKEN_HINTS / DOMAIN_SOURCE_PHRASE_HINTS (heurística por tokens).
    5) Auto-dominio por carpeta no configurada.
    6) 'general'.
    """
    if metadata:
        explicit_domain = str(metadata.get("domain", "") or "").strip()
        if explicit_domain:
            return explicit_domain
    override = _domain_from_filename_override(source_name)
    if override:
        return override
    profile_domain = _path_profile(source_name).get("domain", "")
    if profile_domain:
        return profile_domain
    configured_folder = _domain_from_configured_folder(source_name)
    if configured_folder:
        return configured_folder
    source_domain = _domain_from_source(source_name)
    if source_domain != "general":
        return source_domain
    return _domain_from_folder(source_name) or source_domain


def _taxonomy_for_domain(domain: str) -> Dict[str, str]:
    return DOMAIN_TAXONOMY.get(
        domain,
        {
            "department": "general",
            "document_type": "documento",
            "document_layer": "",
            "confidentiality": "internal",
        },
    )


def _source_taxonomy(source_name: str, metadata: Dict[str, object] | None = None) -> Dict[str, str]:
    domain = _source_domain_key(source_name, metadata)
    base = dict(_taxonomy_for_domain(domain))
    for key, value in _path_profile(source_name).items():
        if key in base and value:
            base[key] = value
    if metadata:
        for key in ("department", "document_type", "document_layer", "confidentiality"):
            explicit = str(metadata.get(key, "") or "").strip()
            if explicit:
                base[key] = explicit
    return base


def _regulation_key(source_name: str, domain: str) -> str:
    normalized = _normalize_text(source_name or "")
    compact = re.sub(r"[^a-z0-9]+", "", normalized)
    if "rite" in normalized or domain == "rite":
        return "RITE"
    if "rebt" in normalized or "itc-bt" in normalized or "itcbt" in compact or domain == "baja_tension":
        return "REBT"
    if "itc-lat" in normalized or "itclat" in compact or domain == "alta_tension":
        return "LAT"
    return domain or "general"


def _document_profile_metadata(
    source_name: str,
    domain: str = "",
    metadata: Dict[str, object] | None = None,
) -> Dict[str, str]:
    """Perfil documental estable para filtrar y escalar el RAG por departamentos.

    Mantiene una forma comun para Chroma y Azure Search: al anadir PDFs nuevos,
    domains.json decide departamento/tipo y el pipeline guarda los mismos campos
    en cada chunk sin reglas especificas por backend.
    """
    resolved_domain = domain or _source_domain_key(source_name, metadata)
    taxonomy = _source_taxonomy(source_name, {**(metadata or {}), "domain": resolved_domain})
    result = {
        "department": taxonomy["department"],
        "domain": resolved_domain,
        "category": resolved_domain,
        "document_type": taxonomy["document_type"],
        "confidentiality": taxonomy["confidentiality"],
        "regulation": _regulation_key(source_name, resolved_domain),
        "document_variant": (
            str((metadata or {}).get("document_variant", "") or "").strip()
            or _path_profile(source_name).get("document_variant", "")
            or _document_variant_from_source(source_name)
        ),
    }
    layer = taxonomy.get("document_layer", "")
    if layer:
        result["document_layer"] = layer
    return result


def _chunk_profile_metadata(
    source_name: str,
    section: str,
    content: str,
    chunk_kind: str,
) -> Dict[str, object]:
    clean_section = _sanitize_section_label(section[:SECTION_LABEL_MAX_LENGTH])
    chunk_context = f"{source_name} {clean_section} {content}"
    itc_refs_str = _extract_itc_refs(chunk_context)
    exact_refs = ", ".join(_extract_exact_refs(chunk_context))
    article_refs = ", ".join(_extract_article_refs(chunk_context))
    it_section_refs = ", ".join(_extract_it_section_refs(chunk_context))
    table_hint = "tabla" if "tabla" in _normalize_text(f"{clean_section} {content}") else ""
    return {
        "section": clean_section,
        "section_type": _section_type(clean_section),
        "article_refs": article_refs,
        "it_section_refs": it_section_refs,
        "itc_refs": itc_refs_str,
        "exact_refs": exact_refs,
        "topics": ", ".join(_extract_topic_terms(content)),
        "chunk_kind": chunk_kind,
        "content_intent": _content_intent(content, clean_section, chunk_kind),
        "scope_hint": _scope_hint(content, clean_section),
        "table_hint": table_hint,
        "table_signal_count": _table_signal_count(content),
        "section_level": 1 if itc_refs_str else (2 if clean_section else 3),
    }


def _page_structure_context(
    text: str,
    active_itc: str = "",
    active_section: str = "",
) -> Tuple[str, str]:
    match = re.search(
        r"(ITC[-\s]*BT[-\s]*\d{1,2})\.?\s+([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s,]{5,})",
        text or "",
    )
    if not match:
        return active_itc, active_section
    return (
        match.group(1).replace(" ", "-").upper(),
        match.group(2).strip()[:80],
    )


def _inherit_active_structure(
    profile: Dict[str, object],
    active_itc: str,
    active_section: str,
) -> Dict[str, object]:
    enriched = dict(profile)
    if active_itc:
        active_ref = _extract_itc_refs(active_itc)
        existing_itc_refs = [
            ref.strip()
            for ref in str(enriched.get("itc_refs", "") or "").split(",")
            if ref.strip()
        ]
        enriched["itc_refs"] = ", ".join(
            [active_ref] + [
                ref for ref in existing_itc_refs
                if _normalize_text(ref) != _normalize_text(active_ref)
            ]
        )
        exact_refs = [
            ref.strip()
            for ref in str(enriched.get("exact_refs", "") or "").split(",")
            if ref.strip()
        ]
        for ref in _extract_exact_refs(active_itc):
            if ref not in exact_refs:
                exact_refs.append(ref)
        enriched["exact_refs"] = ", ".join(exact_refs)
        enriched["section_level"] = 1
    if active_section and not str(enriched.get("section", "") or "").strip():
        enriched["section"] = _sanitize_section_label(active_section)
        enriched["section_type"] = "section"
    return enriched


def _extract_itc_refs(text: str) -> str:
    refs = sorted({
        re.sub(r"\s+", "-", match.group(0).upper().replace(" ", "-"))
        for match in re.finditer(r"\bITC[-\s]*(?:BT|LAT|RAT)[-\s]*\d+\b", text or "", re.IGNORECASE)
    })
    return ", ".join(refs[:8])


def _section_type(section: str) -> str:
    normalized = _normalize_text(section or "")
    if normalized.startswith("articulo") or normalized.startswith("art."):
        return "article"
    if normalized.startswith("itc-") or normalized.startswith("itc "):
        return "itc"
    if re.match(r"^it\s+\d+(?:\.\d+)*", normalized):
        return "technical_instruction"
    if normalized.startswith("anexo"):
        return "annex"
    if normalized.startswith("disposicion"):
        return "disposition"
    if normalized.startswith("capitulo") or normalized.startswith("titulo"):
        return "chapter"
    if re.match(r"^\d+(?:\.\d+)*", normalized):
        return "numbered_section"
    return "section" if section else ""


def _document_variant_from_source(source_name: str) -> str:
    """Infiere la variante documental del nombre de fichero usando document_variants en domains.json.

    Ej: 'rite/RITE-2021-BOE-A-2021-4572.pdf' → '2021'
        'rite/RITE IT3.pdf'                  → 'it3'
    """
    if not source_name:
        return ""
    source_path = _source_pdf_path(source_name)
    normalized_filename = _normalize_text(source_path.replace("\\", "/").rsplit("/", 1)[-1])
    tokens = _filename_tokens(source_path)
    for cfg in _DOMAIN_CFG.get("domains", {}).values():
        for variant in cfg.get("document_variants", []):
            patterns = {_normalize_text(p) for p in variant.get("filename_patterns", [])}
            if tokens & patterns or any(pattern in normalized_filename for pattern in patterns):
                return variant["variant_key"]
    return ""


def _expected_document_variants(question: str, expected_domains: List[str]) -> List[str]:
    """Infiere variantes documentales esperadas de la pregunta según query_triggers en domains.json."""
    normalized = _normalize_text(question or "")
    question_tokens = {token for token in _tokenize(normalized) if token and token not in STOPWORDS}
    scored_variants: List[tuple[str, float, bool]] = []
    for domain_name in expected_domains:
        cfg = _DOMAIN_CFG["domains"].get(domain_name, {})
        for variant in cfg.get("document_variants", []):
            vk = str(variant.get("variant_key") or "").strip()
            if not vk:
                continue
            best_score = 0.0
            matched_exact = False
            for trigger in variant.get("query_triggers", []):
                normalized_trigger = _normalize_text(str(trigger or "").strip())
                if not normalized_trigger:
                    continue
                if normalized_trigger in normalized:
                    score = float(len(normalized_trigger))
                    if re.search(r"\d", normalized_trigger):
                        score += 20.0
                    best_score = max(best_score, score)
                    matched_exact = True
                    continue
                trigger_tokens = {token for token in _tokenize(normalized_trigger) if token and token not in STOPWORDS}
                if len(trigger_tokens) < 2 or not question_tokens:
                    continue
                overlap = question_tokens & trigger_tokens
                required_overlap = (
                    len(trigger_tokens)
                    if len(trigger_tokens) <= 3
                    else max(3, int(len(trigger_tokens) * 0.75))
                )
                if len(overlap) >= required_overlap:
                    score = float(len(overlap) * 5) + (len(normalized_trigger) / 100.0)
                    best_score = max(best_score, score)
            if best_score > 0:
                scored_variants.append((vk, best_score, matched_exact))
    if any(matched_exact for _, _, matched_exact in scored_variants):
        scored_variants = [
            (vk, score, matched_exact)
            for vk, score, matched_exact in scored_variants
            if matched_exact
        ]

    if "80005-2" in normalized:
        scored_variants = [
            (vk, score, matched_exact)
            for vk, score, matched_exact in scored_variants
            if vk != "normativa_base"
        ]
    elif "80005-1" in normalized:
        scored_variants = [
            (vk, score, matched_exact)
            for vk, score, matched_exact in scored_variants
            if vk != "monitorizacion_control"
        ]

    variants: List[str] = []
    if scored_variants:
        scored_variants.sort(key=lambda item: item[1], reverse=True)
        seen: set[str] = set()
        for vk, _score, _matched_exact in scored_variants:
            if vk in seen:
                continue
            seen.add(vk)
            variants.append(vk)

    if "ops" in expected_domains:
        generic_ops_terms = (
            "que es ops",
            "que es el ops",
            "que es un sistema ops",
            "on shore power supply",
            "shore power",
            "shore-side electricity",
            "shore side electricity",
            "cold ironing",
            "electrificacion de atraques",
            "suministro electrico a buques",
            "conexion buque puerto",
            "conexiones buque puerto",
            "atraque electrificado",
            "atraques electrificados",
            "puerto electrificado",
            "puertos electrificados",
            "normativa base",
            "normativa principal",
            "normativa aplica",
            "normativa tecnica aplica",
        )
        monitoring_terms = (
            "monitorizacion y control",
            "monitoring and control",
            "data communication",
            "interfaz de comunicacion",
            "scada",
            "control remoto",
        )
        explicit_part_2 = any(term in normalized for term in ("80005-2", "iec 80005-2", "ieee 80005-2", "iso 80005-2"))
        if not variants:
            if any(term in normalized for term in monitoring_terms):
                variants = ["monitorizacion_control", "normativa_base"]
            elif any(term in normalized for term in generic_ops_terms):
                variants = ["normativa_base", "monitorizacion_control"]
        elif (
            variants == ["monitorizacion_control"]
            and any(term in normalized for term in monitoring_terms)
            and not explicit_part_2
        ):
            variants = ["monitorizacion_control", "normativa_base"]
    return variants


def _contains_configured_term(text: str, term: str) -> bool:
    normalized_text = _normalize_text(text or "")
    normalized_term = _normalize_text(term or "")
    if not normalized_text or not normalized_term:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])", normalized_text) is not None


def _expected_domains(question: str) -> List[str]:
    normalized = _normalize_text(question or "")
    strong_domains = []
    weak_domains = []
    for name, cfg in _DOMAIN_CFG["domains"].items():
        if not cfg.get("trigger_terms") and not cfg.get("trigger_regex") and not cfg.get("reference_patterns"):
            continue
        weak_terms = {
            _normalize_text(term)
            for term in cfg.get("weak_trigger_terms", [])
            if str(term or "").strip()
        }
        matched_terms = [
            _normalize_text(term)
            for term in cfg.get("trigger_terms", [])
            if _contains_configured_term(normalized, term)
        ]
        structural_match = (
            any(re.search(pattern, normalized) for pattern in cfg.get("trigger_regex", []))
            or any(re.search(pattern, normalized) for pattern in cfg.get("reference_patterns", []))
        )
        if structural_match or any(term not in weak_terms for term in matched_terms):
            strong_domains.append(name)
        elif matched_terms:
            weak_domains.append(name)
    return strong_domains or weak_domains


def _matched_domain_query_terms(question: str, domain: str) -> List[str]:
    normalized = _normalize_text(question or "")
    cfg = _DOMAIN_CFG["domains"].get(domain, {})
    terms = [
        term for term in cfg.get("trigger_terms", [])
        if term and _contains_configured_term(normalized, term)
    ]
    for pattern in cfg.get("reference_patterns", []):
        for match in re.finditer(pattern, normalized):
            terms.append(match.group(0))
    seen = set()
    result = []
    for term in terms:
        clean = _normalize_text(term)
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def detect_hint_domains(text: str) -> List[str]:
    """Extrae dominios detectables de un texto (p.ej. historial de conversación).

    Pensado para propagar contexto de dominio de turnos anteriores a preguntas
    de seguimiento cortas que no contienen trigger_terms explícitos.
    """
    return _expected_domains(_clean_question(text)) if text and text.strip() else []


def detect_hint_document_variants(text: str, hint_domains: List[str] | None = None) -> List[str]:
    """Extrae variantes documentales del historial de conversación para preguntas de seguimiento.

    Si la conversación previa menciona 'IT 3' o '2021', las preguntas cortas
    posteriores heredan ese foco documental.
    """
    if not text or not text.strip():
        return []
    clean = _clean_question(text)
    domains = hint_domains if hint_domains else _expected_domains(clean)
    return _expected_document_variants(clean, domains)


def detect_hint_article_refs(text: str) -> List[str]:
    """Extrae referencias a articulos del historial reciente para preguntas de seguimiento."""
    if not text or not text.strip():
        return []
    return _extract_article_refs(_clean_question(text))


def detect_hint_it_section_refs(text: str) -> List[str]:
    """Extrae referencias IT del historial reciente para preguntas de seguimiento."""
    if not text or not text.strip():
        return []
    return _extract_it_section_refs(_clean_question(text))


def _variant_bridge_terms(
    clean_question: str,
    expected_domains: List[str],
    expected_document_variants: List[str],
    *,
    structural_followup_query: bool,
) -> List[str]:
    """Obtiene términos puente desde domains.json para consultas ambiguas.

    Reutiliza los query_triggers configurados de la variante documental esperada
    para enriquecer búsquedas cortas o de seguimiento, sin hardcodear preguntas
    concretas en código.
    """
    if not expected_document_variants:
        return []

    normalized_question = _normalize_text(clean_question or "")
    question_tokens = {token for token in _tokenize(normalized_question) if token and token not in STOPWORDS}
    short_or_ambiguous = structural_followup_query or len(question_tokens) <= 9
    bridge_terms: List[str] = []
    seen: set[str] = set()

    for domain_name in expected_domains:
        cfg = _DOMAIN_CFG["domains"].get(domain_name, {})
        for variant in cfg.get("document_variants", []):
            variant_key = str(variant.get("variant_key") or "")
            if variant_key not in expected_document_variants:
                continue
            triggers = [str(trigger).strip() for trigger in variant.get("query_triggers", []) if str(trigger).strip()]
            selected: List[str] = []
            for trigger in triggers:
                normalized_trigger = _normalize_text(trigger)
                trigger_tokens = {token for token in _tokenize(normalized_trigger) if token and token not in STOPWORDS}
                if question_tokens and trigger_tokens and question_tokens & trigger_tokens:
                    selected.append(trigger)
            if not selected and short_or_ambiguous:
                selected = triggers[:3]
            for term in selected[:4]:
                normalized_term = _normalize_text(term)
                if not normalized_term or normalized_term in seen:
                    continue
                seen.add(normalized_term)
                bridge_terms.append(term)

    return bridge_terms[:8]


def _query_mentions_bt40(question: str) -> bool:
    normalized = _normalize_text(question or "")
    compact = re.sub(r"[^a-z0-9]+", "", normalized)
    return any(term in normalized for term in ("bt-40", "guia bt 40", "guia-bt-40")) or "bt40" in compact


def _query_mentions_bt_generators(question: str) -> bool:
    normalized = _normalize_text(question or "")
    return (
        "instalaciones generadoras" in normalized
        or "instalacion generadora" in normalized
        or "generadoras de baja tension" in normalized
        or ("generadoras" in normalized and "baja tension" in normalized)
        or ("generador" in normalized and "baja tension" in normalized)
        or ("generador" in normalized and "conexion" in normalized and "red" in normalized)
        or any(term in normalized for term in ("aisladas", "asistidas", "interconectadas", "anti-isla", "acoplamiento a red"))
    )


def _query_mentions_rebt_regulation(question: str) -> bool:
    normalized = _normalize_text(question or "")
    return "reglamento electrotecnico" in normalized or "rebt" in normalized


def reset_documents() -> None:
    global collection, table_collection
    _query_cache.clear()
    for name in (COLLECTION_NAME, TABLE_COLLECTION_NAME):
        try:
            chroma_client.delete_collection(name)
        except Exception:
            pass
    collection = chroma_client.create_collection(
        name=COLLECTION_NAME,
        embedding_function=_embedding_fn,
        metadata={"ef_version": _EF_VERSION},
    )
    table_collection = chroma_client.create_collection(
        name=TABLE_COLLECTION_NAME,
        embedding_function=_embedding_fn,
        metadata={"ef_version": _EF_VERSION},
    )


def _get_indexed_sources() -> Dict[str, str]:
    return get_indexed_sources(collection)


def list_indexed_sources() -> Dict[str, str]:
    if RAG_BACKEND == "azure_search":
        from azure_rag_service import list_indexed_sources as list_indexed_sources_azure

        return list_indexed_sources_azure()
    return _get_indexed_sources()


def _empty_retrieval_stats(
    *,
    expected_domains: List[str] | None = None,
    index_status: str = "ready",
) -> Dict[str, object]:
    return {
        "selected_count": 0,
        "source_diversity": 0,
        "expected_domains": expected_domains or [],
        "domain_match_ratio": 0.0,
        "index_status": index_status,
        "backend": "chroma",
    }


def _delete_source_chunks(source_name: str) -> None:
    delete_source_chunks(source_name, collections=(collection, table_collection))


def _iter_add_batches(
    documents: List[str],
    metadatas: List[Dict[str, object]],
    ids: List[str],
    batch_size: int = CHROMA_ADD_BATCH_SIZE,
):
    yield from iter_add_batches(
        documents,
        metadatas,
        ids,
        batch_size=batch_size,
    )


def _collection_add_batched(
    target_collection,
    *,
    documents: List[str],
    metadatas: List[Dict[str, object]],
    ids: List[str],
    batch_size: int = CHROMA_ADD_BATCH_SIZE,
    use_embedding_cache: bool = False,
) -> None:
    add_batched_to_collection(
        target_collection,
        documents=documents,
        metadatas=metadatas,
        ids=ids,
        batch_size=batch_size,
        use_embedding_cache=use_embedding_cache,
        embedding_fn=_embedding_fn,
        embedding_cache=_embedding_cache,
        encode_passages=_encode_passages,
        cache_key_for_text=_chunk_cache_key,
    )


def _index_file(filepath: Path, root_path: Path, file_hash: str) -> int:
    source_name = str(filepath.relative_to(root_path)).replace("\\", "/")
    domain = _source_domain_key(source_name)
    document_profile = _document_profile_metadata(source_name, domain)
    documents, metadatas, ids = [], [], []

    if filepath.suffix.lower() == ".md":
        text = filepath.read_text(encoding="utf-8", errors="replace")
        for chunk_index, chunk in enumerate(_split_text(text), start=1):
            if _is_noise_chunk(chunk):
                continue
            if _looks_like_table_block(chunk):
                chunk_kind = "table"
            elif _extract_numeric_terms(chunk):
                chunk_kind = "numeric"
            else:
                chunk_kind = "text"
            chunk_profile = _chunk_profile_metadata(source_name, "", chunk, chunk_kind)
            context_prefix = f"[{chunk_profile['itc_refs']}] " if chunk_profile["itc_refs"] else ""
            indexed_chunk = f"{context_prefix}{chunk}" if context_prefix else chunk
            documents.append(indexed_chunk)
            metadatas.append({
                "source": source_name,
                "folder": str(filepath.parent).replace("\\", "/"),
                **document_profile,
                "page": 1,
                "printed_page": "",
                "chunk": chunk_index,
                **chunk_profile,
                "file_hash": file_hash,
            })
            ids.append(f"{source_name}-1-{chunk_index}")
        if documents:
            _collection_add_batched(collection, documents=documents, metadatas=metadatas, ids=ids, use_embedding_cache=True)
        return len(documents)

    pdf = fitz.open(str(filepath))
    # Heading ITC activo: persiste entre páginas para que las tablas
    # en páginas posteriores hereden la ITC de su sección.
    active_itc_heading = ""
    active_itc_section = ""
    try:
        for page_index, page in enumerate(pdf):
            raw_html = page.get_text("html")
            p_pat = re.compile(r'<p style="top:([\d.]+)pt[^"]*line-height:([\d.]+)pt[^"]*"[^>]*>(.*?)</p>', re.DOTALL)
            lines = []
            prev_top = None
            prev_lh = 10.0
            for top_s, lh_s, content in p_pat.findall(raw_html):
                top, lh = float(top_s), float(lh_s)
                txt = _html.unescape(re.sub(r"<[^>]+>", "", content)).strip()
                # Normalizar encoding: reemplazar secuencias mojibake comunes
                txt = unicodedata.normalize("NFC", txt)
                if txt:
                    if prev_top is not None and (top - prev_top) > prev_lh + 3.0:
                        lines.append("")
                    lines.append(txt)
                    prev_top = top
                    prev_lh = lh
            text = "\n".join(lines)
            # Fallback OCR: si la página apenas tiene texto extraíble (PDF
            # escaneado), pasa por Tesseract. Sin Tesseract instalado o con
            # OCR_ENABLED=0, este bloque devuelve "" y el comportamiento es
            # igual al previo (página vacía → skip).
            if len(text.strip()) < OCR_MIN_TEXT_CHARS_PER_PAGE:
                ocr_text = _ocr_page_text(page)
                if ocr_text and len(ocr_text.strip()) > len(text.strip()):
                    text = ocr_text
                    logger.info(
                        "[OCR] %s pag %d: texto recuperado por OCR (%d chars)",
                        source_name, page_index + 1, len(text),
                    )
            text = _decode_chunk_corruption(text, source_name)
            page_blocks = _extract_text_blocks(text)
            if not page_blocks:
                continue
            printed_page = _extract_printed_page(text)
            page_exact_refs = ", ".join(_extract_exact_refs(f"{source_name} {text}"))
            # Detectar headings ITC en la página (ej: "ITC-BT-25. INSTALACIONES...")
            # y actualizar el contexto activo que se propagará a tablas.
            active_itc_heading, active_itc_section = _page_structure_context(
                text,
                active_itc_heading,
                active_itc_section,
            )
            for chunk_index, (chunk, clean_section_name) in enumerate(_split_structured_blocks(page_blocks), start=1):
                if _is_noise_chunk(chunk):
                    continue
                if _looks_like_table_block(chunk):
                    chunk_kind = "table"
                elif _extract_numeric_terms(chunk):
                    chunk_kind = "numeric"
                else:
                    chunk_kind = "text"
                chunk_profile = _chunk_profile_metadata(
                    source_name, clean_section_name, chunk, chunk_kind
                )
                chunk_profile = _inherit_active_structure(
                    chunk_profile,
                    active_itc_heading,
                    active_itc_section,
                )
                # Prefijo de contexto para mejorar embedding y búsqueda léxica
                context_prefix = ""
                if chunk_profile["itc_refs"]:
                    context_prefix = f"[{chunk_profile['itc_refs']}] "
                elif clean_section_name:
                    context_prefix = f"[{clean_section_name}] "
                indexed_chunk = f"{context_prefix}{chunk}" if context_prefix else chunk
                documents.append(indexed_chunk)
                metadatas.append({
                    "source": source_name,
                    "folder": str(filepath.parent).replace("\\", "/"),
                    **document_profile,
                    "page": page_index + 1,
                    "printed_page": printed_page,
                    "chunk": chunk_index,
                    **chunk_profile,
                    "file_hash": file_hash,
                })
                ids.append(f"{source_name}-{page_index + 1}-{chunk_index}")
            # Contexto de página para enriquecer filas de tabla:
            # ITC refs y sección dominante (primer bloque con sección no vacía).
            # Si la página no tiene heading ITC propio, hereda el activo.
            page_itc_refs = _extract_itc_refs(f"{source_name} {text}")
            effective_itc = page_itc_refs or active_itc_heading
            page_section = ""
            for blk in page_blocks:
                if blk["section"]:
                    page_section = _sanitize_section_label(
                        blk["section"][:SECTION_LABEL_MAX_LENGTH]
                    )
                    break
            effective_section = page_section or active_itc_section
            table_rows = _extract_table_row_chunks(page, text)
            for row in table_rows:
                raw_row_doc = str(row["document"])
                row_index = int(row["row_index"])
                table_index = int(row["table_index"])
                table_title = str(row["table_title"])
                # Enriquecer texto de fila con contexto de página/ITC activa
                # para que el embedding capture la ITC y sección a la que pertenece.
                row_prefix = ""
                if effective_itc:
                    row_prefix = f"[{effective_itc}] "
                elif effective_section:
                    row_prefix = f"[{effective_section}] "
                if effective_section and effective_section not in raw_row_doc:
                    row_doc = f"{row_prefix}{effective_section}. {raw_row_doc}"
                elif row_prefix:
                    row_doc = f"{row_prefix}{raw_row_doc}"
                else:
                    row_doc = raw_row_doc
                row_metadata = {
                    "source": source_name,
                    "folder": str(filepath.parent).replace("\\", "/"),
                    **document_profile,
                    "page": page_index + 1,
                    "printed_page": printed_page,
                    "chunk": row_index,
                    "section": _sanitize_section_label(table_title[:SECTION_LABEL_MAX_LENGTH]),
                    "section_type": "table",
                    "article_refs": ", ".join(_extract_article_refs(f"{source_name} {page_exact_refs} {table_title} {row_doc}")),
                    "it_section_refs": ", ".join(_extract_it_section_refs(f"{source_name} {page_exact_refs} {table_title} {row_doc}")),
                    "itc_refs": _extract_itc_refs(f"{source_name} {page_exact_refs} {table_title} {row_doc}"),
                    "exact_refs": ", ".join(_extract_exact_refs(f"{source_name} {page_exact_refs} {table_title} {row_doc}")),
                    "topics": ", ".join(_extract_topic_terms(row_doc)),
                    "chunk_kind": "table_row",
                    "content_intent": "table",
                    "scope_hint": _scope_hint(row_doc, table_title),
                    "table_hint": "tabla",
                    "table_title": table_title,
                    "table_index": table_index,
                    "row_index": row_index,
                    "table_signal_count": max(_table_signal_count(row_doc), 1),
                    "file_hash": file_hash,
                }
                row_id = f"{source_name}-{page_index + 1}-t{table_index}-r{row_index}"
                documents.append(row_doc)
                metadatas.append(row_metadata)
                ids.append(row_id)
                # Las filas de tabla se indexan solo en la colección principal
                # (filtrable por chunk_kind="table_row"). Se mantiene
                # table_collection para consultas legacy hasta próxima reindex.
    finally:
        pdf.close()

    if documents:
        _collection_add_batched(
            collection,
            documents=documents,
            metadatas=metadatas,
            ids=ids,
            use_embedding_cache=True,
        )
    return len(documents)


def _sync_documents_chroma(
    folder_path: str = DOCUMENTS_PATH,
    progress_callback: Callable[[Dict[str, object]], None] | None = None,
) -> Dict[str, int]:
    global collection, table_collection
    if _embedding_fn is None:
        raise RuntimeError(
            "Embedding model no disponible. Descarga/cacha localmente "
            f"'{RERANK_MODEL}' antes de indexar."
        )
    if not os.path.isdir(folder_path):
        raise FileNotFoundError(f"No existe la carpeta de documentos: {folder_path}")

    root_path = Path(folder_path)
    current_files = discover_current_files(root_path, recursive_pdf_scan=RECURSIVE_PDF_SCAN)
    total_files = len(current_files)

    indexed = _get_indexed_sources()
    _bluegreen_old_name: str | None = None
    if indexed and all(v == "" for v in indexed.values()):
        logger.info("Índice sin file_hash detectado — iniciando reindexado blue/green")
        _bluegreen_old_name = collection.name
        _staging_name = f"{COLLECTION_NAME}_staging"
        try:
            chroma_client.delete_collection(_staging_name)
        except Exception:
            pass
        collection = chroma_client.create_collection(
            name=_staging_name,
            embedding_function=_embedding_fn,
            metadata={"ef_version": _EF_VERSION},
        )
        try:
            chroma_client.delete_collection(TABLE_COLLECTION_NAME)
        except Exception:
            pass
        table_collection = chroma_client.create_collection(
            name=TABLE_COLLECTION_NAME,
            embedding_function=_embedding_fn,
            metadata={"ef_version": _EF_VERSION},
        )
        _query_cache.clear()
        indexed = {}

    added = updated = removed = 0

    if progress_callback:
        progress_callback({
            "phase": "scan",
            "processed_files": 0,
            "total_files": total_files,
            "current_file": "",
        })

    for source_name in list(indexed):
        if source_name not in current_files:
            _delete_source_chunks(source_name)
            removed += 1
            if progress_callback:
                progress_callback({
                    "phase": "cleanup",
                    "current_file": source_name,
                    "processed_files": 0,
                    "total_files": total_files,
                })
            logger.info("Eliminado del índice: %s", source_name)

    for processed_files, (source_name, filepath) in enumerate(current_files.items(), start=1):
        if progress_callback:
            progress_callback({
                "phase": "indexing",
                "current_file": source_name,
                "processed_files": processed_files,
                "total_files": total_files,
            })
        current_hash = file_hash(str(filepath))
        if source_name in indexed:
            if indexed[source_name] == current_hash:
                # Verificar integridad: si el hash coincide pero no hay chunks,
                # reindexar (posible corrupción parcial de ChromaDB).
                probe = collection.get(
                    where={"source": source_name}, include=[], limit=1
                )
                if probe["ids"]:
                    continue
                logger.warning(
                    "Hash coincide pero sin chunks para %s — reindexando",
                    source_name,
                )
            _delete_source_chunks(source_name)
            updated += 1
            logger.info("Actualizando índice: %s", source_name)
        else:
            added += 1
            logger.info("Indexando nuevo archivo: %s", source_name)
        _index_file(filepath, root_path, current_hash)

    logger.info("Sync completado — añadidos:%d actualizados:%d eliminados:%d", added, updated, removed)

    if _bluegreen_old_name:
        staging_count = collection.count()
        if staging_count > 0:
            try:
                chroma_client.delete_collection(_bluegreen_old_name)
            except Exception as exc:
                logger.warning("No se pudo eliminar colección anterior '%s': %s", _bluegreen_old_name, exc)
            _write_active_collection_name(collection.name)
            logger.info(
                "Blue/green swap completado: '%s' → '%s' (%d chunks)",
                _bluegreen_old_name, collection.name, staging_count,
            )
        else:
            logger.error(
                "Staging vacío tras reindexado — restaurando colección anterior '%s'",
                _bluegreen_old_name,
            )
            collection = _get_or_reset_collection(chroma_client, _bluegreen_old_name, _embedding_fn)

    _save_embedding_cache()
    return {"added": added, "updated": updated, "removed": removed}


def sync_documents(
    folder_path: str = DOCUMENTS_PATH,
    progress_callback: Callable[[Dict[str, object]], None] | None = None,
) -> Dict[str, int]:
    if RAG_BACKEND == "azure_search":
        from azure_rag_service import sync_documents_from_blob
        return sync_documents_from_blob(progress_callback=progress_callback)
    return _sync_documents_chroma(folder_path, progress_callback=progress_callback)


def load_documents(folder_path: str = DOCUMENTS_PATH, reset: bool = False) -> int:
    if RAG_BACKEND == "azure_search":
        result = sync_documents(folder_path)
        return int(result.get("chunks_indexed", 0))
    if reset:
        reset_documents()
    sync_documents(folder_path)
    return collection.count()


def _search_documents_detailed_chroma(
    question: str,
    n_results: int = TOP_K_RESULTS,
    domain: str = "",
    hint_domains: List[str] | None = None,
    hint_document_variants: List[str] | None = None,
    hint_article_refs: List[str] | None = None,
    hint_it_section_refs: List[str] | None = None,
) -> Tuple[str, List[str], Dict[str, object]]:
    if not question.strip():
        return "", [], _empty_retrieval_stats(index_status="not_queried")
    if _embedding_fn is None:
        logger.error("Busqueda RAG deshabilitada: embedding model '%s' no disponible", RERANK_MODEL)
        return "", [], _empty_retrieval_stats(index_status="unavailable")
    _ensure_active_chroma_collections()

    clean_question = _clean_question(question)
    mentions_bt40 = _query_mentions_bt40(clean_question)
    mentions_bt_generators = _query_mentions_bt_generators(clean_question)
    mentions_rebt_regulation = _query_mentions_rebt_regulation(clean_question)
    question_specific_scopes = _question_mentions_specific_scope(_normalize_text(clean_question))
    expected_domains = [domain] if domain else _expected_domains(clean_question)
    if not expected_domains and hint_domains:
        expected_domains = [d for d in hint_domains if d]
        logger.debug("hint_domains aplicados como fallback: %s", expected_domains)
    if collection.count() == 0:
        try:
            sync_documents()
        except Exception:
            return "", [], _empty_retrieval_stats(expected_domains=expected_domains, index_status="sync_failed")
        if collection.count() == 0:
            return "", [], _empty_retrieval_stats(expected_domains=expected_domains, index_status="empty")

    n_results = max(n_results, 6)
    question_tokens = set(_tokenize(clean_question))
    question_keywords = {token for token in question_tokens if token not in STOPWORDS and len(token) >= 5}
    core_terms = [_normalize_text(term) for term in _extract_core_terms(question_keywords, clean_question)]
    query_profile = _build_query_profile(clean_question, question_keywords)
    normalized_question = query_profile["normalized_question"]
    target_itc_refs = {
        ref for ref in query_profile["exact_refs"]
        if re.fullmatch(r"itc-bt-\d{2}", ref)
    }
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
    if query_profile["motivation_query"]:
        for t in ("preambulo", "exposicion", "motivos", "directiva", "adaptar", "eficiencia", "seguridad"):
            if t not in core_terms:
                core_terms.append(t)
    if mentions_bt_generators:
        for t in ("generadoras", "aisladas", "asistidas", "interconectadas", "clasificacion", "condiciones"):
            if t not in core_terms:
                core_terms.append(t)
    if mentions_bt40 and "guias_tecnicas" not in expected_domains:
        expected_domains.append("guias_tecnicas")
    if target_itc_refs and "baja_tension" not in expected_domains:
        expected_domains.insert(0, "baja_tension")
    if mentions_bt_generators and "guias_tecnicas" not in expected_domains:
        for domain in ("baja_tension", "guias_tecnicas"):
            if domain not in expected_domains:
                expected_domains.append(domain)
    expected_document_variants = _expected_document_variants(clean_question, expected_domains)
    if not expected_document_variants and hint_document_variants:
        expected_document_variants = [v for v in hint_document_variants if v]
        logger.debug("hint_document_variants aplicados como fallback: %s", expected_document_variants)
    if not query_profile["article_refs"] and hint_article_refs:
        query_profile["article_refs"] = sorted({ref for ref in hint_article_refs if ref})
        logger.debug("hint_article_refs aplicados como fallback: %s", query_profile["article_refs"])
    if not query_profile["it_section_refs"] and hint_it_section_refs:
        query_profile["it_section_refs"] = sorted({ref for ref in hint_it_section_refs if ref})
        logger.debug("hint_it_section_refs aplicados como fallback: %s", query_profile["it_section_refs"])
    if expected_domains:
        auto_terms = _auto_technical_terms(clean_question)
        existing = set(query_profile["phrase_queries"])
        new_terms = [t for t in auto_terms if t not in existing]
        if new_terms:
            query_profile["phrase_queries"] = query_profile["phrase_queries"] + new_terms
            logger.debug("auto_technical_terms añadidos: %s", new_terms)
    structural_followup_query = bool(query_profile["article_refs"] or query_profile["it_section_refs"])
    variant_bridge_terms = _variant_bridge_terms(
        clean_question,
        expected_domains,
        expected_document_variants,
        structural_followup_query=structural_followup_query,
    )
    if variant_bridge_terms:
        existing_phrase_queries = {_normalize_text(term) for term in query_profile["phrase_queries"]}
        appended = []
        for term in variant_bridge_terms:
            normalized_term = _normalize_text(term)
            if normalized_term and normalized_term not in existing_phrase_queries:
                query_profile["phrase_queries"].append(term)
                existing_phrase_queries.add(normalized_term)
                appended.append(term)
        if appended:
            logger.debug("variant_bridge_terms añadidos: %s", appended)
    semantic_query = clean_question
    if structural_followup_query and (expected_domains or expected_document_variants or variant_bridge_terms):
        semantic_parts = [clean_question] + expected_domains + expected_document_variants + variant_bridge_terms
        semantic_query = " ".join(part for part in semantic_parts if part).strip()
        logger.debug("semantic_query enriquecida con foco conversacional: %s", semantic_query)
        for term in expected_domains + expected_document_variants + variant_bridge_terms:
            normalized_term = _normalize_text(term)
            if normalized_term and normalized_term not in core_terms:
                core_terms.append(normalized_term)
    broad_query = any((
        query_profile["definition_query"],
        query_profile["list_query"],
        query_profile["summary_query"],
        query_profile["table_query"],
        query_profile["comparison_query"],
        query_profile["generalization_query"],
        query_profile["motivation_query"],
    ))
    if query_profile["summary_query"] or query_profile["list_query"] or query_profile["table_query"]:
        n_results = max(n_results, 8 if not query_profile["table_query"] else 12)
    elif (
        query_profile["definition_query"]
        or query_profile["comparison_query"]
        or query_profile["generalization_query"]
        or query_profile["motivation_query"]
    ):
        n_results = max(n_results, 7)
    if mentions_bt_generators:
        n_results = max(n_results, 9)
    if query_profile["temporal_query"]:
        n_results = max(n_results, 8)
    if query_profile["exact_refs"] or query_profile["page_refs"]:
        n_results = max(n_results, 10)
    if query_profile["intent"] in {"numeric_value", "table_lookup"}:
        n_results = max(n_results, 10)
    candidate_count = _candidate_window(n_results, question_keywords, clean_question)
    if broad_query:
        candidate_count = min(candidate_count + 12, 80)
    if structural_followup_query and (expected_domains or expected_document_variants):
        candidate_count = min(candidate_count + 28, 96)
    query_embedding = _encode_query(semantic_query)
    domain_filter = {"domain": {"$in": expected_domains}} if expected_domains else None
    query_kwargs = {
        "query_embeddings": [query_embedding],
        "n_results": candidate_count,
    }
    if domain_filter:
        query_kwargs["where"] = domain_filter
    results = collection.query(**query_kwargs)
    if domain_filter and not (results.get("ids", [[]])[0] or []):
        logger.warning(
            "Busqueda acotada sin candidatos para dominios %s; ampliando al corpus",
            expected_domains,
        )
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=candidate_count,
        )
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    ids = results.get("ids", [[]])[0]
    table_collection_hits = 0
    numeric_comparison_hits = 0

    if target_itc_refs and len(target_itc_refs) <= 3:
        existing_ids = set(ids)
        for ref in sorted(target_itc_refs):
            # Búsqueda por texto (chunks con prefijo [ITC-BT-XX])
            ref_upper = ref.upper()
            for search_term in (ref_upper, ref_upper.replace("-", " ")):
                try:
                    itc_results = collection.get(
                        where_document={"$contains": search_term},
                        include=["documents", "metadatas"],
                        limit=20,
                    )
                    for doc, meta, fid in zip(
                        itc_results.get("documents", []) or [],
                        itc_results.get("metadatas", []) or [],
                        itc_results.get("ids", []) or [],
                    ):
                        if expected_domains and _source_domain_key(str(meta.get("source", "")), meta) not in expected_domains:
                            continue
                        if fid not in existing_ids:
                            documents.append(doc)
                            metadatas.append(meta)
                            ids.append(fid)
                            existing_ids.add(fid)
                except Exception as exc:
                    logger.warning("ITC-forced retrieval failed for %s: %s", search_term, exc)

    structured_search_terms = _structured_search_terms(query_profile)
    if structured_search_terms:
        existing_ids = set(ids)
        for search_term in structured_search_terms[:8]:
            try:
                structured_results = collection.get(
                    where_document={"$contains": search_term},
                    include=["documents", "metadatas"],
                    limit=12,
                )
                for doc, meta, fid in zip(
                    structured_results.get("documents", []) or [],
                    structured_results.get("metadatas", []) or [],
                    structured_results.get("ids", []) or [],
                ):
                    if expected_domains and _source_domain_key(str(meta.get("source", "")), meta) not in expected_domains:
                        continue
                    if fid not in existing_ids:
                        documents.append(doc)
                        metadatas.append(meta)
                        ids.append(fid)
                        existing_ids.add(fid)
            except Exception as exc:
                logger.warning("Structured retrieval failed for %s: %s", search_term, exc)

    table_collection_query = (
        query_profile["intent"] == "table_lookup"
        or query_profile["table_query"]
        or (query_profile["intent"] == "numeric_value" and not query_profile["comparison"])
    )
    if table_collection_query:
        existing_ids = set(ids)
        try:
            table_candidate_count = min(max(n_results * 3, 18), 50)
            table_results = table_collection.query(
                query_embeddings=[_encode_query(semantic_query)],
                n_results=table_candidate_count,
            )
            for doc, meta, fid in zip(
                table_results.get("documents", [[]])[0],
                table_results.get("metadatas", [[]])[0],
                table_results.get("ids", [[]])[0],
            ):
                if expected_domains and _source_domain_key(str(meta.get("source", "")), meta) not in expected_domains:
                    continue
                if fid not in existing_ids:
                    documents.append(doc)
                    metadatas.append(meta)
                    ids.append(fid)
                    existing_ids.add(fid)
                    table_collection_hits += 1
        except Exception as exc:
            logger.warning("Table collection retrieval failed: %s", exc)

    if query_profile["comparison"] and len(query_profile["numeric_value_groups"]) >= 2:
        existing_ids = set(ids)
        for group in query_profile["numeric_value_groups"]:
            for term in group:
                comparison_collections = (collection, table_collection) if query_profile["table_query"] else (collection,)
                for col in comparison_collections:
                    try:
                        value_results = col.get(
                            where_document={"$contains": term},
                            include=["documents", "metadatas"],
                            limit=16,
                        )
                        for doc, meta, fid in zip(
                            value_results.get("documents", []) or [],
                            value_results.get("metadatas", []) or [],
                            value_results.get("ids", []) or [],
                        ):
                            if expected_domains and _source_domain_key(str(meta.get("source", "")), meta) not in expected_domains:
                                continue
                            if fid not in existing_ids:
                                documents.append(doc)
                                metadatas.append(meta)
                                ids.append(fid)
                                existing_ids.add(fid)
                                numeric_comparison_hits += 1
                                if col is table_collection:
                                    table_collection_hits += 1
                    except Exception as exc:
                        logger.warning("Numeric comparison retrieval failed for %s: %s", term, exc)

    if query_profile["page_refs"]:
        existing_ids = set(ids)
        for page_ref in query_profile["page_refs"]:
            for where in ({"page": page_ref}, {"printed_page": str(page_ref)}):
                try:
                    page_results = collection.get(
                        where=where,
                        include=["documents", "metadatas"],
                        limit=20,
                    )
                    for doc, meta, fid in zip(
                        page_results.get("documents", []) or [],
                        page_results.get("metadatas", []) or [],
                        page_results.get("ids", []) or [],
                    ):
                        if expected_domains and _source_domain_key(str(meta.get("source", "")), meta) not in expected_domains:
                            continue
                        if fid not in existing_ids:
                            documents.append(doc)
                            metadatas.append(meta)
                            ids.append(fid)
                            existing_ids.add(fid)
                except Exception as exc:
                    logger.warning("Page-forced retrieval failed for %s/%s: %s", page_ref, where, exc)

    if query_profile["numeric_variants"] or query_profile["phrase_queries"]:
        existing_ids = set(ids)
        for term in list(query_profile["numeric_variants"]) + list(query_profile["phrase_queries"]):
            for col in (collection, table_collection):
                try:
                    forced_results = col.get(
                        where_document={"$contains": term},
                        include=["documents", "metadatas"],
                        limit=18,
                    )
                    for doc, meta, fid in zip(
                        forced_results.get("documents", []) or [],
                        forced_results.get("metadatas", []) or [],
                        forced_results.get("ids", []) or [],
                    ):
                        if expected_domains and _source_domain_key(str(meta.get("source", "")), meta) not in expected_domains:
                            continue
                        if fid not in existing_ids:
                            documents.append(doc)
                            metadatas.append(meta)
                            ids.append(fid)
                            existing_ids.add(fid)
                            if col is table_collection:
                                table_collection_hits += 1
                except Exception as exc:
                    logger.warning("Value/phrase-forced retrieval failed for %s: %s", term, exc)

    if query_profile["exact_refs"]:
        existing_ids = set(ids)
        for ref in query_profile["exact_refs"]:
            for term in (ref, ref.replace("-", " "), ref.upper()):
                for col in (collection, table_collection):
                    try:
                        ref_results = col.get(
                            where_document={"$contains": term},
                            include=["documents", "metadatas"],
                            limit=18,
                        )
                        for doc, meta, fid in zip(
                            ref_results.get("documents", []) or [],
                            ref_results.get("metadatas", []) or [],
                            ref_results.get("ids", []) or [],
                        ):
                            if expected_domains and _source_domain_key(str(meta.get("source", "")), meta) not in expected_domains:
                                continue
                            if fid not in existing_ids:
                                documents.append(doc)
                                metadatas.append(meta)
                                ids.append(fid)
                                existing_ids.add(fid)
                                if col is table_collection:
                                    table_collection_hits += 1
                    except Exception as exc:
                        logger.warning("Reference-forced retrieval failed for %s: %s", term, exc)

    if expected_domains:
        existing_ids = set(ids)
        for expected_domain in expected_domains:
            try:
                forced_n = min(n_results + 6, 14)
                domain_results = collection.query(
                    query_embeddings=[_encode_query(semantic_query)],
                    n_results=forced_n,
                    where={"domain": expected_domain},
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
                logger.warning("Domain-forced retrieval failed for %s: %s", expected_domain, exc)

            for term in _matched_domain_query_terms(clean_question, expected_domain)[:4]:
                for search_term in (term, term.upper()):
                    try:
                        lexical_domain_results = collection.get(
                            where={"domain": expected_domain},
                            where_document={"$contains": search_term},
                            include=["documents", "metadatas"],
                            limit=min(n_results + 4, 12),
                        )
                        for doc, meta, fid in zip(
                            lexical_domain_results.get("documents", []) or [],
                            lexical_domain_results.get("metadatas", []) or [],
                            lexical_domain_results.get("ids", []) or [],
                        ):
                            if fid not in existing_ids:
                                documents.append(doc)
                                metadatas.append(meta)
                                ids.append(fid)
                                existing_ids.add(fid)
                    except Exception as exc:
                        logger.warning(
                            "Domain lexical retrieval failed for %s/%s: %s",
                            expected_domain,
                            search_term,
                            exc,
                        )

    if query_profile["table_query"]:
        existing_ids = set(ids)
        for table_kind, col in (("table_row", table_collection), ("table_row", collection), ("table", collection)):
            try:
                table_query_args = {
                    "query_embeddings": [_encode_query(semantic_query)],
                    "n_results": min(n_results + 6, 16),
                }
                if col is collection:
                    table_query_args["where"] = {"chunk_kind": table_kind}
                table_results = col.query(**table_query_args)
                for doc, meta, fid in zip(
                    table_results.get("documents", [[]])[0],
                    table_results.get("metadatas", [[]])[0],
                    table_results.get("ids", [[]])[0],
                ):
                    if str(meta.get("chunk_kind", "")) != table_kind:
                        continue
                    if expected_domains and _source_domain_key(str(meta.get("source", "")), meta) not in expected_domains:
                        continue
                    if fid not in existing_ids:
                        documents.append(doc)
                        metadatas.append(meta)
                        ids.append(fid)
                        existing_ids.add(fid)
                        if col is table_collection:
                            table_collection_hits += 1
            except Exception as exc:
                logger.warning("Table-forced retrieval failed for %s: %s", table_kind, exc)

    if mentions_rebt_regulation and query_profile["scope_query"]:
        existing_ids = set(ids)
        for term in ("El presente Reglamento tiene por objeto", "prevenir las perturbaciones"):
            try:
                forced_results = collection.get(
                    where_document={"$contains": term},
                    where={"domain": "baja_tension"},
                    include=["documents", "metadatas"],
                    limit=6,
                )
                for doc, meta, fid in zip(
                    forced_results.get("documents", []) or [],
                    forced_results.get("metadatas", []) or [],
                    forced_results.get("ids", []) or [],
                ):
                    if fid not in existing_ids:
                        documents.append(doc)
                        metadatas.append(meta)
                        ids.append(fid)
                        existing_ids.add(fid)
            except Exception as exc:
                logger.warning("REBT objective forced retrieval failed for %s: %s", term, exc)

    if mentions_bt40:
        existing_ids = set(ids)
        for term in ("guia-bt-40", "clasificacion", "clasificación", "instalaciones generadoras"):
            try:
                forced_results = collection.get(
                    where_document={"$contains": term},
                    where={"domain": "guias_tecnicas"},
                    include=["documents", "metadatas"],
                    limit=10,
                )
                for doc, meta, fid in zip(
                    forced_results.get("documents", []) or [],
                    forced_results.get("metadatas", []) or [],
                    forced_results.get("ids", []) or [],
                ):
                    if fid not in existing_ids:
                        documents.append(doc)
                        metadatas.append(meta)
                        ids.append(fid)
                        existing_ids.add(fid)
            except Exception as exc:
                logger.warning("BT-40 forced retrieval failed for %s: %s", term, exc)

    if mentions_bt_generators:
        existing_ids = set(ids)
        generator_terms = (
            "instalaciones generadoras",
            "instalaciones generadoras aisladas",
            "instalaciones generadoras asistidas",
            "instalaciones generadoras interconectadas",
            "clasificacion",
            "clasificación",
        )
        for domain in ("baja_tension", "guias_tecnicas"):
            for term in generator_terms:
                try:
                    forced_results = collection.get(
                        where_document={"$contains": term},
                        where={"domain": domain},
                        include=["documents", "metadatas"],
                        limit=12,
                    )
                    for doc, meta, fid in zip(
                        forced_results.get("documents", []) or [],
                        forced_results.get("metadatas", []) or [],
                        forced_results.get("ids", []) or [],
                    ):
                        if fid not in existing_ids:
                            documents.append(doc)
                            metadatas.append(meta)
                            ids.append(fid)
                            existing_ids.add(fid)
                except Exception as exc:
                    logger.warning("BT generator forced retrieval failed for %s/%s: %s", domain, term, exc)

    ranked_items = []
    seen_ids = set()

    for doc_id, document, metadata in zip(ids, documents, metadatas):
        if not document or not metadata:
            continue

        doc_norm = _normalize_text(document)
        doc_compact = re.sub(r"\s+", "", doc_norm)
        doc_tokens = set(_tokenize(document))
        metadata_norm = _metadata_text(metadata)
        section_title = _normalize_text(str(metadata.get("section", "")))
        source_title = _normalize_text(str(metadata.get("source", "")).replace("/", " "))
        source_domain = _source_domain_key(str(metadata.get("source", "")), metadata)
        inferred_itcs = _inferred_itc_refs(metadata, document)
        document_labeled_terms = _extract_labeled_terms(f"{metadata.get('section', '')} {document}")
        metadata_page = int(metadata.get("page", 0) or 0)
        printed_page = str(metadata.get("printed_page", "") or "")
        overlap_score = len(question_tokens.intersection(doc_tokens))
        keyword_hits = sum(1 for kw in question_keywords if _normalize_text(kw) in doc_norm)
        core_hits = sum(1 for core in core_terms if core in doc_norm)
        numeric_hits = sum(1 for term in query_profile["numeric_terms"] if term in doc_norm)
        numeric_variant_hits = sum(
            1 for term in query_profile["numeric_variants"]
            if _normalize_text(term) in doc_norm or re.sub(r"\s+", "", _normalize_text(term)) in doc_compact
        )
        standalone_number_hits = _standalone_number_hits(query_profile["standalone_numbers"], document)
        numeric_group_hits = 0
        if query_profile["comparison"] and len(query_profile["numeric_value_groups"]) >= 2:
            numeric_group_hits = sum(
                1 for group in query_profile["numeric_value_groups"]
                if _matches_numeric_group(group, document)
            )
        reference_hits = sum(1 for term in query_profile["reference_terms"] if term in doc_norm or term in metadata_norm)
        section_hits = sum(1 for term in query_profile["section_terms"] if term in metadata_norm)
        section_title_hits = sum(1 for term in query_profile["section_terms"] if term in section_title)
        source_title_hits = sum(1 for term in query_profile["section_terms"] if term in source_title)
        definition_hits = len(DEFINITION_CUE_PATTERN.findall(document)) if query_profile["definition_query"] else 0
        motivation_hits = len(MOTIVATION_CUE_PATTERN.findall(document)) if query_profile["motivation_query"] else 0
        list_hits = 1 if query_profile["list_query"] and LIST_CUE_PATTERN.search(document) else 0
        summary_hits = 1 if query_profile["summary_query"] and (LIST_CUE_PATTERN.search(document) or metadata.get("section")) else 0
        table_hits = 0
        if query_profile["table_query"]:
            table_hits = sum((
                1 if "tabla" in doc_norm or "tabla" in metadata_norm or "tabla" in section_title else 0,
                1 if LIST_CUE_PATTERN.search(document) else 0,
                1 if CIRCUIT_LIST_CUE_PATTERN.search(document) else 0,
            ))
            if str(metadata.get("chunk_kind", "")) == "table":
                table_hits += 2
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
        if query_profile["intent"] == "document_location" and query_profile["location_target"]:
            location_target = query_profile["location_target"]
            if location_target in doc_norm:
                score += 18
            if location_target in section_title:
                score += 22
            if re.search(rf"\b\d+(?:\.\d+)*\s+{re.escape(location_target)}\b", doc_norm):
                score += 28
        if query_profile["page_refs"]:
            if metadata_page in query_profile["page_refs"] or printed_page in {str(p) for p in query_profile["page_refs"]}:
                score += 50
        if query_profile["exact_refs"]:
            exact_hits = sum(1 for ref in query_profile["exact_refs"] if ref in doc_norm or ref in metadata_norm)
            if exact_hits:
                score += exact_hits * 28
        score += _structural_anchor_score(
            metadata,
            document,
            exact_refs=query_profile["exact_refs"],
            article_refs=query_profile["article_refs"],
            it_section_refs=query_profile["it_section_refs"],
        )
        if query_profile["intent"] in {"numeric_value", "table_lookup"}:
            if str(metadata.get("chunk_kind", "")) == "table_row":
                score += 30
            elif str(metadata.get("chunk_kind", "")) == "table":
                score += 14
        if query_profile["intent"] == "numeric_value" and (numeric_hits or numeric_variant_hits):
            score += (numeric_hits + numeric_variant_hits) * 8
        if standalone_number_hits:
            score += standalone_number_hits * 18
        if query_profile["comparison"] and len(query_profile["numeric_value_groups"]) >= 2:
            if numeric_group_hits:
                context_overlap = sum(1 for kw in question_keywords if _normalize_text(kw) in doc_norm)
                score += (numeric_group_hits * 22) + (context_overlap * 4)
            else:
                score -= 18
            if str(metadata.get("chunk_kind", "")) in {"table", "table_row"} and not query_profile["table_query"]:
                score -= 35
        if query_profile["phrase_queries"]:
            phrase_hits = sum(1 for phrase in query_profile["phrase_queries"] if _normalize_text(phrase) in doc_norm)
            score += phrase_hits * 80
            technical_hits = sum(
                1 for phrase in query_profile.get("technical_equivalent_phrases", [])
                if _normalize_text(phrase) in doc_norm or _normalize_text(phrase) in metadata_norm
            )
            if technical_hits >= 2:
                score += technical_hits * TECHNICAL_EQUIVALENT_BOOST
            if technical_hits and any(ref in metadata_norm or ref in doc_norm for ref in ("itc-bt-52", "itc-bt-18", "itc-bt-24", "itc-bt-08")):
                score += TECHNICAL_EQUIVALENT_BOOST
        if query_profile.get("normative_intent_query"):
            normative_text = f"{metadata.get('content_intent', '')} {metadata.get('section', '')} {document}"
            application_hits = _normative_application_hit_count(normative_text)
            classification_hits = _normative_classification_hit_count(normative_text)
            if application_hits:
                score += application_hits * NORMATIVE_INTENT_BOOST
            if classification_hits:
                score += classification_hits * (NORMATIVE_INTENT_BOOST - 4)
        if target_itc_refs:
            if query_profile["comparison"] and len(query_profile["numeric_value_groups"]) >= 2:
                if (inferred_itcs & target_itc_refs) and numeric_group_hits:
                    score += 18 + (6 * len(inferred_itcs & target_itc_refs))
            elif inferred_itcs & target_itc_refs:
                score += 70 + (10 * len(inferred_itcs & target_itc_refs))
            elif source_domain in {"baja_tension", "guias_tecnicas"}:
                score -= 8
        # Scope/objeto/finalidad questions benefit from early normative sections.
        if query_profile.get("scope_query"):
            if int(metadata.get("page", 9999) or 9999) <= 3:
                score += 5
            if any(term in section_title for term in ("objeto", "ambito", "alcance", "finalidad", "definicion")):
                score += 7
            if mentions_rebt_regulation:
                if "el presente reglamento tiene por objeto" in doc_norm:
                    score += 80
                if "la presente instruccion tiene por objeto" in doc_norm:
                    score -= 35
                if "articulo 1" in section_title and "objeto" in section_title:
                    score += 14
        if query_profile.get("motivation_query"):
            if int(metadata.get("page", 9999) or 9999) <= 5:
                score += 6
            if motivation_hits:
                score += motivation_hits * 7
            if any(term in section_title for term in ("preambulo", "exposicion", "motivos", "objeto")):
                score += 12
        if "documentacion" in normalized_question or "documentacion" in core_terms:
            documentation_hits = sum(
                1 for term in (
                    "documentacion de las instalaciones",
                    "documentacion tecnica",
                    "certificado de instalacion",
                    "memoria tecnica",
                    "proyecto",
                    "puesta en servicio",
                    "empresa instaladora",
                )
                if term in doc_norm or term in section_title or term in metadata_norm
            )
            score += documentation_hits * 9
            if "catalogo" in doc_norm or "normas une" in doc_norm:
                score -= 10
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
        if mentions_bt_generators:
            if source_domain in {"baja_tension", "guias_tecnicas"}:
                score += GENERATORS_DOMAIN_BOOST
            if "itc-bt-40" in metadata_norm or "bt-40" in source_title or "bt40" in source_title:
                score += GENERATORS_ITC_BOOST
            generator_hits = sum(
                1 for term in (
                    "instalaciones generadoras aisladas",
                    "instalaciones generadoras asistidas",
                    "instalaciones generadoras interconectadas",
                    "aisladas",
                    "asistidas",
                    "interconectadas",
                    "clasificacion",
                    "condiciones generales",
                )
                if term in doc_norm or term in section_title or term in metadata_norm
            )
            score += generator_hits * GENERATORS_TERM_BOOST
        if mentions_bt40:
            if source_domain == "guias_tecnicas":
                score += BT40_DOMAIN_BOOST
                if any(term in section_title for term in ("clasificacion", "objeto", "campo de aplicacion")):
                    score += BT40_SECTION_BOOST
                if any(term in doc_norm for term in ("instalaciones generadoras aisladas", "instalaciones generadoras asistidas", "instalaciones generadoras interconectadas")):
                    score += BT40_DOC_TERM_BOOST
            else:
                score -= BT40_MISMATCH_PENALTY
        if expected_domains:
            if source_domain in expected_domains:
                score += 12
            else:
                score -= 60 if query_profile["article_refs"] else 30
                score += _domain_exclusion_penalty(expected_domains, source_domain, clean_question)
        chunk_layer = str(metadata.get("document_layer", "") or "")
        score += _document_layer_boost(
            chunk_layer,
            normative_intent=bool(query_profile.get("normative_intent_query")),
            procedure_intent=bool(query_profile.get("procedure_query")),
        )
        if expected_document_variants:
            source_document_variant = _document_variant_from_source(str(metadata.get("source", "")))
            if source_document_variant and source_document_variant in expected_document_variants:
                score += DOCUMENT_VARIANT_BOOST
                score += max(
                    0,
                    (len(expected_document_variants) - expected_document_variants.index(source_document_variant))
                    * DOCUMENT_VARIANT_ORDER_BOOST,
                )
            elif source_document_variant and source_document_variant not in expected_document_variants:
                score -= DOCUMENT_VARIANT_MISMATCH_PENALTY
        score += _source_mention_score(question_tokens, str(metadata.get("source", "")))
        if query_profile["it_section_refs"]:
            it_ref_hits = sum(
                1 for ref in query_profile["it_section_refs"]
                if _reference_matches_text(section_title, ref) or _reference_matches_text(doc_norm, ref)
            )
            if it_ref_hits:
                score += it_ref_hits * IT_SECTION_BOOST
        if query_profile["article_refs"]:
            article_ref_hits = sum(
                1 for ref in query_profile["article_refs"]
                if _reference_matches_text(section_title, ref)
                or _reference_matches_text(doc_norm, ref)
                or _reference_matches_text(metadata_norm, ref)
            )
            if article_ref_hits:
                score += article_ref_hits * ARTICLE_REF_BOOST
            if _looks_like_toc_chunk(document, metadata):
                score -= 90
        if query_profile["comparison"] and len(doc_tokens.intersection(question_tokens)) >= 2:
            score += COMPARISON_PRIORITY_BOOST
        if core_terms and core_hits == 0:
            score -= CORE_TERM_PENALTY
        if overlap_score == 0 and core_hits == 0 and metadata.get("chunk_kind") not in {"numeric", "table"}:
            score -= 6
        chunk_scope = _normalize_text(str(metadata.get("scope_hint", "")))
        if chunk_scope and not question_specific_scopes:
            for specific_term in SPECIFIC_SCOPE_TERMS:
                if _normalize_text(specific_term) in chunk_scope:
                    score -= SCOPE_PENALTY_SPECIFIC
                    break

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "[SCORE] %s p.%s s=%d | overlap=%d kw=%d core=%d num=%d numvar=%d standalone=%d ref=%d sec=%d phrase=%d",
                metadata.get("source", "?")[-40:], metadata.get("page", "?"),
                score, overlap_score, keyword_hits, core_hits, numeric_hits,
                numeric_variant_hits, standalone_number_hits, reference_hits,
                section_hits,
                sum(1 for phrase in query_profile["phrase_queries"] if _normalize_text(phrase) in doc_norm) if query_profile["phrase_queries"] else 0,
            )
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

    lexical_results = []
    if core_terms:
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
            if expected_domains:
                lex_source_domain = _source_domain_key(str(metadata.get("source", "")), metadata)
                if lex_source_domain in expected_domains:
                    lexical_score += 12
                else:
                    lexical_score -= 60 if query_profile["article_refs"] else 30
                    lexical_score += _domain_exclusion_penalty(expected_domains, lex_source_domain, clean_question)
            lex_chunk_layer = str(metadata.get("document_layer", "") or "")
            lexical_score += _document_layer_boost(
                lex_chunk_layer,
                normative_intent=bool(query_profile.get("normative_intent_query")),
                procedure_intent=bool(query_profile.get("procedure_query")),
            )
            inferred_itcs = _inferred_itc_refs(metadata, document)
            try:
                metadata_page = int(metadata.get("page", 0) or 0)
            except Exception:
                metadata_page = 0
            printed_page = str(metadata.get("printed_page", "") or "")
            if query_profile["intent"] == "document_location" and query_profile["location_target"]:
                location_target = query_profile["location_target"]
                if location_target in doc_norm:
                    lexical_score += 16
                if location_target in section_title:
                    lexical_score += 20
                if re.search(rf"\b\d+(?:\.\d+)*\s+{re.escape(location_target)}\b", doc_norm):
                    lexical_score += 24
            if query_profile["page_refs"] and (
                metadata_page in query_profile["page_refs"] or printed_page in {str(p) for p in query_profile["page_refs"]}
            ):
                lexical_score += 45
            if query_profile["exact_refs"]:
                exact_hits = sum(1 for ref in query_profile["exact_refs"] if ref in doc_norm or ref in metadata_norm)
                lexical_score += exact_hits * 24
            lexical_score += _structural_anchor_score(
                metadata,
                document,
                exact_refs=query_profile["exact_refs"],
                article_refs=query_profile["article_refs"],
                it_section_refs=query_profile["it_section_refs"],
            )
            if query_profile["intent"] in {"numeric_value", "table_lookup"} and str(metadata.get("chunk_kind", "")) == "table_row":
                lexical_score += 25
            if query_profile["numeric_variants"]:
                doc_compact = re.sub(r"\s+", "", doc_norm)
                lexical_score += sum(
                    1 for term in query_profile["numeric_variants"]
                    if _normalize_text(term) in doc_norm or re.sub(r"\s+", "", _normalize_text(term)) in doc_compact
                ) * 8
            lexical_score += _standalone_number_hits(query_profile["standalone_numbers"], document) * 16
            if query_profile["comparison"] and len(query_profile["numeric_value_groups"]) >= 2:
                numeric_group_hits = sum(
                    1 for group in query_profile["numeric_value_groups"]
                    if _matches_numeric_group(group, document)
                )
                if numeric_group_hits:
                    context_overlap = sum(1 for kw in question_keywords if _normalize_text(kw) in doc_norm)
                    lexical_score += (numeric_group_hits * 18) + (context_overlap * 4)
                else:
                    lexical_score -= 12
                if str(metadata.get("chunk_kind", "")) in {"table", "table_row"} and not query_profile["table_query"]:
                    lexical_score -= 30
            if query_profile["phrase_queries"]:
                lexical_score += sum(1 for phrase in query_profile["phrase_queries"] if _normalize_text(phrase) in doc_norm) * 70
                technical_hits = sum(
                    1 for phrase in query_profile.get("technical_equivalent_phrases", [])
                    if _normalize_text(phrase) in doc_norm or _normalize_text(phrase) in metadata_norm
                )
                if technical_hits >= 2:
                    lexical_score += technical_hits * TECHNICAL_EQUIVALENT_BOOST
                if technical_hits and any(ref in metadata_norm or ref in doc_norm for ref in ("itc-bt-52", "itc-bt-18", "itc-bt-24", "itc-bt-08")):
                    lexical_score += TECHNICAL_EQUIVALENT_BOOST
            if query_profile.get("normative_intent_query"):
                normative_text = f"{metadata.get('content_intent', '')} {metadata.get('section', '')} {document}"
                application_hits = _normative_application_hit_count(normative_text)
                classification_hits = _normative_classification_hit_count(normative_text)
                if application_hits:
                    lexical_score += application_hits * NORMATIVE_INTENT_BOOST
                if classification_hits:
                    lexical_score += classification_hits * (NORMATIVE_INTENT_BOOST - 4)
            if target_itc_refs:
                if inferred_itcs & target_itc_refs:
                    lexical_score += 55 + (8 * len(inferred_itcs & target_itc_refs))
                elif _source_domain_key(str(metadata.get("source", "")), metadata) in {"baja_tension", "guias_tecnicas"}:
                    lexical_score -= 6
            if core_terms and not any(core in doc_norm for core in core_terms):
                lexical_score -= CORE_TERM_PENALTY
            if query_profile["reference_terms"] and any(ref in doc_norm for ref in query_profile["reference_terms"]):
                lexical_score += REFERENCE_PRIORITY_BOOST
            if query_profile["definition_query"] and DEFINITION_CUE_PATTERN.search(document):
                lexical_score += DEFINITION_PRIORITY_BOOST
            if query_profile["motivation_query"] and MOTIVATION_CUE_PATTERN.search(document):
                lexical_score += 12
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
                if str(metadata.get("chunk_kind", "")) == "table":
                    lexical_score += TABLE_PRIORITY_BOOST * 2
            if query_profile["comparison_query"] and COMPARISON_CUE_PATTERN.search(document):
                lexical_score += COMPARISON_PRIORITY_BOOST_INTENT
            if query_profile["procedure_query"] and PROCEDURE_CUE_PATTERN.search(document):
                lexical_score += PROCEDURE_PRIORITY_BOOST
            if query_profile["temporal_query"] and TEMPORAL_CUE_PATTERN.search(document):
                lexical_score += TEMPORAL_PRIORITY_BOOST
            if mentions_bt_generators:
                if _source_domain_key(str(metadata.get("source", "")), metadata) in {"baja_tension", "guias_tecnicas"}:
                    lexical_score += GENERATORS_LEXICAL_DOMAIN
                if any(term in doc_norm or term in metadata_norm or term in section_title for term in ("aisladas", "asistidas", "interconectadas", "itc-bt-40", "bt-40")):
                    lexical_score += GENERATORS_LEXICAL_TERM
            if expected_document_variants:
                source_document_variant = _document_variant_from_source(str(metadata.get("source", "")))
                if source_document_variant and source_document_variant in expected_document_variants:
                    lexical_score += DOCUMENT_VARIANT_BOOST
                    lexical_score += max(
                        0,
                        (len(expected_document_variants) - expected_document_variants.index(source_document_variant))
                        * DOCUMENT_VARIANT_ORDER_BOOST,
                    )
                elif source_document_variant and source_document_variant not in expected_document_variants:
                    lexical_score -= DOCUMENT_VARIANT_MISMATCH_PENALTY
            lexical_score += _source_mention_score(question_tokens, str(metadata.get("source", "")))
            if query_profile["it_section_refs"]:
                it_ref_hits_lex = sum(
                    1 for ref in query_profile["it_section_refs"]
                    if _reference_matches_text(section_title, ref) or _reference_matches_text(doc_norm, ref)
                )
                if it_ref_hits_lex:
                    lexical_score += it_ref_hits_lex * IT_SECTION_BOOST
            if query_profile["article_refs"]:
                article_ref_hits_lex = sum(
                    1 for ref in query_profile["article_refs"]
                    if _reference_matches_text(section_title, ref)
                    or _reference_matches_text(doc_norm, ref)
                    or _reference_matches_text(metadata_norm, ref)
                )
                if article_ref_hits_lex:
                    lexical_score += article_ref_hits_lex * ARTICLE_REF_BOOST
                if _looks_like_toc_chunk(document, metadata):
                    lexical_score -= 90
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
            chunk_scope = _normalize_text(str(metadata.get("scope_hint", "")))
            if chunk_scope and not question_specific_scopes:
                for specific_term in SPECIFIC_SCOPE_TERMS:
                    if _normalize_text(specific_term) in chunk_scope:
                        lexical_score -= SCOPE_PENALTY_SPECIFIC
                        break
            ranked_items.append((lexical_score, doc_id, document, metadata))
            seen_ids.add(doc_id)

    if not ranked_items:
        return "", [], {
            "candidate_count": candidate_count,
            "selected_count": 0,
            "source_diversity": 0,
            "expected_domains": expected_domains,
            "domain_match_ratio": 0.0,
            "index_status": "ready",
            "backend": "chroma",
        }

    # --- Reranking: embedding similarity + BM25 léxico complementario ---
    avg_doc_len = sum(len(item[2].split()) for item in ranked_items) / max(len(ranked_items), 1)
    query_tokens_for_bm25 = question_keywords or question_tokens

    if rerank_model:
        query_embedding = rerank_model.encode(clean_question, convert_to_tensor=True)
        candidate_texts = [item[2] for item in ranked_items]
        candidate_embeddings = rerank_model.encode(candidate_texts, convert_to_tensor=True)
        similarities = cos_sim(query_embedding, candidate_embeddings)[0].tolist()

        reranked = []
        for item, sem_score in zip(ranked_items, similarities):
            base_score, doc_id, document, metadata = item
            bm25 = _bm25_score(query_tokens_for_bm25, document, avg_doc_len)
            final_score = base_score + (float(sem_score) * RERANK_WEIGHT) + (bm25 * RERANK_BM25_WEIGHT)
            reranked.append((final_score, doc_id, document, metadata))
        ranked_items = reranked
    else:
        reranked = []
        for item in ranked_items:
            base_score, doc_id, document, metadata = item
            bm25 = _bm25_score(query_tokens_for_bm25, document, avg_doc_len)
            final_score = base_score + (bm25 * RERANK_BM25_WEIGHT)
            reranked.append((final_score, doc_id, document, metadata))
        ranked_items = reranked

    ranked_items.sort(key=lambda item: item[0], reverse=True)

    # --- Umbral mínimo de relevancia ---
    if MIN_CHUNK_SCORE > 0:
        above_threshold = [item for item in ranked_items if item[0] >= MIN_CHUNK_SCORE]
        if above_threshold:
            ranked_items = above_threshold

    # --- Filtro duro de dominio cruzado ---
    if expected_domains:
        filtered = []
        for item in ranked_items:
            source_domain = _source_domain_key(str(item[3].get("source", "")), item[3])
            if source_domain in expected_domains:
                filtered.append(item)
            elif item[0] >= CROSS_DOMAIN_MIN_SCORE:
                filtered.append(item)
        if filtered:
            ranked_items = filtered

    if expected_domains:
        preferred_items = [
            item for item in ranked_items
            if _source_domain_key(str(item[3].get("source", "")), item[3]) in expected_domains
        ]
        other_items = [
            item for item in ranked_items
            if _source_domain_key(str(item[3].get("source", "")), item[3]) not in expected_domains
        ]
        if preferred_items:
            ranked_items = preferred_items + other_items

    if query_profile["phrase_queries"]:
        def _phrase_hit_count(item: Tuple[float, str, str, Dict[str, object]]) -> int:
            text = _normalize_text(f"{item[3].get('section', '')} {item[2]}")
            return sum(
                1 for phrase in query_profile["phrase_queries"]
                if _normalize_text(phrase) in text
            )

        ranked_items.sort(key=lambda item: (_phrase_hit_count(item), item[0]), reverse=True)

    def _matches_variant_focus(item: Tuple[float, str, str, Dict[str, object]]) -> bool:
        if not expected_document_variants:
            return False
        source_document_variant = _document_variant_from_source(str(item[3].get("source", "")))
        return bool(source_document_variant and source_document_variant in expected_document_variants)

    def _matches_item_structural_focus(item: Tuple[float, str, str, Dict[str, object]]) -> bool:
        return _matches_structural_focus(
            item[3],
            item[2],
            exact_refs=query_profile["exact_refs"],
            article_refs=query_profile["article_refs"],
            it_section_refs=query_profile["it_section_refs"],
        )

    selected = []
    selected_ids = set()
    source_counts = {}
    section_counts = {}
    source_cap = MAX_CHUNKS_PER_SOURCE + (2 if broad_query else 0)
    section_cap = 3 if broad_query else 2
    if expected_domains:
        preferred_source_count = len({
            item[3].get("source", "unknown")
            for item in ranked_items
            if _source_domain_key(str(item[3].get("source", "")), item[3]) in expected_domains
        })
        if preferred_source_count <= max(len(expected_domains), 1):
            source_cap = max(source_cap, n_results)
    if query_profile["table_query"]:
        source_cap += 3
        section_cap += 2
        if expected_domains and len(expected_domains) == 1:
            source_cap = max(source_cap, n_results + 4)
    if mentions_bt_generators:
        source_cap += 3
        section_cap += 2
    diversity_target = 0
    if query_profile["comparison_query"] or query_profile["summary_query"] or query_profile["list_query"]:
        diversity_pool = ranked_items
        if expected_domains:
            diversity_pool = [
                item for item in ranked_items
                if _source_domain_key(str(item[3].get("source", "")), item[3]) in expected_domains
            ] or ranked_items
        diversity_target = min(3, len({item[3].get("source", "unknown") for item in diversity_pool}))
    selection_items = ranked_items
    if expected_domains:
        preferred_selection_items = [
            item for item in ranked_items
            if _source_domain_key(str(item[3].get("source", "")), item[3]) in expected_domains
        ]
        if preferred_selection_items:
            selection_items = preferred_selection_items
    if expected_document_variants:
        variant_selection_items = [item for item in selection_items if _matches_variant_focus(item)]
        if variant_selection_items:
            selection_items = variant_selection_items
    if query_profile["exact_refs"] or query_profile["article_refs"] or query_profile["it_section_refs"]:
        structural_selection_items = [item for item in selection_items if _matches_item_structural_focus(item)]
        if structural_selection_items:
            selection_items = structural_selection_items
            selection_items.sort(
                key=lambda item: (
                    _clean_structural_chunk_score(
                        item[3],
                        item[2],
                        exact_refs=query_profile["exact_refs"],
                        article_refs=query_profile["article_refs"],
                        it_section_refs=query_profile["it_section_refs"],
                    ),
                    item[0],
                ),
                reverse=True,
            )
    layer_counts: Dict[str, int] = {}
    max_per_layer = max(n_results - 1, 3)
    for item in selection_items:
        _, doc_id, _, metadata = item
        source_name = metadata.get("source", "unknown")
        section_name = metadata.get("section", "") or "__none__"
        if diversity_target and len(source_counts) < diversity_target and source_name in source_counts:
            continue
        if source_counts.get(source_name, 0) >= source_cap:
            continue
        if section_counts.get((source_name, section_name), 0) >= section_cap:
            continue
        chunk_layer = str(metadata.get("document_layer", "") or "")
        if chunk_layer and layer_counts.get(chunk_layer, 0) >= max_per_layer:
            continue
        selected.append(item)
        selected_ids.add(doc_id)
        source_counts[source_name] = source_counts.get(source_name, 0) + 1
        section_counts[(source_name, section_name)] = section_counts.get((source_name, section_name), 0) + 1
        if chunk_layer:
            layer_counts[chunk_layer] = layer_counts.get(chunk_layer, 0) + 1
        if len(selected) >= n_results:
            break

    if len(selected) < n_results:
        fallback_items = selection_items if selection_items else ranked_items
        for item in fallback_items:
            _, doc_id, _, _ = item
            if doc_id in selected_ids:
                continue
            selected.append(item)
            selected_ids.add(doc_id)
            if len(selected) >= n_results:
                break

    if query_profile.get("normative_intent_query") and selected:
        def _has_complement(kind: str) -> bool:
            return any(_normative_complement_kind(item) == kind for item in selected)

        def _best_complement(kind: str):
            for item in ranked_items:
                if item[1] in selected_ids:
                    continue
                if expected_domains and _source_domain_key(str(item[3].get("source", "")), item[3]) not in expected_domains:
                    continue
                if _normative_complement_kind(item) == kind:
                    return item
            return None

        for complement_kind in ("application", "classification"):
            if _has_complement(complement_kind):
                continue
            complement = _best_complement(complement_kind)
            if complement is None:
                continue
            if len(selected) < n_results:
                selected.append(complement)
            else:
                replace_index = len(selected) - 1
                for idx in range(len(selected) - 1, -1, -1):
                    item = selected[idx]
                    if not _normative_complement_kind(item) and not any(
                        _normalize_text(phrase) in _normalize_text(f"{item[3].get('section', '')} {item[2]}")
                        for phrase in query_profile.get("technical_equivalent_phrases", [])
                    ):
                        replace_index = idx
                        break
                selected_ids.discard(selected[replace_index][1])
                selected[replace_index] = complement
            selected_ids.add(complement[1])

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
            focused.sort(key=lambda item: (_focus_hits(item[2], core_terms), item[0]), reverse=True)
            selected = (focused + non_focused)[:n_results]

    if query_profile["list_query"] and selected:
        selected.sort(
            key=lambda item: (
                len(CIRCUIT_LIST_CUE_PATTERN.findall(item[2])),
                1 if LIST_CUE_PATTERN.search(item[2]) else 0,
                item[0],
            ),
            reverse=True,
        )

    # Enforcement final: article_refs + expected_domains → eliminar items de dominios foráneos.
    # Solo se aplica si hay suficientes items del dominio esperado para no degradar la respuesta.
    if expected_domains and query_profile["article_refs"] and selected:
        domain_only = [
            item for item in selected
            if _source_domain_key(str(item[3].get("source", "")), item[3]) in expected_domains
        ]
        if len(domain_only) >= max(1, n_results // 2):
            selected = domain_only

    if query_profile["comparison"] and len(query_profile["numeric_value_groups"]) >= 2 and selected:
        value_groups = query_profile["numeric_value_groups"]
        covered = set()
        for group_index, group in enumerate(value_groups):
            if any(_matches_numeric_group(group, item[2]) for item in selected):
                covered.add(group_index)
        missing = [idx for idx in range(len(value_groups)) if idx not in covered]
        if missing:
            candidates_outside = [
                item for item in ranked_items
                if item[1] not in selected_ids
            ]
            for group_index in missing:
                group = value_groups[group_index]
                for cand in candidates_outside:
                    if _matches_numeric_group(group, cand[2]):
                        old_id = selected[-1][1]
                        selected[-1] = cand
                        selected_ids.discard(old_id)
                        selected_ids.add(cand[1])
                        break

        def _comparison_context_terms(group_index: int) -> List[str]:
            def _numeric_context_text(document: str, group: List[str]) -> str:
                normalized_document = _normalize_text(document)
                for term in group:
                    normalized_term = _normalize_text(term)
                    idx = normalized_document.find(normalized_term)
                    if idx >= 0:
                        start = max(0, idx - 240)
                        end = min(len(normalized_document), idx + len(normalized_term) + 240)
                        return normalized_document[start:end]
                return normalized_document

            term_counts: Dict[str, int] = {}
            for other_index, other_group in enumerate(value_groups):
                if other_index == group_index:
                    continue
                for _, _, document, metadata in selected:
                    if not _matches_numeric_group(other_group, document):
                        continue
                    text = f"{metadata.get('section', '')} {_numeric_context_text(document, other_group)}"
                    for token in _tokenize(text):
                        normalized = _normalize_text(token)
                        if (
                            len(normalized) < 7
                            or normalized in STOPWORDS
                            or re.search(r"\d", normalized)
                            or normalized.startswith("itc")
                        ):
                            continue
                        term_counts[normalized] = term_counts.get(normalized, 0) + 1
            return [
                term for term, _ in sorted(
                    term_counts.items(),
                    key=lambda item: (item[1], len(item[0]), item[0]),
                    reverse=True,
                )[:10]
            ]

        for group_index, group in enumerate(value_groups):
            context_terms = _comparison_context_terms(group_index)
            if not context_terms:
                continue
            group_candidates = [item for item in ranked_items if _matches_numeric_group(group, item[2])]
            if not group_candidates:
                continue

            def _context_hit_count(item: Tuple[float, str, str, Dict[str, object]]) -> int:
                doc_norm = _normalize_text(f"{item[3].get('section', '')} {item[2]}")
                return sum(1 for term in context_terms if term in doc_norm)

            selected_group_items = [item for item in selected if _matches_numeric_group(group, item[2])]
            current_context = max((_context_hit_count(item) for item in selected_group_items), default=0)
            best_candidate = max(group_candidates, key=lambda item: (_context_hit_count(item), item[0]))
            best_context = _context_hit_count(best_candidate)
            if best_context <= current_context or best_candidate[1] in selected_ids:
                continue
            replace_index = None
            if selected_group_items:
                weakest_id = min(selected_group_items, key=lambda item: (_context_hit_count(item), item[0]))[1]
                replace_index = next((idx for idx, item in enumerate(selected) if item[1] == weakest_id), None)
            if replace_index is None:
                replace_index = len(selected) - 1
            old_id = selected[replace_index][1]
            selected[replace_index] = best_candidate
            selected_ids.discard(old_id)
            selected_ids.add(best_candidate[1])

    if mentions_rebt_regulation and query_profile["scope_query"] and selected:
        selected.sort(
            key=lambda item: (
                1 if "el presente reglamento tiene por objeto" in _normalize_text(item[2]) else 0,
                item[0],
            ),
            reverse=True,
        )
    if query_profile["intent"] == "document_location" and query_profile["location_target"] and not query_profile["page_refs"] and selected:
        location_target = query_profile["location_target"]

        def _location_page(metadata: Dict[str, object]) -> int:
            for key in ("printed_page", "page"):
                try:
                    value = int(str(metadata.get(key, "") or "0"))
                except ValueError:
                    value = 0
                if value:
                    return value
            return 9999

        selected.sort(
            key=lambda item: (
                1 if re.search(rf"\b\d+(?:\.\d+)*\s+{re.escape(location_target)}\b", _normalize_text(item[2])) else 0,
                1 if location_target in _normalize_text(str(item[3].get("section", ""))) else 0,
                1 if location_target in _normalize_text(item[2]) else 0,
                -_location_page(item[3]),
                item[0],
            ),
            reverse=True,
        )
    if (
        (query_profile["phrase_queries"] or query_profile["numeric_variants"])
        and query_profile["intent"] != "document_location"
        and not query_profile["page_refs"]
        and selected
    ):
        selected.sort(
            key=lambda item: (
                sum(1 for phrase in query_profile["phrase_queries"] if _normalize_text(phrase) in _normalize_text(item[2])),
                1 if query_profile["intent"] in {"numeric_value", "table_lookup"} and str(item[3].get("chunk_kind", "")) == "table_row" else 0,
                _standalone_number_hits(query_profile["standalone_numbers"], item[2]),
                sum(1 for kw in question_keywords if _normalize_text(kw) in _normalize_text(item[2])),
                sum(
                    1 for term in query_profile["numeric_variants"]
                    if _normalize_text(term) in _normalize_text(item[2])
                    or re.sub(r"\s+", "", _normalize_text(term)) in re.sub(r"\s+", "", _normalize_text(item[2]))
                ),
                item[0],
            ),
            reverse=True,
        )

    if expected_document_variants and selected:
        variant_only = [item for item in selected if _matches_variant_focus(item)]
        if variant_only and (
            selected[0] not in variant_only
            or len(variant_only) >= max(2, min(n_results, max(1, n_results // 2)))
        ):
            selected = variant_only[:n_results]

    if (query_profile["exact_refs"] or query_profile["article_refs"] or query_profile["it_section_refs"]) and selected:
        selected.sort(
            key=lambda item: (
                _clean_structural_chunk_score(
                    item[3],
                    item[2],
                    exact_refs=query_profile["exact_refs"],
                    article_refs=query_profile["article_refs"],
                    it_section_refs=query_profile["it_section_refs"],
                ),
                item[0],
            ),
            reverse=True,
        )

    if query_profile["page_refs"] and selected:
        requested_pages = {str(page) for page in query_profile["page_refs"]}

        def _matches_requested_page(metadata: Dict[str, object]) -> int:
            page = str(metadata.get("page", "") or "")
            printed_page = str(metadata.get("printed_page", "") or "")
            return 1 if page in requested_pages or printed_page in requested_pages else 0

        selected.sort(
            key=lambda item: (
                _matches_requested_page(item[3]),
                item[0],
            ),
            reverse=True,
        )

    # Neighbor expansion: for table chunks, fetch contiguous ±1 chunks
    # (same source+page) to recover tables split across chunks.
    expand_neighbors = bool(
        query_profile["table_query"]
        or query_profile["list_query"]
        or query_profile["scope_query"]
        or query_profile["motivation_query"]
        or mentions_bt_generators
    )
    if expand_neighbors and selected:
        neighbor_ids: List[str] = []
        for _, doc_id, _, metadata in list(selected):
            if query_profile["table_query"] and str(metadata.get("chunk_kind", "")) != "table":
                continue
            parts = str(doc_id).rsplit("-", 2)
            if len(parts) != 3:
                continue
            source_part, page_part, chunk_part = parts
            try:
                chunk_idx = int(chunk_part)
                page_idx = int(page_part)
            except ValueError:
                continue
            deltas = (-1, 1)
            if query_profile["motivation_query"] or query_profile["scope_query"] or mentions_bt_generators:
                deltas = (-2, -1, 1, 2)
            for delta in deltas:
                neighbor = f"{source_part}-{page_part}-{chunk_idx + delta}"
                if neighbor not in selected_ids and neighbor not in neighbor_ids:
                    neighbor_ids.append(neighbor)
            if chunk_idx <= 2 and (query_profile["motivation_query"] or query_profile["scope_query"]):
                next_page_first = f"{source_part}-{page_idx + 1}-1"
                if next_page_first not in selected_ids and next_page_first not in neighbor_ids:
                    neighbor_ids.append(next_page_first)
        if neighbor_ids:
            try:
                neighbor_results = collection.get(
                    ids=neighbor_ids,
                    include=["documents", "metadatas"],
                )
                for nid, ndoc, nmeta in zip(
                    neighbor_results.get("ids", []) or [],
                    neighbor_results.get("documents", []) or [],
                    neighbor_results.get("metadatas", []) or [],
                ):
                    if not ndoc or not nmeta or nid in selected_ids:
                        continue
                    selected.append((0.0, nid, ndoc, nmeta))
                    selected_ids.add(nid)
            except Exception as exc:
                logger.warning("Neighbor expansion failed: %s", exc)

    context_parts = []
    abbreviation_definitions = _extract_requested_abbreviation_definitions(
        clean_question,
        selected,
    )
    if abbreviation_definitions:
        definition_lines = [
            f"{letter} = {meaning}"
            for letter, meaning in sorted(abbreviation_definitions.items())
        ]
        context_parts.append(
            "[Leyenda consolidada a partir de los fragmentos recuperados]\n"
            + "\n".join(definition_lines)
        )
    if (query_profile["list_query"] or query_profile["table_query"]) and "circuit" in normalized_question:
        circuit_definitions = _extract_circuit_definitions(selected)
        if (
            "minim" in normalized_question
            or "electrificacion basica" in normalized_question
            or "no pueden faltar" in normalized_question
        ):
            circuit_definitions = {
                code: meaning
                for code, meaning in circuit_definitions.items()
                if code in {"C1", "C2", "C3", "C4", "C5"}
            }
        if circuit_definitions:
            circuit_lines = [
                f"{code}: {meaning}"
                for code, meaning in sorted(
                    circuit_definitions.items(),
                    key=lambda item: int(item[0][1:]),
                )
            ]
            context_parts.append(
                "[Lista consolidada a partir de los fragmentos recuperados]\n"
                + "\n".join(circuit_lines)
            )
    sources = []
    seen_sources = set()
    matched_domains = 0
    matched_departments = 0
    table_selected_chunks = 0
    table_selected_signal_count = 0
    expected_departments = sorted({
        _taxonomy_for_domain(domain_name)["department"]
        for domain_name in expected_domains
    })
    for _, _, document, metadata in selected:
        source_name = str(metadata.get("source", ""))
        taxonomy = _source_taxonomy(source_name, metadata)
        clean_section = _sanitize_section_label(str(metadata.get("section", "")))
        section_suffix = f", {clean_section}" if clean_section else ""
        printed_suffix = f", pag. doc {metadata.get('printed_page')}" if metadata.get("printed_page") else ""
        kind_suffix = f", {metadata.get('chunk_kind')}" if metadata.get("chunk_kind") in {"table", "table_row"} else ""
        inferred_itcs = sorted(_inferred_itc_refs(metadata, document))
        itc_suffix = f", {', '.join(ref.upper() for ref in inferred_itcs[:3])}" if inferred_itcs else ""
        source_label = f"{metadata['source']} (pag. {metadata['page']}{printed_suffix}{itc_suffix}{section_suffix}{kind_suffix})"
        context_parts.append(f"[{source_label}]\n{_clean_context_document(document, source_name)}")
        if source_label not in seen_sources:
            sources.append(source_label)
            seen_sources.add(source_label)
        if expected_domains and _source_domain_key(source_name, metadata) in expected_domains:
            matched_domains += 1
        if expected_departments and taxonomy["department"] in expected_departments:
            matched_departments += 1
        if str(metadata.get("chunk_kind", "")) in {"table", "table_row"}:
            table_selected_chunks += 1
        table_selected_signal_count += int(metadata.get("table_signal_count", 0) or 0)

    table_candidate_signal_count = 0
    if query_profile["table_query"]:
        for _, _, document, metadata in ranked_items[: max(n_results * 3, 12)]:
            if str(metadata.get("chunk_kind", "")) in {"table", "table_row"}:
                table_candidate_signal_count += int(metadata.get("table_signal_count", 0) or 0)
            elif _looks_like_table_block(document):
                table_candidate_signal_count += _table_signal_count(document)

    table_coverage_ratio = 1.0
    if query_profile["table_query"]:
        table_coverage_ratio = (
            table_selected_signal_count / max(table_candidate_signal_count, 1)
            if table_candidate_signal_count > 0
            else 0.0
        )
        table_coverage_ratio = round(min(max(table_coverage_ratio, 0.0), 1.0), 4)

    source_names = [str(item[3].get("source", "unknown")) for item in selected]
    unique_source_names = sorted(set(source_names))
    selected_domains = sorted({
        _source_domain_key(str(item[3].get("source", "")), item[3])
        for item in selected
    })
    selected_departments = sorted({
        _source_taxonomy(str(item[3].get("source", "")), item[3])["department"]
        for item in selected
    })
    selected_document_types = sorted({
        _source_taxonomy(str(item[3].get("source", "")), item[3])["document_type"]
        for item in selected
    })
    supported_chunk_ratio, max_query_term_coverage = _query_support_metrics(
        question_keywords,
        selected,
    )
    retrieval_stats = {
        "candidate_count": candidate_count,
        "selected_count": len(selected),
        "source_diversity": len(unique_source_names),
        "top_sources": unique_source_names[:5],
        "selected_domains": selected_domains,
        "expected_domains": expected_domains,
        "expected_document_variants": expected_document_variants,
        "domain_match_ratio": round(matched_domains / max(len(selected), 1), 4) if expected_domains else 1.0,
        "index_status": "ready",
        "backend": "chroma",
        "selected_departments": selected_departments,
        "expected_departments": expected_departments,
        "department_match_ratio": round(matched_departments / max(len(selected), 1), 4) if expected_departments else 1.0,
        "selected_document_types": selected_document_types,
        "supported_chunk_ratio": supported_chunk_ratio,
        "max_query_term_coverage": max_query_term_coverage,
        "broad_query": broad_query,
        "table_selected_chunks": table_selected_chunks,
        "table_collection_hits": table_collection_hits,
        "numeric_comparison_hits": numeric_comparison_hits,
        "target_itc_refs": sorted(target_itc_refs),
        "target_article_refs": sorted(query_profile["article_refs"]),
        "target_it_section_refs": sorted(query_profile["it_section_refs"]),
        "table_selected_signal_count": table_selected_signal_count,
        "table_candidate_signal_count": table_candidate_signal_count,
        "table_coverage_ratio": table_coverage_ratio,
        "score_top": round(selected[0][0], 2) if selected else 0.0,
        "score_bottom": round(selected[-1][0], 2) if selected else 0.0,
        "all_below_threshold": (
            MIN_CHUNK_SCORE > 0
            and bool(selected)
            and selected[0][0] < MIN_CHUNK_SCORE
        ),
    }
    if logger.isEnabledFor(logging.DEBUG):
        for rank, item in enumerate(selected, 1):
            logger.debug(
                "[SELECTED #%d] score=%.1f src=%s p.%s sect=%s",
                rank, item[0], item[3].get("source", "?")[-45:],
                item[3].get("page", "?"), item[3].get("section", "")[:40],
            )
    return "\n\n".join(context_parts), sources, retrieval_stats


def search_documents_detailed(
    question: str,
    n_results: int = TOP_K_RESULTS,
    domain: str = "",
    hint_domains: List[str] | None = None,
    hint_document_variants: List[str] | None = None,
    hint_article_refs: List[str] | None = None,
    hint_it_section_refs: List[str] | None = None,
) -> Tuple[str, List[str], Dict[str, object]]:
    cached = _query_cache.get(
        question,
        n_results,
        domain,
        hint_domains,
        hint_document_variants,
        hint_article_refs,
        hint_it_section_refs,
    )
    if cached is not None:
        logger.debug("Cache hit para query: %s", question[:60])
        return cached
    if RAG_BACKEND == "azure_search":
        from azure_rag_service import search_documents_detailed_azure
        result = search_documents_detailed_azure(
            question,
            n_results=n_results,
            hint_domains=hint_domains,
            hint_document_variants=hint_document_variants,
            hint_article_refs=hint_article_refs,
            hint_it_section_refs=hint_it_section_refs,
        )
    else:
        result = _search_documents_detailed_chroma(
            question,
            n_results=n_results,
            domain=domain,
            hint_domains=hint_domains,
            hint_document_variants=hint_document_variants,
            hint_article_refs=hint_article_refs,
            hint_it_section_refs=hint_it_section_refs,
        )
    _query_cache.put(
        question,
        n_results,
        result,
        domain,
        hint_domains,
        hint_document_variants,
        hint_article_refs,
        hint_it_section_refs,
    )
    return result


def search_documents(question: str, n_results: int = TOP_K_RESULTS, domain: str = "") -> Tuple[str, List[str]]:
    context, sources, _ = search_documents_detailed(question, n_results=n_results, domain=domain)
    return context, sources
