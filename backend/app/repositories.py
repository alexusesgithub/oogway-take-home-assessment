"""Persistence helpers: all SQL for users, sessions, messages and artifacts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Artifact, ChatSession, Message, User
from app.errors import NotFoundError


def utcnow() -> datetime:
    return datetime.now(UTC)


async def upsert_user(db: AsyncSession, external_id: str, metadata: dict[str, Any]) -> User:
    user = (await db.execute(select(User).where(User.external_id == external_id))).scalar_one_or_none()
    if user is None:
        user = User(external_id=external_id, metadata_=metadata)
        db.add(user)
    else:
        user.last_seen_at = utcnow()
        user.metadata_ = {**(user.metadata_ or {}), **metadata}
    await db.commit()
    await db.refresh(user)
    return user


async def create_session(db: AsyncSession, user: User, title: str | None) -> ChatSession:
    session = ChatSession(user_id=user.id, title=(title or "New chat").strip()[:200])
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def get_session(db: AsyncSession, user: User, session_id: uuid.UUID) -> ChatSession:
    session = (
        await db.execute(select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user.id))
    ).scalar_one_or_none()
    if session is None:
        raise NotFoundError("Chat session not found.", details={"session_id": str(session_id)})
    return session


async def list_sessions(db: AsyncSession, user: User, limit: int = 50) -> list[dict[str, Any]]:
    counts = select(Message.session_id, func.count().label("n")).group_by(Message.session_id).subquery()
    last_user = (
        select(Message.content)
        .where(Message.session_id == ChatSession.id, Message.role == "user")
        .order_by(Message.created_at.desc())
        .limit(1)
        .scalar_subquery()
    )
    rows = (
        await db.execute(
            select(ChatSession, func.coalesce(counts.c.n, 0), last_user)
            .outerjoin(counts, counts.c.session_id == ChatSession.id)
            .where(ChatSession.user_id == user.id)
            .order_by(ChatSession.updated_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "id": s.id,
            "title": s.title,
            "created_at": s.created_at,
            "updated_at": s.updated_at,
            "message_count": n,
            "preview": (preview or "")[:140] or None,
        }
        for s, n, preview in rows
    ]


async def session_messages(db: AsyncSession, session_id: uuid.UUID, limit: int | None = None) -> list[Message]:
    stmt = select(Message).where(Message.session_id == session_id).order_by(Message.created_at.desc())
    if limit:
        stmt = stmt.limit(limit)
    return list(reversed((await db.execute(stmt)).scalars().all()))


async def session_artifacts(db: AsyncSession, session_id: uuid.UUID) -> list[Artifact]:
    stmt = select(Artifact).where(Artifact.session_id == session_id).order_by(Artifact.created_at)
    return list((await db.execute(stmt)).scalars().all())


async def latest_artifact(db: AsyncSession, session_id: uuid.UUID) -> Artifact | None:
    stmt = select(Artifact).where(Artifact.session_id == session_id).order_by(Artifact.created_at.desc()).limit(1)
    return (await db.execute(stmt)).scalar_one_or_none()


async def add_message(db: AsyncSession, **values: Any) -> Message:
    message = Message(created_at=utcnow(), **values)
    db.add(message)
    await db.flush()
    return message


async def touch_session(db: AsyncSession, session: ChatSession, title: str | None = None) -> None:
    values: dict[str, Any] = {"updated_at": utcnow()}
    if title:
        values["title"] = title[:200]
    await db.execute(update(ChatSession).where(ChatSession.id == session.id).values(**values))


async def get_artifact(db: AsyncSession, user: User, artifact_id: uuid.UUID) -> Artifact:
    artifact = (
        await db.execute(
            select(Artifact)
            .join(ChatSession, ChatSession.id == Artifact.session_id)
            .where(Artifact.id == artifact_id, ChatSession.user_id == user.id)
        )
    ).scalar_one_or_none()
    if artifact is None:
        raise NotFoundError("Artifact not found.", details={"artifact_id": str(artifact_id)})
    return artifact


async def get_message(db: AsyncSession, user: User, message_id: uuid.UUID) -> Message:
    message = (
        await db.execute(
            select(Message)
            .join(ChatSession, ChatSession.id == Message.session_id)
            .where(Message.id == message_id, ChatSession.user_id == user.id)
        )
    ).scalar_one_or_none()
    if message is None:
        raise NotFoundError("Message not found.", details={"message_id": str(message_id)})
    return message
