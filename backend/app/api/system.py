"""Health, configuration, knowledge-base operations, retrieval debug and metrics."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.api.deps import current_user, get_db
from app.db.models import User
from app.db.session import ping
from app.errors import ConflictError, Unauthorized
from app.retrieval.ingest import kb_status
from app.schemas import ConfigOut, ErrorResponse, HealthOut, UserOut, UserUpdate

log = structlog.get_logger("api")
router = APIRouter(tags=["system"])


@router.get("/health/live", response_model=HealthOut)
async def live() -> HealthOut:
    """Process is up (used by the container healthcheck)."""
    return HealthOut(status="ok", version=__version__)


@router.get("/health/ready", response_model=HealthOut, responses={503: {"model": HealthOut}})
async def ready(request: Request) -> JSONResponse:
    """Dependency health: database, knowledge base and model providers.

    `ok` = everything needed for grounded answers works; `degraded` = the app
    serves but something is impaired (e.g. Ollama down -> evidence-only mode);
    `down` = the database is unreachable (HTTP 503).
    """
    state = request.app.state
    checks: dict[str, dict[str, Any]] = {}
    started = time.perf_counter()
    try:
        await asyncio.wait_for(ping(state.engine), timeout=3)
        checks["database"] = {"ok": True, "latency_ms": int((time.perf_counter() - started) * 1000)}
    except Exception as exc:
        checks["database"] = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}

    if checks["database"]["ok"]:
        async with state.session_factory() as db:
            kb = await kb_status(db)
        ingesting = state.ingest_task is not None and not state.ingest_task.done()
        checks["knowledge_base"] = {
            "ok": kb["chunks"] > 0,
            "episodes": kb["episodes"],
            "chunks": kb["chunks"],
            "ingesting": ingesting,
            "last_run_status": (kb["last_run"] or {}).get("status"),
        }
    statuses = await state.providers.statuses()
    default = state.settings.llm_provider
    for s in statuses:
        checks[f"provider:{s.name}"] = {"ok": s.available, "state": s.state, "detail": s.detail, "hint": s.hint}

    if not checks["database"]["ok"]:
        status = "down"
    elif checks["knowledge_base"]["ok"] and checks[f"provider:{default}"]["ok"]:
        status = "ok"
    else:
        status = "degraded"
    body = HealthOut(status=status, checks=checks, version=__version__)
    return JSONResponse(body.model_dump(), status_code=503 if status == "down" else 200)


@router.get("/api/config", response_model=ConfigOut)
async def config(request: Request, db: AsyncSession = Depends(get_db)) -> ConfigOut:
    """What the UI needs: provider toggle state, knowledge-base stats, skills."""
    state = request.app.state
    s = state.settings
    return ConfigOut(
        app_version=__version__,
        default_provider=s.llm_provider,
        fallback_provider=s.llm_fallback_provider,
        providers=[p.to_dict() for p in await state.providers.statuses()],
        agent_tools=s.agent_tools,
        embeddings_enabled=s.embeddings_enabled,
        knowledge_base={
            **(await kb_status(db)),
            "ingesting": state.ingest_task is not None and not state.ingest_task.done(),
            "repo_url": s.transcripts_repo_url,
        },
        skills=[
            {"name": k.name, "route": k.route, "version": k.version, "description": k.description}
            for k in state.skills.all()
        ],
    )


@router.get("/api/search", responses={422: {"model": ErrorResponse}})
async def search(
    request: Request,
    q: str = Query(min_length=2, max_length=500, description="Search query"),
    k: int = Query(default=8, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Retrieval debug endpoint: see exactly which passages a question retrieves."""
    result = await request.app.state.retriever.search(
        db, q, top_k=k, max_per_episode=request.app.state.settings.retrieval_max_per_episode
    )
    return result.to_dict()


