from fastapi import HTTPException, Request
import unicodedata

from config import (
    ADMIN_API_KEY,
    ADMIN_PANEL_ALLOWED_EMAILS,
    ADMIN_PANEL_ALLOWED_NAMES,
    ENTRA_ENABLED,
)
from database import db_conn
from entra_auth import validate_entra_token


def _admin_identity_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    without_marks = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join(without_marks.strip().lower().split())


ADMIN_PANEL_ALLOWED_NAME_KEYS = {_admin_identity_key(name) for name in ADMIN_PANEL_ALLOWED_NAMES}


def assert_admin(request: Request) -> None:
    auth_header = (request.headers.get("authorization") or "").strip()
    if ENTRA_ENABLED and auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        try:
            claims = validate_entra_token(token)
        except Exception as exc:
            raise HTTPException(status_code=401, detail=f"Token Entra no valido: {exc}") from exc
        email = (
            claims.get("preferred_username")
            or claims.get("email")
            or claims.get("upn")
            or ""
        ).strip().lower()
        if email and email in ADMIN_PANEL_ALLOWED_EMAILS:
            return
        raise HTTPException(status_code=403, detail="Acceso solo para administradores de Entra")

    admin_key = (request.headers.get("x-admin-key") or "").strip()
    if admin_key and ADMIN_API_KEY and admin_key == ADMIN_API_KEY:
        return

    user_name = (request.headers.get("x-user-name") or "").strip().lower()
    user_email = (request.headers.get("x-user-email") or "").strip().lower()
    auth_provider = (request.headers.get("x-auth-provider") or "").strip().lower()
    is_local_admin = auth_provider == "local" and user_name == "admin"
    is_allowed_entra_email = bool(user_email and user_email in ADMIN_PANEL_ALLOWED_EMAILS)
    is_allowed_admin_name = _admin_identity_key(user_name) in ADMIN_PANEL_ALLOWED_NAME_KEYS
    if not (is_local_admin or is_allowed_entra_email or is_allowed_admin_name):
        raise HTTPException(status_code=403, detail="Acceso solo para rol Administrador")
    if admin_key and ADMIN_API_KEY and admin_key != ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Acceso admin denegado")


def load_user_by_id(user_id: int) -> tuple | None:
    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT TOP 1 Id, Nombre, Email, Rol, AuthProvider FROM Usuarios WHERE Id = ?",
            user_id,
        )
        return cursor.fetchone()


def resolve_request_user_id(request: Request) -> int:
    auth_header = (request.headers.get("authorization") or "").strip()
    if ENTRA_ENABLED and auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        try:
            claims = validate_entra_token(token)
        except Exception as exc:
            raise HTTPException(status_code=401, detail=f"Token Entra no valido: {exc}") from exc

        email = (
            claims.get("preferred_username")
            or claims.get("email")
            or claims.get("upn")
            or ""
        ).strip().lower()
        if not email:
            raise HTTPException(status_code=401, detail="El token de Entra no contiene email")

        with db_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT TOP 1 Id FROM Usuarios WHERE LOWER(Email) = ?", email)
            row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Usuario de Entra no sincronizado en el chatbot")
        return int(row[0])

    user_id_header = (request.headers.get("x-user-id") or "").strip()
    if not user_id_header.isdigit():
        raise HTTPException(status_code=401, detail="Identidad de usuario no disponible")

    user_id = int(user_id_header)
    user_row = load_user_by_id(user_id)
    if not user_row:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")

    header_provider = (request.headers.get("x-auth-provider") or "").strip().lower()
    header_email = (request.headers.get("x-user-email") or "").strip().lower()
    if header_provider and str(user_row[4] or "").strip().lower() not in {"", header_provider}:
        raise HTTPException(status_code=403, detail="La sesion no coincide con el proveedor del usuario")
    if header_email and str(user_row[2] or "").strip().lower() not in {"", header_email}:
        raise HTTPException(status_code=403, detail="La sesion no coincide con el email del usuario")
    return user_id
