from fastapi import APIRouter, HTTPException

from database import get_connection
from models import LoginRequest, RegisterRequest


router = APIRouter()


@router.post("/registro")
def register(data: RegisterRequest):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT Id FROM Usuarios WHERE Email = ?", data.email)
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="El email ya esta registrado")

    cursor.execute(
        "INSERT INTO Usuarios (Nombre, Email, Password) VALUES (?, ?, ?)",
        data.nombre,
        data.email,
        data.password,
    )

    conn.commit()
    conn.close()
    return {"mensaje": "Usuario registrado correctamente"}


@router.post("/login")
def login(data: LoginRequest):
    conn = get_connection()
    cursor = conn.cursor()

    # Login using username (Nombre) and password.
    cursor.execute(
        "SELECT Id, Nombre, Rol FROM Usuarios WHERE Nombre = ? AND Password = ?",
        data.nombre,
        data.password,
    )
    user = cursor.fetchone()
    conn.close()

    if not user:
        raise HTTPException(status_code=401, detail="Usuario y contrasena no coinciden")

    return {
        "mensaje": "Login correcto",
        "usuario": {
            "id": user[0],
            "nombre": user[1],
            "rol": user[2],
        },
    }
