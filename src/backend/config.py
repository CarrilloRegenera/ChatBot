import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"

load_dotenv(ENV_PATH)


def _is_local_runtime() -> bool:
    website_hostname = os.getenv("WEBSITE_HOSTNAME", "").strip()
    if website_hostname:
        return False

    environment_name = os.getenv("ENVIRONMENT", "").strip().lower()
    if environment_name in {"qa", "pro", "prod", "production"}:
        return False

    return True


def _default_appregenera_api_base_url() -> str:
    if _is_local_runtime():
        return "http://localhost:5204"
    return "https://api-appregenera-pro-amgxcba0awffdjcd.westeurope-01.azurewebsites.net"

def _to_absolute_path(path_value: str) -> str:
    expanded = os.path.expandvars(os.path.expanduser(path_value))
    path = Path(expanded)
    if not path.is_absolute():
        path = (BASE_DIR / path).resolve()
    return str(path)


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
DOCUMENTS_PATH = _to_absolute_path(os.getenv("DOCUMENTS_PATH", "../../data/documentos"))
CHROMA_DB_PATH = _to_absolute_path(os.getenv("CHROMA_DB_PATH", "../../chroma_db"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-nano").strip()
OPENAI_TIMEOUT_SECS = float(os.getenv("OPENAI_TIMEOUT_SECS", "45"))
OPENAI_MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "1"))
LLM_SECONDARY_MODEL = (
    os.getenv("LLM_SECONDARY_MODEL")
    or os.getenv("GEMINI_SECONDARY_MODEL")
    or os.getenv("GEMINI_FLASH_503_FALLBACK_MODEL")
    or "gpt-4.1-mini"
).strip()
LLM_FALLBACK_MODEL = (
    os.getenv("LLM_FALLBACK_MODEL")
    or os.getenv("GEMINI_FLASH_503_FALLBACK_MODEL")
    or LLM_SECONDARY_MODEL
).strip()
CONFIDENCE_FALLBACK_THRESHOLD = float(os.getenv("CONFIDENCE_FALLBACK_THRESHOLD", "0.65"))
FALLBACK_MIN_CONFIDENCE_GAP = float(os.getenv("FALLBACK_MIN_CONFIDENCE_GAP", "0.07"))
FALLBACK_MAX_BASE_CONFIDENCE = float(os.getenv("FALLBACK_MAX_BASE_CONFIDENCE", "0.72"))
FALLBACK_MIN_RETRIEVAL_QUALITY = (os.getenv("FALLBACK_MIN_RETRIEVAL_QUALITY", "mixed") or "mixed").strip().lower()
OPENAI_BASELINE_MODEL = (os.getenv("OPENAI_BASELINE_MODEL", "gpt-5.4-nano")).strip()
TOP_K_RESULTS = int(os.getenv("TOP_K_RESULTS", "5"))
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "reglamentos").strip()
TABLE_COLLECTION_NAME = os.getenv("TABLE_COLLECTION_NAME", f"{COLLECTION_NAME}_tablas").strip()
RECURSIVE_PDF_SCAN = os.getenv("RECURSIVE_PDF_SCAN", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_RERANK = os.getenv("ENABLE_RERANK", "true").strip().lower() in {"1", "true", "yes", "on"}
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-small").strip()
EMBEDDING_FP16 = os.getenv("EMBEDDING_FP16", "false").strip().lower() in {"1", "true", "yes", "on"}
EMBEDDING_QUERY_PREFIX = os.getenv("EMBEDDING_QUERY_PREFIX", "query:").strip()
EMBEDDING_PASSAGE_PREFIX = os.getenv("EMBEDDING_PASSAGE_PREFIX", "passage:").strip()
RERANK_MODEL = os.getenv("RERANK_MODEL", EMBEDDING_MODEL).strip()
RERANK_MODEL_REVISION = os.getenv("RERANK_MODEL_REVISION", "").strip()
RERANK_WEIGHT = float(os.getenv("RERANK_WEIGHT", "9.0"))
MAX_CHUNKS_PER_SOURCE = int(os.getenv("MAX_CHUNKS_PER_SOURCE", "4"))
MIN_QUERY_LENGTH = int(os.getenv("MIN_QUERY_LENGTH", "4"))
MEMORY_COLLECTION_NAME = os.getenv("MEMORY_COLLECTION_NAME", f"{COLLECTION_NAME}_memoria_validada").strip()
MEMORY_MAX_RESULTS = int(os.getenv("MEMORY_MAX_RESULTS", "3"))
MEMORY_MAX_DISTANCE = float(os.getenv("MEMORY_MAX_DISTANCE", "0.35"))
CONVERSATION_LOCK_TIMEOUT_SECS = float(os.getenv("CONVERSATION_LOCK_TIMEOUT_SECS", "60"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",")
    if origin.strip()
]
SYNC_DOCUMENTS_ON_STARTUP = os.getenv("SYNC_DOCUMENTS_ON_STARTUP", "true").strip().lower() in {"1", "true", "yes", "on"}
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "").strip()
ENTRA_ENABLED = os.getenv("ENTRA_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
ENTRA_TENANT_ID = os.getenv("ENTRA_TENANT_ID", "").strip()
ENTRA_CLIENT_ID = os.getenv("ENTRA_CLIENT_ID", "").strip()
ENTRA_API_SCOPE = os.getenv("ENTRA_API_SCOPE", "").strip()
ENTRA_ADMIN_EMAILS = {
    email.strip().lower()
    for email in os.getenv("ENTRA_ADMIN_EMAILS", "").split(",")
    if email.strip()
}
ADMIN_PANEL_ALLOWED_EMAILS = {
    email.strip().lower()
    for email in os.getenv(
        "ADMIN_PANEL_ALLOWED_EMAILS",
        "jcanete@regeneraenergy.es,acarrillo@regeneraenergy.es",
    ).split(",")
    if email.strip()
}
APPREGENERA_API_BASE_URL = os.getenv("APPREGENERA_API_BASE_URL", _default_appregenera_api_base_url()).strip().rstrip("/")
APPREGENERA_TIMEOUT_SECS = float(os.getenv("APPREGENERA_TIMEOUT_SECS", "20"))
APPREGENERA_SQL_CONNECTION_STRING = os.getenv("APPREGENERA_SQL_CONNECTION_STRING", "").strip()
APPREGENERA_ALLOWED_MODULES = tuple(
    module.strip().lower()
    for module in os.getenv("APPREGENERA_ALLOWED_MODULES", "estudios,produccion").split(",")
    if module.strip()
)
APPREGENERA_DEV_BYPASS_KEY = os.getenv("APPREGENERA_DEV_BYPASS_KEY", "").strip()

RAG_BACKEND = os.getenv("RAG_BACKEND", "chroma").strip().lower()
AZURE_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "").strip().rstrip("/")
AZURE_SEARCH_KEY = os.getenv("AZURE_SEARCH_KEY", "").strip()
AZURE_SEARCH_INDEX_NAME = os.getenv("AZURE_SEARCH_INDEX_NAME", "idx-chatbot-rag").strip()
AZURE_SEARCH_VECTOR_FIELD = os.getenv("AZURE_SEARCH_VECTOR_FIELD", "content_vector").strip()
BLOB_STORAGE_CONNECTION_STRING = os.getenv("BLOB_STORAGE_CONNECTION_STRING", "").strip()
BLOB_CONTAINER_NAME = os.getenv("BLOB_CONTAINER_NAME", "documentos-rag").strip()
BLOB_PREFIX = os.getenv("BLOB_PREFIX", "").strip().strip("/")
BLOB_PREFIX_ALTA_TENSION = os.getenv("BLOB_PREFIX_ALTA_TENSION", "").strip().strip("/")
BLOB_PREFIX_BAJA_TENSION = os.getenv("BLOB_PREFIX_BAJA_TENSION", "").strip().strip("/")
BLOB_PREFIX_GUIAS_TECNICAS = os.getenv("BLOB_PREFIX_GUIAS_TECNICAS", "").strip().strip("/")
BLOB_PREFIX_RITE = os.getenv("BLOB_PREFIX_RITE", "").strip().strip("/")

# Keep only general language stopwords by default; domain-specific words
# can be injected via env for each corpus.
DEFAULT_STOPWORDS = {
    "como", "donde", "cuando", "cuales", "cuanto", "sobre", "segun",
    "para", "desde", "hasta", "esta", "este", "estas", "estos", "debe",
    "deben", "que", "con", "sin", "entre", "hacia",
    # palabras estructurales de preguntas — no aportan semántica al tema
    "cuantos", "cuantas", "tiene", "cada", "seran", "seria", "puede",
    "podria", "suele", "deben", "hacer", "tener",
}

stopwords_env = os.getenv("DOMAIN_STOPWORDS", "")
domain_stopwords = {
    token.strip().lower()
    for token in stopwords_env.split(",")
    if token.strip()
}

STOPWORDS = DEFAULT_STOPWORDS.union(domain_stopwords)
