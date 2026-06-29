import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
DEFAULT_EMBEDDING_CACHE_MAX_ENTRIES = 20000

load_dotenv(ENV_PATH)


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None:
            return value
    return default


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
TOP_K_SYMBOL_QUERY = int(os.getenv("TOP_K_SYMBOL_QUERY", "3"))
TOP_K_COMPLEX_QUERY = int(os.getenv("TOP_K_COMPLEX_QUERY", "8"))
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
RERANK_BM25_WEIGHT = float(os.getenv("RERANK_BM25_WEIGHT", "6.0"))
MIN_CHUNK_SCORE = float(os.getenv("MIN_CHUNK_SCORE", "4.0"))
CROSS_DOMAIN_MIN_SCORE = float(os.getenv("CROSS_DOMAIN_MIN_SCORE", "30.0"))
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
ADMIN_PANEL_ALLOWED_NAMES = {
    name.strip().lower()
    for name in os.getenv(
        "ADMIN_PANEL_ALLOWED_NAMES",
        "Adrian Carrillo,Jorge Cañete,Jorge Canete",
    ).split(",")
    if name.strip()
}
EMAIL_ENABLED = _env_first("Email__Enabled", "EMAIL__ENABLED", default="false").strip().lower() in {"1", "true", "yes", "on"}
EMAIL_PROVIDER = _env_first("Email__Provider", "EMAIL__PROVIDER", default="Graph").strip()
GRAPH_EMAIL_TENANT_ID = _env_first("GraphEmail__TenantId", "GRAPH_EMAIL_TENANT_ID").strip()
GRAPH_EMAIL_CLIENT_ID = _env_first("GraphEmail__ClientId", "GRAPH_EMAIL_CLIENT_ID").strip()
GRAPH_EMAIL_CLIENT_SECRET = _env_first("GraphEmail__ClientSecret", "GRAPH_EMAIL_CLIENT_SECRET").strip()
GRAPH_EMAIL_FROM_USER = _env_first("GraphEmail__FromUser", "GRAPH_EMAIL_FROM_USER").strip()
GRAPH_EMAIL_SAVE_TO_SENT_ITEMS = _env_first("GraphEmail__SaveToSentItems", "GRAPH_EMAIL_SAVE_TO_SENT_ITEMS", default="true").strip().lower() in {"1", "true", "yes", "on"}
ENTRA_JWKS_TIMEOUT_SECS = float(_env_first("ENTRA_JWKS_TIMEOUT_SECS", default="8"))
ENTRA_JWKS_CACHE_TTL_SECS = int(_env_first("ENTRA_JWKS_CACHE_TTL_SECS", default="21600"))
GITHUB_SERVER_URL = _env_first("GITHUB_SERVER_URL", default="https://github.com").strip().rstrip("/")
GITHUB_REPOSITORY_OWNER = _env_first("GITHUB_REPOSITORY_OWNER", default="CarrilloRegenera").strip()
GITHUB_REPOSITORY_NAME = _env_first("GITHUB_REPOSITORY_NAME", default="ChatBot").strip()
GITHUB_DEPLOY_WORKFLOW = _env_first("GITHUB_DEPLOY_WORKFLOW", default="deploy-chatbot.yml").strip()
GITHUB_DEPLOY_BRANCH = _env_first("GITHUB_DEPLOY_BRANCH", default="sandbox").strip()
GITHUB_DEPLOY_TOKEN = _env_first("GITHUB_DEPLOY_TOKEN").strip()
DEPLOY_WEBHOOK_SECRET = _env_first("DEPLOY_WEBHOOK_SECRET").strip()
DEPLOYMENTS_HISTORY_LIMIT = int(_env_first("DEPLOYMENTS_HISTORY_LIMIT", default="40"))
CHATBOT_FRONTEND_URL = _env_first("CHATBOT_FRONTEND_URL", default="https://chatbot.appregenera.com").strip().rstrip("/")
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
RAG_INDEX_VERSION = os.getenv("RAG_INDEX_VERSION", "3").strip() or "3"
EMBEDDING_CACHE_MAX_ENTRIES = int(
    os.getenv("EMBEDDING_CACHE_MAX_ENTRIES", str(DEFAULT_EMBEDDING_CACHE_MAX_ENTRIES))
)
AZURE_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "").strip().rstrip("/")
AZURE_SEARCH_KEY = os.getenv("AZURE_SEARCH_KEY", "").strip()
AZURE_SEARCH_INDEX_NAME = os.getenv("AZURE_SEARCH_INDEX_NAME", "idx-chatbot-rag").strip()
AZURE_SEARCH_VECTOR_FIELD = os.getenv("AZURE_SEARCH_VECTOR_FIELD", "content_vector").strip()
BLOB_STORAGE_CONNECTION_STRING = os.getenv("BLOB_STORAGE_CONNECTION_STRING", "").strip()
BLOB_CONTAINER_NAME = os.getenv("BLOB_CONTAINER_NAME", "documentos-rag").strip()
BLOB_PREFIX = os.getenv("BLOB_PREFIX", "").strip().strip("/")

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
