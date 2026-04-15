from fastapi import APIRouter, HTTPException
from models import RegisterRequest, LoginRequest
from database import get_connection

router = APIRouter()

@router.post("/registro")
def register(data: RegisterRequest):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT Id FROM Usuarios WHERE Email = ?", data.email)
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="El email ya está registrado")
    
    cursor.execute(
        "INSERT INTO Usuarios (Nombre, Email, Password) VALUES (?, ?, ?)",
        data.nombre, data.email, data.password
    )

    conn.commit()
    conn.close()
    return {"mensaje": "Usuario registrado correctamente"}

@router.post("/login")
def login(data: LoginRequest):
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT Id, Nombre, Rol FROM Usuarios WHERE Email = ? AND Password = ?",
        data.email, data.password
    )
    user = cursor.fetchone()
    conn.close()
    
    if not user:
        raise HTTPException(status_code=401, detail="Email y contraseña no coinciden")
    
    return {
        "mensaje": "Login correcto",
        "usuario": {
            "id": user[0],
            "nombre": user[1],
            "rol": user[2]
        }
    }