from contextlib import asynccontextmanager
import logging
import os
import sqlite3
from threading import Lock, Thread
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from routes.admin_deployments import router as admin_deployments_router
from routes.auth import router as auth_router
from routes.deployments_webhook import router as deployments_webhook_router
from config import (
    ALLOWED_ORIGINS,
    CHATBOT_FRONTEND_URL,
    CHROMA_DB_PATH,
    LOG_LEVEL,
    RAG_BACKEND,
    SYNC_DOCUMENTS_ON_STARTUP,
)
from entra_auth import warm_entra_jwks
from observability import RequestIdFilter, reset_request_id, set_request_id


logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname).1s | req=%(request_id)s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
for handler in logging.getLogger().handlers:
    handler.addFilter(RequestIdFilter())


def _reset_runtime_state() -> None:
    app.state.runtime_ready = False
    app.state.startup_error = ""
    app.state.database_ready = False
    app.state.chat_router_ready = False
    app.state.chat_router_error = ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    _reset_runtime_state()
    Thread(target=_startup_background_init, daemon=True, name="startup-init").start()
    yield


app = FastAPI(title="ChatBot API", lifespan=lifespan)
_reset_runtime_state()
_chat_router_lock = Lock()

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(auth_router, prefix="/api")
app.include_router(admin_deployments_router)
app.include_router(admin_deployments_router, prefix="/api")
app.include_router(deployments_webhook_router)
app.include_router(deployments_webhook_router, prefix="/api")


def _is_lightweight_path(path: str) -> bool:
    normalized = path.rstrip("/") or "/"
    return (
        normalized in {"/", "/api", "/health", "/api/health"}
        or normalized in {"/admin/deployments/webhook", "/api/admin/deployments/webhook"}
        or normalized.startswith("/admin/deployments")
        or normalized.startswith("/api/admin/deployments")
        or normalized.startswith("/login")
        or normalized.startswith("/api/login")
        or normalized.startswith("/registro")
        or normalized.startswith("/api/registro")
        or normalized.startswith("/ui")
    )


def _include_chat_router() -> None:
    if getattr(app.state, "chat_router_ready", False):
        return
    with _chat_router_lock:
        if getattr(app.state, "chat_router_ready", False):
            return
        app.state.chat_router_error = ""
        logger = logging.getLogger(__name__)
        logger.info("Cargando rutas de chat")
        from routes.chat import router as chat_router
        from routes.chat_admin import router as chat_admin_router

        app.include_router(chat_router)
        app.include_router(chat_router, prefix="/api")
        app.include_router(chat_admin_router)
        app.include_router(chat_admin_router, prefix="/api")
        app.state.chat_router_ready = True
        logger.info("Rutas de chat cargadas")


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())[:12]
    token = set_request_id(request_id)
    try:
        response = await call_next(request)
    finally:
        reset_request_id(token)
    response.headers["x-request-id"] = request_id
    return response


@app.middleware("http")
async def lazy_chat_router_middleware(request: Request, call_next):
    if request.method.upper() != "OPTIONS" and not _is_lightweight_path(request.url.path):
        try:
            _include_chat_router()
        except Exception:
            app.state.chat_router_error = "unavailable"
            logging.getLogger(__name__).exception("No se pudieron cargar las rutas de chat")
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "El servicio de chat todavia se esta cargando. Intentalo de nuevo en unos segundos.",
                    "chat_router_ready": False,
                },
            )
    return await call_next(request)


def _startup_background_init():
    logger = logging.getLogger(__name__)
    app.state.database_ready = False
    if not SYNC_DOCUMENTS_ON_STARTUP:
        logger.info("sync_documents desactivado en startup (SYNC_DOCUMENTS_ON_STARTUP=false)")
        app.state.runtime_ready = True
    try:
        warm_entra_jwks()
    except Exception:
        logger.warning("No se pudo precalentar JWKS de Entra en startup", exc_info=True)
    if not SYNC_DOCUMENTS_ON_STARTUP:
        return
    try:
        from rag_service import sync_documents
        sync_documents()
    except Exception:
        logger.exception("Error durante sync_documents en startup; la API arranca de todos modos")
    app.state.runtime_ready = True


def _rag_health_snapshot() -> dict:
    snapshot = {
        "rag_backend": RAG_BACKEND,
        "rag_ready": None,
        "rag_index_status": "not_checked",
        "rag_indexed_chunks": None,
    }
    if RAG_BACKEND == "azure_search":
        # Keep Azure validation read-only and avoid leaking provider errors in a
        # public health response.  The detailed failure remains in application logs.
        from azure_rag_service import azure_index_health

        snapshot.update(azure_index_health())
        return snapshot

    if RAG_BACKEND != "chroma":
        return snapshot

    db_path = Path(CHROMA_DB_PATH) / "chroma.sqlite3"
    snapshot["rag_indexed_chunks"] = 0
    if not db_path.exists():
        snapshot["rag_ready"] = False
        snapshot["rag_index_status"] = "missing"
        return snapshot

    conn = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM embeddings")
        indexed_chunks = int(cursor.fetchone()[0] or 0)
        cursor.close()
    except Exception:
        logging.getLogger(__name__).exception("No se pudo consultar el indice Chroma para health")
        snapshot["rag_ready"] = False
        snapshot["rag_index_status"] = "error"
        return snapshot
    finally:
        if conn is not None:
            conn.close()

    snapshot["rag_indexed_chunks"] = indexed_chunks
    snapshot["rag_ready"] = indexed_chunks > 0
    snapshot["rag_index_status"] = "ready" if indexed_chunks > 0 else "empty"
    return snapshot


def _health_payload() -> dict:
    payload = {
        "status": "ok",
        "database": "deferred",
        "runtime_ready": bool(getattr(app.state, "runtime_ready", False)),
        "startup_error": getattr(app.state, "startup_error", ""),
        "chat_router_ready": bool(getattr(app.state, "chat_router_ready", False)),
        "chat_router_error": getattr(app.state, "chat_router_error", ""),
        "deployment_image_tag": os.getenv("DEPLOY_IMAGE_TAG", "").strip(),
    }
    payload.update(_rag_health_snapshot())
    return payload


@app.get("/")
def root():
    if FRONTEND_DIR.is_dir() and not os.getenv("WEBSITE_HOSTNAME", "").strip():
        return RedirectResponse(url="/ui/")
    return {
        "mensaje": "ChatBot API funcionando",
        "frontend_url": CHATBOT_FRONTEND_URL,
        "health_url": "/health",
    }


@app.get("/health")
def health():
    return _health_payload()


@app.get("/api")
def api_root():
    return root()


@app.get("/api/health")
def api_health():
    return health()


@app.options("/{full_path:path}")
def options_preflight(full_path: str, request: Request):
    origin = (request.headers.get("origin") or "").strip()
    allowed_origin = origin if origin in ALLOWED_ORIGINS else (ALLOWED_ORIGINS[0] if ALLOWED_ORIGINS else "*")
    requested_headers = (request.headers.get("access-control-request-headers") or "*").strip() or "*"
    return Response(
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": allowed_origin,
            "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
            "Access-Control-Allow-Headers": requested_headers,
            "Access-Control-Max-Age": "86400",
            "Vary": "Origin, Access-Control-Request-Headers",
        },
    )
