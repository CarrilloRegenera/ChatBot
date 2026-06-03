import logging
import os
from threading import Lock, Thread
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from routes.auth import router as auth_router
from routes.deployments_webhook import router as deployments_webhook_router
from config import ALLOWED_ORIGINS, CHATBOT_FRONTEND_URL, LOG_LEVEL, SYNC_DOCUMENTS_ON_STARTUP
from entra_auth import warm_entra_jwks
from observability import RequestIdFilter, reset_request_id, set_request_id


logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname).1s | req=%(request_id)s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
for handler in logging.getLogger().handlers:
    handler.addFilter(RequestIdFilter())


app = FastAPI(title="ChatBot API")
app.state.runtime_ready = False
app.state.startup_error = ""
app.state.database_ready = False
app.state.chat_router_ready = False
app.state.chat_router_error = ""
_chat_router_lock = Lock()

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(auth_router, prefix="/api")
app.include_router(deployments_webhook_router)
app.include_router(deployments_webhook_router, prefix="/api")


def _is_lightweight_path(path: str) -> bool:
    normalized = path.rstrip("/") or "/"
    return (
        normalized in {"/", "/api", "/health", "/api/health"}
        or normalized in {"/admin/deployments/webhook", "/api/admin/deployments/webhook"}
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

        app.include_router(chat_router)
        app.include_router(chat_router, prefix="/api")
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
        except Exception as exc:
            app.state.chat_router_error = str(exc)
            logging.getLogger(__name__).exception("No se pudieron cargar las rutas de chat")
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "El servicio de chat todavia se esta cargando. Intentalo de nuevo en unos segundos.",
                    "chat_router_ready": False,
                    "chat_router_error": str(exc),
                },
            )
    return await call_next(request)


@app.on_event("startup")
def startup_init():
    app.state.runtime_ready = False
    app.state.startup_error = ""
    Thread(target=_startup_background_init, daemon=True, name="startup-init").start()


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
    return {
        "status": "ok",
        "database": "deferred",
        "runtime_ready": bool(getattr(app.state, "runtime_ready", False)),
        "startup_error": getattr(app.state, "startup_error", ""),
        "chat_router_ready": bool(getattr(app.state, "chat_router_ready", False)),
        "chat_router_error": getattr(app.state, "chat_router_error", ""),
        "deployment_image_tag": os.getenv("DEPLOY_IMAGE_TAG", "").strip(),
    }


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
