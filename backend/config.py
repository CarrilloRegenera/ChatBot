import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"

load_dotenv(ENV_PATH)

def _to_absolute_path(path_value: str) -> str:
    expanded = os.path.expandvars(os.path.expanduser(path_value))
    path = Path(expanded)
    if not path.is_absolute():
        path = (BASE_DIR / path).resolve()
    return str(path)


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
DOCUMENTS_PATH = _to_absolute_path(os.getenv("DOCUMENTS_PATH", "../documentos"))
CHROMA_DB_PATH = _to_absolute_path(os.getenv("CHROMA_DB_PATH", "../chroma_db"))
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
GEMINI_BASELINE_MODEL = os.getenv("GEMINI_BASELINE_MODEL", "gemini-2.5-flash").strip()
TOP_K_RESULTS = int(os.getenv("TOP_K_RESULTS", "3"))
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "reglamentos").strip()
RECURSIVE_PDF_SCAN = os.getenv("RECURSIVE_PDF_SCAN", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_RERANK = os.getenv("ENABLE_RERANK", "true").strip().lower() in {"1", "true", "yes", "on"}
RERANK_MODEL = os.getenv("RERANK_MODEL", "paraphrase-multilingual-MiniLM-L12-v2").strip()
RERANK_WEIGHT = float(os.getenv("RERANK_WEIGHT", "9.0"))
MAX_CHUNKS_PER_SOURCE = int(os.getenv("MAX_CHUNKS_PER_SOURCE", "4"))
MIN_QUERY_LENGTH = int(os.getenv("MIN_QUERY_LENGTH", "4"))
MEMORY_COLLECTION_NAME = os.getenv("MEMORY_COLLECTION_NAME", f"{COLLECTION_NAME}_memoria_validada").strip()
MEMORY_MAX_RESULTS = int(os.getenv("MEMORY_MAX_RESULTS", "3"))
MEMORY_MAX_DISTANCE = float(os.getenv("MEMORY_MAX_DISTANCE", "0.35"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()

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
