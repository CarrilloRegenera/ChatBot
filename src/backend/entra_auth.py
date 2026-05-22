import logging
from functools import lru_cache

import jwt
from jwt import PyJWKClient

from config import ENTRA_API_SCOPE, ENTRA_CLIENT_ID, ENTRA_ENABLED, ENTRA_TENANT_ID
from entra_config import allowed_audiences, issuer_candidates


logger = logging.getLogger(__name__)


def _require_entra_config() -> None:
    if not ENTRA_ENABLED:
        raise RuntimeError("ENTRA_ENABLED no está activado")
    missing = []
    if not ENTRA_TENANT_ID:
        missing.append("ENTRA_TENANT_ID")
    if not ENTRA_CLIENT_ID:
        missing.append("ENTRA_CLIENT_ID")
    if missing:
        raise RuntimeError("Configuración Entra incompleta: " + ", ".join(missing))


def _allowed_audiences() -> tuple[str, ...]:
    return allowed_audiences(ENTRA_CLIENT_ID, ENTRA_API_SCOPE)


@lru_cache(maxsize=1)
def _jwks_client() -> PyJWKClient:
    _require_entra_config()
    jwks_url = f"https://login.microsoftonline.com/{ENTRA_TENANT_ID}/discovery/v2.0/keys"
    return PyJWKClient(jwks_url)


def validate_entra_token(token: str) -> dict:
    _require_entra_config()
    if not token:
        raise ValueError("Token Entra ausente")

    audiences = _allowed_audiences()
    if not audiences:
        raise ValueError("No hay audiences permitidos configurados")

    signing_key = _jwks_client().get_signing_key_from_jwt(token)
    payload = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=list(audiences),
        options={"require": ["exp", "iat", "iss", "aud"]},
    )
    issuer = str(payload.get("iss", "") or "")
    if issuer not in issuer_candidates(ENTRA_TENANT_ID):
        raise ValueError("Issuer Entra no permitido")
    return payload
