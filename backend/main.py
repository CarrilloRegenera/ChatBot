from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routes.auth import router as auth_router
from routes.chat import router as chat_router


app = FastAPI(title="ChatBot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(chat_router)

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