"""Sessions, messages (chat turns) and feedback."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app.api.deps import current_user, get_db
from app.db.models import Message, User
from app.errors import AppError
from app.schemas import (
    ChatTurnOut,
    ErrorResponse,
    FeedbackIn,
    MessageCreate,
    MessageOut,
    SessionCreate,
    SessionDetail,
    SessionOut,
    SessionUpdate,
)
from app.serializers import artifact_out, message_out

log = structlog.get_logger("api")
router = APIRouter(prefix="/api", tags=["sessions"])

ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}
KEEPALIVE_SECONDS = 15.0


@router.post("/sessions", response_model=SessionOut, status_code=201, responses=ERRORS)
async def create_session(
    body: SessionCreate | None = None, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)
) -> SessionOut:
    """Start a new chat. Each session keeps its own independent context."""
    session = await repo.create_session(db, user, body.title if body else None)
    return SessionOut(id=session.id, title=session.title, created_at=session.created_at, updated_at=session.updated_at)


@router.get("/sessions", response_model=list[SessionOut], responses=ERRORS)
async def list_sessions(db: AsyncSession = Depends(get_db), user: User = Depends(current_user)) -> list[SessionOut]:
    return [SessionOut(**row) for row in await repo.list_sessions(db, user)]


@router.get("/sessions/{session_id}", response_model=SessionDetail, responses=ERRORS)
async def get_session(
    session_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)
) -> SessionDetail:
    session = await repo.get_session(db, user, session_id)
    messages = await repo.session_messages(db, session.id)
    artifacts = await repo.session_artifacts(db, session.id)
    by_message = {a.message_id: a.id for a in artifacts}
    return SessionDetail(
        id=session.id,
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at,
        message_count=len(messages),
        messages=[message_out(m, by_message.get(m.id)) for m in messages],
        artifacts=[artifact_out(a) for a in artifacts],
    )


@router.patch("/sessions/{session_id}", response_model=SessionOut, responses=ERRORS)
async def rename_session(
    session_id: uuid.UUID, body: SessionUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)
) -> SessionOut:
    session = await repo.get_session(db, user, session_id)
    session.title = body.title.strip()
    await db.commit()
    await db.refresh(session)
    return SessionOut(id=session.id, title=session.title, created_at=session.created_at, updated_at=session.updated_at)


@router.delete("/sessions/{session_id}", status_code=204, responses=ERRORS)
async def delete_session(
    session_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)
) -> Response:
    session = await repo.get_session(db, user, session_id)
    await db.delete(session)
    await db.commit()
    return Response(status_code=204)


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str, ensure_ascii=False)}\n\n"


@router.post(
    "/sessions/{session_id}/messages",
    responses={
        **ERRORS,
        200: {
            "description": "`text/event-stream` when `stream=true` (events: user_message, status, meta, "
            "sources, delta, reset, done, error); otherwise a ChatTurnOut JSON body.",
            "model": ChatTurnOut,
        },
    },
)
async def send_message(
    session_id: uuid.UUID,
    body: MessageCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
) -> Any:
    """Send a user message and get the assistant's grounded reply.

    The turn runs in a background task that persists its result even if the
    client disconnects mid-stream; the stream is only a live view of it.
    """
    await repo.get_session(db, user, session_id)  # 404 before streaming starts
    # Release this request's connection now: a turn can stream for minutes and the
    # worker uses its own short-lived sessions. Holding it would exhaust the pool.
    user_id = user.id
    await db.close()
    service = request.app.state.chat_service
    request_id = getattr(request.state, "request_id", None)

    if not body.stream:

        async def _ignore(event: str, data: dict[str, Any]) -> None:
            return None

        result = await service.run_turn(
            user_id=user_id,
            session_id=session_id,
            content=body.content,
            mode=body.mode,
            provider=body.provider,
            emit=_ignore,
        )
        return ChatTurnOut(
            user_message=MessageOut(**result.user_message),
            assistant_message=MessageOut(**result.assistant_message),
            artifact=result.artifact,
        )

    queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue()

    async def emit(event: str, data: dict[str, Any]) -> None:
        await queue.put((event, data))

    async def worker() -> None:
        try:
            await service.run_turn(
                user_id=user_id,
                session_id=session_id,
                content=body.content,
                mode=body.mode,
                provider=body.provider,
                emit=emit,
            )
        except AppError as exc:
            log.warning("turn_failed", code=exc.code, error=exc.message)
            await queue.put(("error", exc.to_dict(request_id)))
        except Exception:
            log.exception("turn_crashed")
            await queue.put(
                ("error", AppError("Unexpected error while answering.", hint="See logs.").to_dict(request_id))
            )
        finally:
            await queue.put(None)

    task = asyncio.create_task(worker())
    tasks: set[asyncio.Task] = request.app.state.background_tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)

    async def stream() -> AsyncIterator[str]:
        yield ": stream-open\n\n"
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
            except TimeoutError:
                yield ": keep-alive\n\n"
                continue
            if item is None:
                break
            yield _sse(*item)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Request-ID": request_id or ""},
    )


@router.post("/messages/{message_id}/feedback", response_model=MessageOut, responses=ERRORS)
async def message_feedback(
    message_id: uuid.UUID, body: FeedbackIn, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, Any]:
    """Thumbs up/down on an assistant answer (feeds the success metric)."""
    message = await repo.get_message(db, user, message_id)
    if message.role != "assistant":
        raise AppError("Feedback applies to assistant messages only.", code="validation_error", status_code=422)
    await db.execute(update(Message).where(Message.id == message.id).values(feedback=body.rating))
    await db.commit()
    await db.refresh(message)
    return message_out(message)
