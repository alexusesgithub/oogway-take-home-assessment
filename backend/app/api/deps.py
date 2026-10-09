"""FastAPI dependencies: DB sessions, current user, services."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app.db.models import User
from app.errors import ValidationFailed

USER_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{6,128}$")
DEFAULT_USER = "local-evaluator"


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


async def current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_user_id: str | None = Header(default=None, description="Anonymous client id (UUID kept in localStorage)."),
) -> User:
    """Lightweight identity for an internal tool.

    There is no login in this scope (see PRD: SSO is out of scope). The web
    client generates a random id once and sends it as `X-User-Id`, which keeps
    each browser's sessions separate and lets us store user metadata.
    """
    external_id = x_user_id or DEFAULT_USER
    if not USER_ID_RE.match(external_id):
        raise ValidationFailed("Invalid X-User-Id header.", hint="Use 6-128 chars: letters, digits, _ . : -")
    metadata = {
        "user_agent": (request.headers.get("user-agent") or "")[:200],
        "locale": (request.headers.get("accept-language") or "").split(",")[0][:20],
    }
    return await repo.upsert_user(db, external_id, metadata)
