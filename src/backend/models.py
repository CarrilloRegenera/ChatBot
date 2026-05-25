from pydantic import BaseModel

class RegisterRequest(BaseModel):
    nombre: str
    email: str
    password: str

class LoginRequest(BaseModel):
    nombre: str
    password: str

class ConversationRequest(BaseModel):
    user_id: int
    title: str
    chat_mode: str | None = None

class MessageRequest(BaseModel):
    conversation_id: int
    question: str
    chat_mode: str | None = None


class InteractionReviewRequest(BaseModel):
    reviewer: str = "system"
