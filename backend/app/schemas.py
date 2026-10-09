"""Public API request/response contracts (OpenAPI is generated from these)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import ProviderName

Mode = Literal["auto", "answer", "essay", "artifact"]


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool = False
    request_id: str | None = None
    hint: str | None = None
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


# --------------------------------------------------------------------------- users


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    external_id: str
    display_name: str | None
    created_at: datetime
    last_seen_at: datetime


class UserUpdate(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)


# --------------------------------------------------------------------------- sessions


class SessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class SessionUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class SessionOut(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    preview: str | None = None


class SourceOut(BaseModel):
    id: str
    chunk_id: int
    episode_slug: str
    title: str
    guest: str
    publish_date: str | None = None
    timestamp: str | None = None
    start_seconds: int | None = None
    youtube_url: str | None = None
    source_url: str | None = None
    speakers: str | None = None
    excerpt: str
    score: float | None = None
    coverage: float | None = None


class MessageOut(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    status: Literal["complete", "degraded", "error"]
    route: str | None = None
    route_reason: str | None = None
    provider: str | None = None
    model: str | None = None
    sources: list[SourceOut] = []
    citations: list[str] = []
    quality: dict[str, Any] = {}
    metrics: dict[str, Any] = {}
    error: dict[str, Any] | None = None
    feedback: int | None = None
    artifact_id: uuid.UUID | None = None
    created_at: datetime


class ArtifactOut(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    message_id: uuid.UUID | None
    kind: Literal["markdown", "html"]
    title: str
    content: str
    version: int
    created_at: datetime
    rendered_html: str | None = Field(
        default=None, description="Sanitised, CSP-locked HTML document for sandboxed rendering (html kind only)."
    )
    sanitizer_report: dict[str, int] = {}


class SessionDetail(SessionOut):
    messages: list[MessageOut]
    artifacts: list[ArtifactOut]


# --------------------------------------------------------------------------- chat


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000, description="The user's message.")
    mode: Mode = Field(default="auto", description="Force a skill, or let the router decide.")
    provider: ProviderName | None = Field(default=None, description="Override LLM_PROVIDER for this turn.")
    stream: bool = Field(default=True, description="Server-Sent Events when true, a single JSON body when false.")

    @field_validator("content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


class ChatTurnOut(BaseModel):
    user_message: MessageOut
    assistant_message: MessageOut
    artifact: ArtifactOut | None = None


class FeedbackIn(BaseModel):
    rating: Literal[-1, 1]


# --------------------------------------------------------------------------- ops


class ProviderStatusOut(BaseModel):
    name: ProviderName
    label: str
    kind: str
    model: str
    available: bool
    state: str
    detail: str | None = None
    hint: str | None = None


class ConfigOut(BaseModel):
    app_version: str
    default_provider: ProviderName
    fallback_provider: ProviderName | None
    providers: list[ProviderStatusOut]
    agent_tools: str
    embeddings_enabled: bool
    knowledge_base: dict[str, Any]
    skills: list[dict[str, Any]]


class HealthOut(BaseModel):
    status: Literal["ok", "degraded", "down"]
    checks: dict[str, dict[str, Any]] = {}
    version: str
