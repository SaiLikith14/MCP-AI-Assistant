from datetime import datetime

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    # Omit to start a new conversation; the stream's first event returns the new id.
    conversation_id: str | None = None


class MessageOut(BaseModel):
    role: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
