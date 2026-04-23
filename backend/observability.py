import contextvars
import logging


_request_id_ctx_var = contextvars.ContextVar("request_id", default="-")


def set_request_id(request_id: str):
    return _request_id_ctx_var.set(request_id)


def reset_request_id(token):
    _request_id_ctx_var.reset(token)


def get_request_id() -> str:
    return _request_id_ctx_var.get()


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True
