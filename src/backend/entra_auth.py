import json
import logging
import time

import jwt
import requests
from jwt.algorithms import RSAAlgorithm

from config import (
    ENTRA_API_SCOPE,
    ENTRA_CLIENT_ID,
    ENTRA_ENABLED,
    ENTRA_JWKS_CACHE_TTL_SECS,
    ENTRA_JWKS_TIMEOUT_SECS,
    ENTRA_TENANT_ID,
)
from entra_config import allowed_audiences, issuer_candidates


logger = logging.getLogger(__name__)

_JWKS_CACHE: dict[str, object] = {
    "expires_at": 0.0,
    "keys": [],
}


def _require_entra_config() -> None:
    if not ENTRA_ENABLED:
        raise RuntimeError("ENTRA_ENABLED no esta activado")
    missing = []
    if not ENTRA_TENANT_ID:
        missing.append("ENTRA_TENANT_ID")
    if not ENTRA_CLIENT_ID:
        missing.append("ENTRA_CLIENT_ID")
    if missing:
        raise RuntimeError("Configuracion Entra incompleta: " + ", ".join(missing))


def _allowed_audiences() -> tuple[str, ...]:
    return allowed_audiences(ENTRA_CLIENT_ID, ENTRA_API_SCOPE)


def _jwks_url() -> str:
    return f"https://login.microsoftonline.com/{ENTRA_TENANT_ID}/discovery/v2.0/keys"


def _jwks_document() -> dict:
    _require_entra_config()
    now = time.time()
    expires_at = float(_JWKS_CACHE.get("expires_at", 0.0) or 0.0)
    cached_keys = _JWKS_CACHE.get("keys")
    if cached_keys and expires_at > now:
        return {"keys": cached_keys}

    logger.info("Actualizando JWKS de Entra desde %s", _jwks_url())
    response = requests.get(_jwks_url(), timeout=ENTRA_JWKS_TIMEOUT_SECS)
    response.raise_for_status()
    payload = response.json()
    keys = payload.get("keys") or []
    _JWKS_CACHE["keys"] = keys
    _JWKS_CACHE["expires_at"] = now + max(60, ENTRA_JWKS_CACHE_TTL_SECS)
    return {"keys": keys}


def _signing_key_from_token(token: str):
    try:
        token_header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise ValueError("Cabecera del token Entra no valida") from exc

    kid = str(token_header.get("kid") or "").strip()
    if not kid:
        raise ValueError("El token Entra no contiene kid")

    jwks = _jwks_document().get("keys") or []
    for jwk in jwks:
        if str(jwk.get("kid") or "").strip() == kid:
            return RSAAlgorithm.from_jwk(json.dumps(jwk))

    # Reintenta una vez sin cache por si Azure AD ha rotado claves.
    _JWKS_CACHE["expires_at"] = 0.0
    jwks = _jwks_document().get("keys") or []
    for jwk in jwks:
        if str(jwk.get("kid") or "").strip() == kid:
            return RSAAlgorithm.from_jwk(json.dumps(jwk))

    raise ValueError("No se encontro una clave valida para el token Entra")


def validate_entra_token(token: str) -> dict:
    _require_entra_config()
    if not token:
        raise ValueError("Token Entra ausente")

    audiences = _allowed_audiences()
    if not audiences:
        raise ValueError("No hay audiences permitidos configurados")

    try:
        signing_key = _signing_key_from_token(token)
        payload = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=list(audiences),
            options={"require": ["exp", "iat", "iss", "aud"]},
        )
    except requests.Timeout as exc:
        logger.warning("Timeout consultando JWKS de Entra")
        raise ValueError("Timeout validando el token de Microsoft") from exc
    except requests.RequestException as exc:
        logger.warning("Error de red consultando JWKS de Entra: %s", exc)
        raise ValueError("No se pudo validar el token de Microsoft") from exc
    except jwt.InvalidTokenError as exc:
        raise ValueError(f"Token Entra no valido: {exc}") from exc

    issuer = str(payload.get("iss", "") or "")
    if issuer not in issuer_candidates(ENTRA_TENANT_ID):
        raise ValueError("Issuer Entra no permitido")
    return payload
