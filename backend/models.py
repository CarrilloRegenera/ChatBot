from pydantic import BaseModel

class RegistroRequest(BaseModel):
    nombre: str
    email: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str