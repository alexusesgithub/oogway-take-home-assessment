"""Shared fixtures.

* Unit tests need nothing external.
* Tests marked `db` use a throw-away database (`lenny_test`) on the Postgres
  from docker compose (override with TEST_DATABASE_URL). They are skipped,
  not failed, when Postgres is unreachable.
* The LLM is replaced by `FakeRuntime`, so tests are deterministic, fast and
  free; live model behaviour is covered by the manual test plan.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.agent.providers import ProviderStatus
from app.agent.runtime import AgentEvent, AgentRequest
from app.config import Settings

BACKEND_DIR = Path(__file__).resolve().parent.parent
FIXTURE_TRANSCRIPTS = Path(__file__).resolve().parent / "fixtures" / "transcripts"
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://lenny:lenny@localhost:5432/lenny_test"
)


class FakeRuntime:
    """Deterministic stand-in for ClaudeAgentRuntime. `responder` maps the
    request to the model's text; `error` makes the next run fail."""

    def __init__(self) -> None:
        self.requests: list[AgentRequest] = []
        self.responder: Callable[[AgentRequest], str] = default_responder
        self.error: Exception | None = None

    async def run(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        self.requests.append(request)
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        text = self.responder(request)
        for i in range(0, len(text), 40):
            yield AgentEvent("delta", {"text": text[i : i + 40]})
        yield AgentEvent(
            "final",
            {"text": text, "ttft_ms": 5, "model_ms": 20, "input_tokens": 100, "output_tokens": 50, "num_turns": 1},
        )


def default_responder(request: AgentRequest) -> str:
    return (
        "Ask users how they would feel if they could no longer use the product [S1].\n\n"
        "- **Use the 40 percent bar.** Rahul Vohra says at least 40 percent of users answering very "
        "disappointed signals product-market fit [S1].\n"
        "**Try this:** run the survey this week."
    )


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = dict(
        app_env="test",
        database_url=TEST_DATABASE_URL,
        log_format="console",
        log_level="WARNING",
        auto_ingest=False,
        transcripts_dir=str(FIXTURE_TRANSCRIPTS),
        llm_provider="ollama",
        llm_fallback_provider=None,
        anthropic_api_key=None,
        ollama_auto_provision=False,
        embeddings_enabled=False,
        static_dir=str(BACKEND_DIR / "no-static"),
        llm_timeout_seconds=30,
    )
    values.update(overrides)
    return Settings(**values)


def set_provider_state(app: Any, name: str, available: bool) -> None:
    """Pin a provider's health without touching the network."""
    registry = app.state.providers
    status = ProviderStatus(
        name=name,  # type: ignore[arg-type]
        label="Ollama (local)" if name == "ollama" else "Claude (cloud)",
        kind="local" if name == "ollama" else "cloud",
        model="test-model",
        available=available,
        state="ready" if available else "unavailable",
        detail=None if available else "simulated outage",
        hint=None if available else "start it",
    )

    async def _status(*_: Any, **__: Any) -> ProviderStatus:
        return status

    if name == "ollama":
        registry._ollama_status = _status
    else:
        registry._anthropic_status = _status
    registry.invalidate(name)


# --------------------------------------------------------------------------- database


def _database_reachable() -> bool:
    import asyncio

    import asyncpg

    url = TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    admin_url = url.rsplit("/", 1)[0] + "/postgres"
    db_name = url.rsplit("/", 1)[1]

    async def _reset() -> None:
        conn = await asyncpg.connect(admin_url, timeout=3)
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
            await conn.execute(f'CREATE DATABASE "{db_name}"')
        finally:
            await conn.close()

    try:
        asyncio.run(_reset())
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def database_url() -> str:
    if not _database_reachable():
        pytest.skip(f"PostgreSQL not reachable at {TEST_DATABASE_URL} (start it with `docker compose up -d db`)")
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND_DIR, env=env, check=True,
        capture_output=True,
    )
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def settings(database_url: str) -> Settings:
    return make_settings(database_url=database_url)


@pytest.fixture(scope="session")
async def session_factory(settings: Settings):
    from app.db.session import create_engine, create_session_factory

    engine = create_engine(settings)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture(scope="session")
async def kb(session_factory, settings: Settings):
    from app.retrieval.ingest import ingest

    return await ingest(session_factory, settings, trigger="test")


@pytest.fixture(scope="session")
def fake_runtime() -> FakeRuntime:
    return FakeRuntime()


@pytest.fixture(scope="session")
async def app(settings: Settings, kb, fake_runtime: FakeRuntime):
    from app.main import create_app

    application = create_app(settings, runtime=fake_runtime)
    async with application.router.lifespan_context(application):
        set_provider_state(application, "ollama", True)
        set_provider_state(application, "anthropic", False)
        yield application


@pytest.fixture
async def client(app) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"X-User-Id": "test-user-0001"}, timeout=30
    ) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_fake(request: pytest.FixtureRequest) -> None:
    if "fake_runtime" in request.fixturenames or "client" in request.fixturenames:
        runtime: FakeRuntime = request.getfixturevalue("fake_runtime")
        runtime.responder = default_responder
        runtime.error = None
        runtime.requests.clear()
