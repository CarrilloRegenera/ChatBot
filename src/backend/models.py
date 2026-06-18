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
    request_id: str | None = None


class MessageCancelRequest(BaseModel):
    conversation_id: int
    request_id: str


class InteractionReviewRequest(BaseModel):
    reviewer: str = "system"


class DeploymentTriggerRequest(BaseModel):
    branch: str | None = None


class DeploymentNotificationSettingsRequest(BaseModel):
    recipients: list[str]


class DeploymentWebhookRequest(BaseModel):
    run_id: int
    run_number: int | None = None
    run_attempt: int | None = None
    workflow_name: str | None = None
    branch: str | None = None
    requested_action: str | None = None
    requested_by_email: str | None = None
    requested_by_name: str | None = None
    trigger_source: str | None = None
    status: str | None = None
    conclusion: str | None = None
    actor: str | None = None
    html_url: str | None = None
    logs_url: str | None = None
    backend_url: str | None = None
    frontend_url: str | None = None
    run_started_at: str | None = None
    completed_at: str | None = None
