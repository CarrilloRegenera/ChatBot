import logging
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from routes.auth import router as auth_router
from routes.chat import router as chat_router
from database import ensure_app_schema, ping_database
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(chat_router)

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


@app.on_event("startup")
def startup_init():
    ensure_app_schema()
    ping_database()
    try:
        warm_entra_jwks()
    except Exception:
        logging.getLogger(__name__).warning("No se pudo precalentar JWKS de Entra en startup", exc_info=True)
    if not SYNC_DOCUMENTS_ON_STARTUP:
        logging.getLogger(__name__).info("sync_documents desactivado en startup (SYNC_DOCUMENTS_ON_STARTUP=false)")
        return
    try:
        from rag_service import sync_documents
        sync_documents()
    except Exception:
        logging.getLogger(__name__).exception("Error durante sync_documents en startup; la API arranca de todos modos")


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
    try:
        ping_database()
    except Exception as exc:
        logging.getLogger(__name__).warning("Health check sin SQL disponible: %s", exc)
        return JSONResponse(status_code=503, content={"status": "degraded", "database": "unavailable"})
    return {"status": "ok", "database": "ready"}


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
