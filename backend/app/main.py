"""FastAPI application factory and lifecycle."""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.agent.providers import ProviderRegistry
from app.agent.runtime import AgentRuntime, ClaudeAgentRuntime, default_config_dir
from app.agent.skills import SkillRegistry
from app.api import artifacts, sessions, system
from app.config import Settings, get_settings
from app.db.session import create_engine, create_session_factory, ping
from app.errors import register_exception_handlers
from app.observability import configure_logging
from app.retrieval.embeddings import OllamaEmbedder
from app.retrieval.ingest import IngestionInProgress, embed_pending, ingest, kb_status
from app.retrieval.search import Retriever
from app.services.chat import ChatService

log = structlog.get_logger("app")


async def _wait_for_db(app: FastAPI, attempts: int = 15) -> bool:
    for i in range(attempts):
        try:
            await ping(app.state.engine)
            return True
        except Exception as exc:
            log.warning("database_not_ready", attempt=i + 1, error=f"{type(exc).__name__}: {str(exc)[:120]}")
            await asyncio.sleep(min(2 * (i + 1), 5))
    return False


def create_app(settings: Settings | None = None, runtime: AgentRuntime | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN202
        state = app.state
        state.settings = settings
        state.engine = create_engine(settings)
        state.session_factory = create_session_factory(state.engine)
        state.skills = SkillRegistry.load()
        state.providers = ProviderRegistry(settings)
        state.retriever = Retriever(OllamaEmbedder(settings), settings.retrieval_candidate_pool)
        state.runtime = runtime or ClaudeAgentRuntime(settings.llm_max_concurrency, default_config_dir())
        state.chat_service = ChatService(
            settings, state.session_factory, state.retriever, state.providers, state.runtime, state.skills
        )
        state.background_tasks = set()
        state.ingest_task = None

        async def run_ingest(trigger: str, full: bool = False) -> None:
            try:
                await ingest(state.session_factory, settings, trigger=trigger, full=full)
                if settings.embeddings_enabled:
                    await embed_pending(state.session_factory, settings)
            except IngestionInProgress:
                log.info("ingest_skipped_already_running")
            except Exception as exc:  # surfaced via /api/kb/status and /health/ready
                log.error("background_ingest_failed", error=str(exc)[:500])

        state.run_ingest = run_ingest

        log.info(
            "app_starting",
            version=__version__,
            provider=settings.llm_provider,
            fallback=settings.llm_fallback_provider,
            ollama_model=settings.ollama_runtime_model,
            anthropic_configured=bool(settings.anthropic_api_key),
            embeddings=settings.embeddings_enabled,
        )
        if runtime is None:
            state.providers.start_ollama_provisioning()
        if await _wait_for_db(app, attempts=1 if settings.app_env == "test" else 15):
            if settings.auto_ingest:
                async with state.session_factory() as db:
                    kb = await kb_status(db)
                if kb["chunks"] == 0:
                    log.info("knowledge_base_empty_starting_ingest")
                    state.ingest_task = asyncio.create_task(run_ingest("startup"))
                elif settings.embeddings_enabled and kb["embedded_chunks"] < kb["chunks"]:
                    state.ingest_task = asyncio.create_task(embed_pending(state.session_factory, settings))
        else:
            log.error("database_unavailable_at_startup", hint="The API will report 'down' on /health/ready.")
        yield
        for task in [*state.background_tasks, state.ingest_task]:
            if task is not None and not task.done():
                task.cancel()
        await state.engine.dispose()

    app = FastAPI(
        title="Lenny Growth Assistant API",
        version=__version__,
        description="Grounded answers, Ship 30 essays and artifacts from Lenny's Podcast transcripts.",
        lifespan=lifespan,
    )
    register_exception_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Any:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("request_failed", method=request.method, path=request.url.path)
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "internal_error",
                        "message": "Unexpected server error.",
                        "retryable": False,
                        "request_id": request_id,
                    }
                },
            )
        response.headers["X-Request-ID"] = request_id
        if not request.url.path.startswith(("/assets", "/health/live")):
            log.info(
                "http_request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        return response

    app.include_router(system.router)
    app.include_router(sessions.router)
    app.include_router(artifacts.router)
    _mount_frontend(app, Path(settings.static_dir))
    return app


def _mount_frontend(app: FastAPI, static_dir: Path) -> None:
    """Serve the built React app (single container deploy). SPA fallback to index.html."""
    static_root = static_dir.resolve()
    index = static_root / "index.html"
    if not index.is_file():
        return
    if (static_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> Any:
        if path.startswith(("api/", "health/")):
            return JSONResponse(status_code=404, content={"error": {"code": "not_found", "message": "Not found."}})
        candidate = (static_root / path).resolve()  # noqa: ASYNC240 - tiny stat on a local file
        if path and static_root in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


app = create_app()
