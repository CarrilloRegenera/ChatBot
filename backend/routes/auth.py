from fastapi import APIRouter, HTTPException
from models import RegistroRequest, LoginRequest
from database import get_connection

router = APIRouter()

@router.post("/registro")
def registro(datos: RegistroRequest):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT Id FROM Usuarios WHERE Email = ?", datos.email)
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="El email ya está registrado")
    
    cursor.execute(
        "INSERT INTO Usuarios (Nombre, Email, Password) VALUES (?, ?, ?)",
        datos.nombre, datos.email, datos.password
    )

    conn.commit()
    conn.close()
    return {"mensaje": "Usuario registrado correctamente"}

@router.post("/login")
def login(datos: LoginRequest):
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT Id, Nombre, Rol FROM Usuarios WHERE Email = ? AND Password = ?",
        datos.email, datos.password
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