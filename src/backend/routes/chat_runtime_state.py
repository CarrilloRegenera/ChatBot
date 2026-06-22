import time
from threading import Lock
from typing import Dict


_LOCK_TTL = 1800
_LOCK_CLEANUP_INTERVAL = 300
_locks_guard = Lock()
_conversation_locks: Dict[int, Lock] = {}
_lock_last_used: Dict[int, float] = {}
_last_lock_cleanup: float = 0.0
_cancelled_request_ids: set[str] = set()
_cancelled_request_ids_lock = Lock()


class RequestCancelledError(Exception):
    """Raised when the client explicitly cancels a pending chat request."""


def q_preview(text: str, size: int = 90) -> str:
    one_line = " ".join((text or "").split())
    if len(one_line) <= size:
        return one_line
    return one_line[:size] + "..."


def normalize_request_id(value: str | None) -> str:
    return str(value or "").strip()


def mark_request_cancelled(request_id: str) -> None:
    normalized = normalize_request_id(request_id)
    if not normalized:
        return
    with _cancelled_request_ids_lock:
        _cancelled_request_ids.add(normalized)


def clear_cancelled_request(request_id: str) -> None:
    normalized = normalize_request_id(request_id)
    if not normalized:
        return
    with _cancelled_request_ids_lock:
        _cancelled_request_ids.discard(normalized)


def is_request_cancelled(request_id: str) -> bool:
    normalized = normalize_request_id(request_id)
    if not normalized:
        return False
    with _cancelled_request_ids_lock:
        return normalized in _cancelled_request_ids


def raise_if_request_cancelled(request_id: str) -> None:
    if is_request_cancelled(request_id):
        raise RequestCancelledError()


def get_conversation_lock(conversation_id: int) -> Lock:
    global _last_lock_cleanup
    with _locks_guard:
        now = time.time()
        if now - _last_lock_cleanup > _LOCK_CLEANUP_INTERVAL:
            stale = [cid for cid, ts in _lock_last_used.items() if now - ts > _LOCK_TTL]
            for cid in stale:
                _conversation_locks.pop(cid, None)
                _lock_last_used.pop(cid, None)
            _last_lock_cleanup = now
        if conversation_id not in _conversation_locks:
            _conversation_locks[conversation_id] = Lock()
        _lock_last_used[conversation_id] = now
        return _conversation_locks[conversation_id]


def remove_conversation_lock(conversation_id: int) -> None:
    with _locks_guard:
        _conversation_locks.pop(conversation_id, None)
        _lock_last_used.pop(conversation_id, None)
