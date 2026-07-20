import json
from typing import Any
from urllib import error, parse, request

from config import APPREGENERA_API_BASE_URL, APPREGENERA_DEV_BYPASS_KEY, APPREGENERA_TIMEOUT_SECS


class AppRegeneraClientError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _build_headers(user_token: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
    }
    if user_token:
        headers["Authorization"] = f"Bearer {user_token}"
    if APPREGENERA_DEV_BYPASS_KEY:
        headers["x-chatbot-dev-key"] = APPREGENERA_DEV_BYPASS_KEY
    return headers


def _build_url(path: str) -> str:
    base_url = APPREGENERA_API_BASE_URL.rstrip("/")
    parsed = parse.urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise AppRegeneraClientError("APPREGENERA_API_BASE_URL debe usar HTTP o HTTPS")
    if not path.startswith("/"):
        raise AppRegeneraClientError("La ruta de AppRegenera debe ser relativa a la API")
    return f"{base_url}{path}"


def get_json(path: str, *, params: dict[str, Any] | None = None, user_token: str | None = None) -> Any:
    url = _build_url(path)
    if params:
        clean_params = {key: value for key, value in params.items() if value is not None and value != ""}
        if clean_params:
            url = f"{url}?{parse.urlencode(clean_params, doseq=True)}"

    req = request.Request(url, headers=_build_headers(user_token), method="GET")
    return _execute(req)


def post_json(path: str, payload: dict[str, Any], *, user_token: str | None = None) -> Any:
    url = _build_url(path)
    data = json.dumps(payload).encode("utf-8")
    headers = _build_headers(user_token)
    headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, headers=headers, method="POST")
    return _execute(req)


def _execute(req: request.Request) -> Any:
    try:
        # The request URL is built by _build_url, which only accepts HTTP(S) endpoints.
        with request.urlopen(req, timeout=APPREGENERA_TIMEOUT_SECS) as response:  # nosec B310
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body) if body else {}
        except Exception:
            payload = {}
        detail = payload.get("detail") or payload.get("message") or body or str(exc)
        raise AppRegeneraClientError(str(detail), status_code=exc.code) from exc
    except error.URLError as exc:
        raise AppRegeneraClientError(f"No se pudo conectar con AppRegenera: {exc.reason}") from exc
