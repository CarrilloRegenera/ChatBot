import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"

load_dotenv(ENV_PATH)

def _to_absolute_path(path_value: str) -> str:
    path = Path(path_value)
    if not path.is_absolute():
        path = (BASE_DIR / path).resolve()
    return str(path)


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
DOCUMENTS_PATH = _to_absolute_path(os.getenv("DOCUMENTS_PATH", "../documentos"))
CHROMA_DB_PATH = _to_absolute_path(os.getenv("CHROMA_DB_PATH", "../chroma_db"))
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite").strip()
TOP_K_RESULTS = int(os.getenv("TOP_K_RESULTS", "3"))

