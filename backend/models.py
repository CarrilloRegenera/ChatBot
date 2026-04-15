from pydantic import BaseModel

class RegisterRequest(BaseModel):
    nombre: str
    email: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class ConversationRequest(BaseModel):
    user_id: int
    title: str

class MessageRequest(BaseModel):
    conversation_id: int
    question: str