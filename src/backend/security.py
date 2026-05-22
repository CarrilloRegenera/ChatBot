import base64
import hashlib
import hmac
import os


_PBKDF2_PREFIX = "pbkdf2_sha256"
_DEFAULT_ITERATIONS = 600000
_SALT_BYTES = 16


def hash_password(password: str, *, iterations: int = _DEFAULT_ITERATIONS) -> str:
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    salt_b64 = base64.b64encode(salt).decode("ascii")
    digest_b64 = base64.b64encode(digest).decode("ascii")
    return f"{_PBKDF2_PREFIX}${iterations}${salt_b64}${digest_b64}"


def is_hashed_password(value: str | None) -> bool:
    return bool(value) and value.startswith(f"{_PBKDF2_PREFIX}$")


def verify_password(password: str, stored_value: str | None) -> bool:
    if not stored_value:
        return False
    if not is_hashed_password(stored_value):
        return hmac.compare_digest(stored_value, password)

    try:
        _, iterations_raw, salt_b64, digest_b64 = stored_value.split("$", 3)
        iterations = int(iterations_raw)
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected = base64.b64decode(digest_b64.encode("ascii"))
    except Exception:
        return False

    computed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(expected, computed)
