"""Artifact retrieval and sandboxed rendering."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app.api.deps import current_user, get_db
from app.artifacts.sanitize import CSP, sanitize_html
from app.db.models import User
from app.schemas import ArtifactOut, ErrorResponse
from app.serializers import artifact_out

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


@router.get("/{artifact_id}", response_model=ArtifactOut, responses={404: {"model": ErrorResponse}})
async def get_artifact(
    artifact_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, Any]:
    return artifact_out(await repo.get_artifact(db, user, artifact_id))


@router.get("/{artifact_id}/render", response_class=HTMLResponse, responses={404: {"model": ErrorResponse}})
async def render_artifact(
    artifact_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)
) -> Response:
    """Sanitised HTML served with a CSP `sandbox` header (defence in depth for
    "open in new tab"; the in-app viewer uses a sandboxed iframe)."""
    artifact = await repo.get_artifact(db, user, artifact_id)
    if artifact.kind != "html":
        return PlainTextResponse(artifact.content, media_type="text/markdown; charset=utf-8")
    html = sanitize_html(artifact.content, artifact.title).html
    return HTMLResponse(
        html,
        headers={
            "Content-Security-Policy": f"sandbox; {CSP}",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
        },
    )


@router.get("/{artifact_id}/download", responses={404: {"model": ErrorResponse}})
async def download_artifact(
    artifact_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)
) -> Response:
    artifact = await repo.get_artifact(db, user, artifact_id)
    ext, media = ("html", "text/html") if artifact.kind == "html" else ("md", "text/markdown")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in artifact.title.lower())[:60].strip("-")
    body = sanitize_html(artifact.content, artifact.title).html if artifact.kind == "html" else artifact.content
    return Response(
        body,
        media_type=f"{media}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe or "artifact"}-v{artifact.version}.{ext}"'},
    )
