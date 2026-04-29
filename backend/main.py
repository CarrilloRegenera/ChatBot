import logging
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from routes.auth import router as auth_router
from routes.chat import router as chat_router
from database import ensure_app_schema
from config import LOG_LEVEL
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
    allow_origins=["*"],
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
    try:
        from rag_service import sync_documents
        sync_documents()
    except Exception:
        logging.getLogger(__name__).exception("Error durante sync_documents en startup; la API arranca de todos modos")


@app.get("/")
def root():
    if FRONTEND_DIR.is_dir():
        return RedirectResponse(url="/ui/")
    return {"mensaje": "ChatBot API funcionando"}


@app.get("/health")
def health():
    return {"status": "ok"}
