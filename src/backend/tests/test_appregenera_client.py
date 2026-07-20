import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import appregenera_client


def test_build_url_accepts_https_api_base(monkeypatch):
    monkeypatch.setattr(appregenera_client, "APPREGENERA_API_BASE_URL", "https://api.example.com/")

    assert appregenera_client._build_url("/v1/projects") == "https://api.example.com/v1/projects"


def test_build_url_rejects_non_http_schemes(monkeypatch):
    monkeypatch.setattr(appregenera_client, "APPREGENERA_API_BASE_URL", "file:///tmp/api")

    with pytest.raises(appregenera_client.AppRegeneraClientError, match="HTTP o HTTPS"):
        appregenera_client._build_url("/v1/projects")


def test_build_url_rejects_non_relative_paths(monkeypatch):
    monkeypatch.setattr(appregenera_client, "APPREGENERA_API_BASE_URL", "https://api.example.com")

    with pytest.raises(appregenera_client.AppRegeneraClientError, match="relativa"):
        appregenera_client._build_url("https://other.example.com")
