"""ORM -> API payload conversion (validated through the public schemas)."""

from __future__ import annotations

import uuid
from typing import Any

from app.artifacts.sanitize import sanitize_html
from app.db.models import Artifact, Message
from app.schemas import ArtifactOut, MessageOut


def message_out(message: Message, artifact_id: uuid.UUID | None = None) -> dict[str, Any]:
    return MessageOut(
        id=message.id,
        session_id=message.session_id,
        role=message.role,  # type: ignore[arg-type]
        content=message.content,
        status=message.status,  # type: ignore[arg-type]
        route=message.route,
        route_reason=message.route_reason,
        provider=message.provider,
        model=message.model,
        sources=message.sources or [],
        citations=message.citations or [],
        quality=message.quality or {},
        metrics=message.metrics or {},
        error=message.error,
        feedback=message.feedback,
        artifact_id=artifact_id,
        created_at=message.created_at,
    ).model_dump(mode="json")


def artifact_out(artifact: Artifact) -> dict[str, Any]:
    rendered = sanitize_html(artifact.content, artifact.title).html if artifact.kind == "html" else None
    return ArtifactOut(
        id=artifact.id,
        session_id=artifact.session_id,
        message_id=artifact.message_id,
        kind=artifact.kind,  # type: ignore[arg-type]
        title=artifact.title,
        content=artifact.content,
        version=artifact.version,
        created_at=artifact.created_at,
        rendered_html=rendered,
        sanitizer_report=artifact.sanitizer_report or {},
    ).model_dump(mode="json")
