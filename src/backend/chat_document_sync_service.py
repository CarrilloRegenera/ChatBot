import time
from threading import Lock, Thread
from typing import Callable, Dict, Tuple


DEFAULT_SYNC_STATUS = {
    "state": "idle",
    "started_at": None,
    "finished_at": None,
    "result": None,
    "error": "",
    "heartbeat_at": None,
    "phase": "",
    "current_file": "",
    "processed_files": 0,
    "total_files": 0,
}


_document_sync_lock = Lock()
_document_sync_inflight = False
_document_sync_status = dict(DEFAULT_SYNC_STATUS)


def import_state(status: Dict[str, object], inflight: bool) -> None:
    global _document_sync_status, _document_sync_inflight
    with _document_sync_lock:
        _document_sync_status = dict(status)
        _document_sync_inflight = bool(inflight)


def export_state() -> Tuple[Dict[str, object], bool]:
    with _document_sync_lock:
        return dict(_document_sync_status), _document_sync_inflight


def update_document_sync_status(**updates) -> Dict[str, object]:
    global _document_sync_status
    with _document_sync_lock:
        _document_sync_status = {
            **_document_sync_status,
            **updates,
            "heartbeat_at": time.time(),
        }
        return dict(_document_sync_status)


def start_document_sync_background(
    *,
    rag_service_factory: Callable[[], object],
    logger,
    state_callback: Callable[[Dict[str, object], bool], None] | None = None,
) -> Dict[str, object]:
    global _document_sync_inflight, _document_sync_status
    with _document_sync_lock:
        if _document_sync_inflight:
            return dict(_document_sync_status)
        _document_sync_inflight = True
        _document_sync_status = {
            "state": "running",
            "started_at": time.time(),
            "finished_at": None,
            "result": None,
            "error": "",
            "heartbeat_at": time.time(),
            "phase": "starting",
            "current_file": "",
            "processed_files": 0,
            "total_files": 0,
        }
        if state_callback is not None:
            state_callback(dict(_document_sync_status), _document_sync_inflight)

    def _worker() -> None:
        global _document_sync_inflight
        try:
            def _progress_callback(payload: Dict[str, object]) -> None:
                status = update_document_sync_status(**payload)
                if state_callback is not None:
                    _status, _inflight = export_state()
                    state_callback(_status, _inflight)

            result = rag_service_factory().sync_documents(progress_callback=_progress_callback)
            update_document_sync_status(
                state="completed",
                finished_at=time.time(),
                result=result,
                error="",
                phase="completed",
                current_file="",
            )
            if state_callback is not None:
                _status, _inflight = export_state()
                state_callback(_status, _inflight)
        except Exception as exc:
            logger.exception("Error durante sync documental en segundo plano")
            update_document_sync_status(
                state="failed",
                finished_at=time.time(),
                error=str(exc),
                phase="failed",
            )
            if state_callback is not None:
                _status, _inflight = export_state()
                state_callback(_status, _inflight)
        finally:
            with _document_sync_lock:
                _document_sync_inflight = False
                if state_callback is not None:
                    state_callback(dict(_document_sync_status), _document_sync_inflight)

    Thread(target=_worker, daemon=True, name="document-sync").start()
    return dict(_document_sync_status)
