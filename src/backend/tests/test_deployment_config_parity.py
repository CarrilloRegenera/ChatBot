import re
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[3]


def _extract_regex(path: Path, pattern: str) -> str:
    match = re.search(pattern, path.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, f"No se encontro patron {pattern!r} en {path}"
    return match.group(1)


def test_rag_index_version_default_matches_dockerfile_and_workflow():
    dockerfile_value = _extract_regex(
        REPO_ROOT / "src/backend/Dockerfile",
        r"^ARG RAG_INDEX_VERSION=(.+)$",
    )
    workflow_value = _extract_regex(
        REPO_ROOT / ".github/workflows/deploy-chatbot.yml",
        r'^\s+RAG_INDEX_VERSION:\s+"([^"]+)"$',
    )

    assert config.DEFAULT_RAG_INDEX_VERSION == config.RAG_INDEX_VERSION
    assert dockerfile_value == config.DEFAULT_RAG_INDEX_VERSION
    assert workflow_value == config.DEFAULT_RAG_INDEX_VERSION
