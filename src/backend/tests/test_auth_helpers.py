import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import routes.auth_helpers as auth_helpers


def _request(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin",
            "headers": [(name.encode(), value.encode()) for name, value in headers.items()],
        }
    )


def test_assert_admin_rejects_spoofed_identity_headers_when_entra_is_enabled(monkeypatch):
    monkeypatch.setattr(auth_helpers.cfg, "ENTRA_ENABLED", True)
    monkeypatch.setattr(auth_helpers.cfg, "ADMIN_API_KEY", "")

    with pytest.raises(HTTPException, match="Authorization Bearer requerida") as exc_info:
        auth_helpers.assert_admin(_request({"x-user-name": "admin", "x-user-email": "admin@example.com"}))

    assert exc_info.value.status_code == 401


def test_assert_admin_allows_configured_admin_key_when_entra_is_enabled(monkeypatch):
    monkeypatch.setattr(auth_helpers.cfg, "ENTRA_ENABLED", True)
    monkeypatch.setattr(auth_helpers.cfg, "ADMIN_API_KEY", "test-admin-key")

    auth_helpers.assert_admin(_request({"x-admin-key": "test-admin-key"}))


def test_assert_admin_allows_configured_entra_email(monkeypatch):
    monkeypatch.setattr(auth_helpers.cfg, "ENTRA_ENABLED", True)
    monkeypatch.setattr(auth_helpers.cfg, "ADMIN_API_KEY", "")
    monkeypatch.setattr(auth_helpers.cfg, "ADMIN_PANEL_ALLOWED_EMAILS", {"admin@example.com"})
    monkeypatch.setattr(auth_helpers, "validate_entra_token", lambda token: {"preferred_username": "admin@example.com"})

    auth_helpers.assert_admin(_request({"authorization": "Bearer test-token"}))