@router.get("/api/kb/status")
async def knowledge_base_status(request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    status = await kb_status(db)
    status["ingesting"] = request.app.state.ingest_task is not None and not request.app.state.ingest_task.done()
    return status


@router.post(
    "/api/kb/ingest", status_code=202, responses={401: {"model": ErrorResponse}, 409: {"model": ErrorResponse}}
)
async def trigger_ingest(
    request: Request, x_admin_token: str | None = Header(default=None), full: bool = False
) -> dict[str, Any]:
    """Refresh the knowledge base from the transcript repository (idempotent).

    Requires `X-Admin-Token` = ADMIN_TOKEN, except in development when no
    token is configured.
    """
    s = request.app.state.settings
    expected = s.admin_token.get_secret_value() if s.admin_token else None
    if expected is None and s.app_env != "development":
        raise Unauthorized("Ingestion endpoint is disabled.", hint="Set ADMIN_TOKEN to enable it.")
    if expected is not None and x_admin_token != expected:
        raise Unauthorized("Invalid or missing X-Admin-Token.")
    state = request.app.state
    if state.ingest_task is not None and not state.ingest_task.done():
        raise ConflictError("An ingestion run is already in progress.", retryable=True)
    state.ingest_task = asyncio.create_task(state.run_ingest("api", full))
    return {"status": "started", "full": full}


@router.get("/api/metrics")
async def metrics(db: AsyncSession = Depends(get_db), days: int = Query(default=30, ge=1, le=365)) -> dict[str, Any]:
    """Product & operational metrics from stored turns (see PRD success metrics)."""
    sql = text(
        """
        SELECT coalesce(provider, 'none') AS provider, count(*) AS answers,
               count(*) FILTER (WHERE status = 'complete' AND jsonb_array_length(citations) > 0) AS grounded,
               count(*) FILTER (WHERE quality->>'answer_type' = 'not_covered') AS not_covered,
               count(*) FILTER (WHERE status = 'degraded') AS degraded,
               count(*) FILTER (WHERE jsonb_array_length(quality->'invalid_citations') > 0) AS with_invalid_citations,
               avg((quality->>'citation_coverage')::float) AS avg_citation_coverage,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY (metrics->>'total_ms')::float) AS p50_total_ms,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY (metrics->>'total_ms')::float) AS p95_total_ms,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY (metrics->>'ttft_ms')::float) AS p50_ttft_ms,
               count(*) FILTER (WHERE feedback = 1) AS thumbs_up,
               count(*) FILTER (WHERE feedback = -1) AS thumbs_down
        FROM messages
        WHERE role = 'assistant' AND coalesce(route, '') <> 'chitchat'
          AND created_at > now() - make_interval(days => :days)
        GROUP BY ROLLUP (coalesce(provider, 'none'))
        """
    )
    rows = (await db.execute(sql, {"days": days})).mappings().all()

    def _fmt(row: Any) -> dict[str, Any]:
        answers = row["answers"] or 0
        rated = (row["thumbs_up"] or 0) + (row["thumbs_down"] or 0)
        return {
            "answers": answers,
            "grounded_answer_rate": round(row["grounded"] / answers, 3) if answers else None,
            "not_covered_rate": round(row["not_covered"] / answers, 3) if answers else None,
            "degraded_rate": round(row["degraded"] / answers, 3) if answers else None,
            "invalid_citation_rate": round(row["with_invalid_citations"] / answers, 3) if answers else None,
            "avg_citation_coverage": round(row["avg_citation_coverage"], 3) if row["avg_citation_coverage"] else None,
            "p50_total_ms": row["p50_total_ms"],
            "p95_total_ms": row["p95_total_ms"],
            "p50_ttft_ms": row["p50_ttft_ms"],
            "helpful_rate": round(row["thumbs_up"] / rated, 3) if rated else None,
            "ratings": rated,
        }

    # ROLLUP always yields a grand-total row (provider IS NULL), even with no data.
    overall = next(r for r in rows if r["provider"] is None)
    return {
        "window_days": days,
        "overall": _fmt(overall),
        "by_provider": {r["provider"]: _fmt(r) for r in rows if r["provider"] is not None},
    }


@router.get("/api/me", response_model=UserOut)
async def me(user: User = Depends(current_user)) -> User:
    return user


@router.patch("/api/me", response_model=UserOut)
async def update_me(body: UserUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)) -> User:
    user = await db.merge(user)
    user.display_name = body.display_name.strip()
    await db.commit()
    await db.refresh(user)
    return user
