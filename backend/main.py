import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
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

@app.get("/")
def root():
    return{"mensaje: ChatBot API funcionando"}

@app.get("/health")
def health():
    return{"status: ok"}

from database import get_connection

@app.get("/test-db")
def test_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM Usuarios")
    rows = cursor.fetchall()
    usuarios = []
    for row in rows:
        usuarios.append({
            "id": row[0],
            "nombre": row[1],
            "email": row[2]
        })
    conn.close()
    return {"usuarios": usuarios}
