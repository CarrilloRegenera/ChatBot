from fastapi import APIRouter, Header, HTTPException

from config import ENTRA_ADMIN_EMAILS, ENTRA_ENABLED
from database import db_conn
from entra_auth import validate_entra_token
from models import LoginRequest, RegisterRequest
from security import hash_password, is_hashed_password, verify_password


router = APIRouter()


def _entra_role(email: str, existing_role: str | None = None) -> str:
    normalized_email = (email or "").strip().lower()
    if normalized_email and normalized_email in ENTRA_ADMIN_EMAILS:
        return "Administrador"
    if existing_role:
        return existing_role
    return "Usuario"


@router.post("/registro")
def register(data: RegisterRequest):
    with db_conn() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT Id FROM Usuarios WHERE Email = ?", data.email)
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="El email ya esta registrado")

        hashed_password = hash_password(data.password)
        cursor.execute(
            "INSERT INTO Usuarios (Nombre, Email, Password) VALUES (?, ?, ?)",
            data.nombre,
            data.email,
            hashed_password,
        )
    return {"mensaje": "Usuario registrado correctamente"}


@router.post("/login")
def login(data: LoginRequest):
    with db_conn() as conn:
        cursor = conn.cursor()

        # Login using username (Nombre) and password.
        cursor.execute(
            "SELECT Id, Nombre, Rol, Password FROM Usuarios WHERE Nombre = ?",
            data.nombre,
        )
        user = cursor.fetchone()

        if not user or not verify_password(data.password, user[3]):
            user = None
        elif not is_hashed_password(user[3]):
            cursor.execute(
                "UPDATE Usuarios SET Password = ? WHERE Id = ?",
                hash_password(data.password),
                user[0],
            )

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


@router.post("/login/entra")
def login_entra(authorization: str | None = Header(default=None)):
    if not ENTRA_ENABLED:
        raise HTTPException(status_code=400, detail="Login Entra no habilitado")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authorization Bearer requerida")

    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = validate_entra_token(token)
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Token Entra no válido: {exc}") from exc

    email = (
        claims.get("preferred_username")
        or claims.get("email")
        or claims.get("upn")
        or ""
    ).strip()
    display_name = (claims.get("name") or email.split("@")[0] or "Usuario").strip()
    if not email:
        raise HTTPException(status_code=400, detail="El token de Entra no contiene email")

    with db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT Id, Nombre, Rol FROM Usuarios WHERE Email = ?", email)
        existing = cursor.fetchone()

        if existing:
            role = _entra_role(email, existing[2] if len(existing) > 2 else None)
            cursor.execute(
                "UPDATE Usuarios SET Nombre = ?, Rol = ?, Password = ?, AuthProvider = ? WHERE Id = ?",
                display_name,
                role,
                "__ENTRA__",
                "entra",
                existing[0],
            )
            user_id = existing[0]
            user_name = display_name
        else:
            role = _entra_role(email)
            cursor.execute(
                "INSERT INTO Usuarios (Nombre, Email, Password, Rol, AuthProvider) OUTPUT INSERTED.Id VALUES (?, ?, ?, ?, ?)",
                display_name,
                email,
                "__ENTRA__",
                role,
                "entra",
            )
            user_id = cursor.fetchone()[0]
            user_name = display_name

    return {
        "mensaje": "Login Entra correcto",
        "usuario": {
            "id": user_id,
            "nombre": user_name,
            "rol": role,
            "email": email,
            "auth_provider": "entra",
        },
    }
