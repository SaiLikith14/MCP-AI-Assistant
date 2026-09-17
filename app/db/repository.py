"""All database reads/writes live here, so the API layer never touches SQL.

Each function opens its own short-lived session. That matters for the streaming
endpoint: holding one session open for the whole response would keep a database
connection tied up while the model thinks.
"""
import uuid

from sqlalchemy import delete, select

from app.db.models import Conversation, Message, utcnow
from app.db.session import SessionLocal


async def create_conversation(first_message: str) -> Conversation:
    title = first_message.strip()[:60] or "New conversation"
    conversation = Conversation(id=str(uuid.uuid4()), title=title)
    async with SessionLocal() as session:
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)
    return conversation


async def conversation_exists(conversation_id: str) -> bool:
    async with SessionLocal() as session:
        result = await session.execute(
            select(Conversation.id).where(Conversation.id == conversation_id)
        )
        return result.scalar_one_or_none() is not None


async def add_message(conversation_id: str, role: str, content: str) -> None:
    async with SessionLocal() as session:
        session.add(Message(conversation_id=conversation_id, role=role, content=content))
        # Touch the conversation so "most recently used" ordering works.
        conversation = await session.get(Conversation, conversation_id)
        if conversation is not None:
            conversation.updated_at = utcnow()
        await session.commit()


async def get_history(conversation_id: str) -> list[dict]:
    """Prior turns in the shape the agent expects."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id)
        )
        return [{"role": m.role, "content": m.content} for m in result.scalars()]


async def list_conversations() -> list[Conversation]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(Conversation).order_by(Conversation.updated_at.desc())
        )
        return list(result.scalars())


async def get_messages(conversation_id: str) -> list[Message]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id)
        )
        return list(result.scalars())


async def delete_conversation(conversation_id: str) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(Conversation).where(Conversation.id == conversation_id))
        await session.commit()
