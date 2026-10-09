"""Model provider configuration, health and fallback.

Two providers sit behind one agent runtime (the Claude Agent SDK):

* `ollama`    - local model, mandatory for the demo. The SDK talks to Ollama's
                Anthropic-compatible Messages API (ANTHROPIC_BASE_URL).
* `anthropic` - Claude via the Anthropic API.

Selection: request override (UI dropdown) -> LLM_PROVIDER. If the chosen
provider is unhealthy and LLM_FALLBACK_PROVIDER is set and healthy, the turn
runs on the fallback and the response says so (`fallback_used`). If nothing
is available the chat service answers in *degraded mode* (retrieval only).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass

import httpx
import structlog

from app.config import ProviderName, Settings
from app.errors import ProviderUnavailable

log = structlog.get_logger("providers")

STATUS_TTL_SECONDS = 10.0
PROVISION_RETRY_SECONDS = 60.0


@dataclass
class ProviderStatus:
    name: ProviderName
    label: str
    kind: str  # local | cloud
    model: str
    available: bool
    state: str  # ready | provisioning | unavailable | not_configured
    detail: str | None = None
    hint: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ProviderTarget:
    """Everything the runtime needs to call a provider."""

    name: ProviderName
    model: str  # display model name
    runtime_model: str  # model id sent to the API
    base_url: str | None
    api_key: str | None
    max_output_tokens: int
    context_tokens: int
    tools_enabled: bool
    local: bool


@dataclass(frozen=True)
class Resolution:
    target: ProviderTarget
    requested: ProviderName
    fallback_used: bool
    note: str | None = None


class ProviderRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cache: dict[str, tuple[float, ProviderStatus]] = {}
        self._provision_task: asyncio.Task | None = None
        self._provision_state: str = "idle"  # idle | running | done | failed
        self._provision_error: str | None = None
        self._provision_failed_at = 0.0

    # ------------------------------------------------------------------ targets
    def target(self, name: ProviderName) -> ProviderTarget:
        s = self.settings
        tools = s.agent_tools == "on" or (s.agent_tools == "auto" and name == "anthropic")
        if name == "anthropic":
            return ProviderTarget(
                name="anthropic",
                model=s.anthropic_model,
                runtime_model=s.anthropic_model,
                base_url=s.anthropic_base_url,
                api_key=s.anthropic_api_key.get_secret_value() if s.anthropic_api_key else None,
                max_output_tokens=s.anthropic_max_output_tokens,
                context_tokens=200_000,
                tools_enabled=tools,
                local=False,
            )
        return ProviderTarget(
            name="ollama",
            model=s.ollama_model,
            runtime_model=s.ollama_runtime_model,
            base_url=s.ollama_base_url.rstrip("/"),
            api_key="ollama",
            max_output_tokens=s.ollama_max_output_tokens,
            context_tokens=s.ollama_num_ctx if s.ollama_num_ctx > 0 else 4096,
            tools_enabled=tools,
            local=True,
        )

    # ------------------------------------------------------------------ health
    async def status(self, name: ProviderName, *, fresh: bool = False) -> ProviderStatus:
        cached = self._cache.get(name)
        if not fresh and cached and time.monotonic() - cached[0] < STATUS_TTL_SECONDS:
            return cached[1]
        status = await (self._anthropic_status() if name == "anthropic" else self._ollama_status())
        self._cache[name] = (time.monotonic(), status)
        return status

    async def statuses(self) -> list[ProviderStatus]:
        return list(await asyncio.gather(self.status("ollama"), self.status("anthropic")))

    def invalidate(self, name: ProviderName) -> None:
        self._cache.pop(name, None)

    async def _anthropic_status(self) -> ProviderStatus:
        s = self.settings
        base = dict(name="anthropic", label="Claude (cloud)", kind="cloud", model=s.anthropic_model)
        if not s.anthropic_api_key:
            return ProviderStatus(
                **base,
                available=False,
                state="not_configured",
                detail="ANTHROPIC_API_KEY is not set.",
                hint="Add ANTHROPIC_API_KEY to .env and restart to enable the cloud provider.",
            )
        return ProviderStatus(**base, available=True, state="ready")

    async def _ollama_status(self) -> ProviderStatus:
        s = self.settings
        label = f"{s.ollama_model}" + (f" · ctx {s.ollama_num_ctx}" if s.ollama_num_ctx > 0 else "")
        base = dict(name="ollama", label="Ollama (local)", kind="local", model=label)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as client:
                resp = await client.get(f"{s.ollama_base_url.rstrip('/')}/api/tags")
                resp.raise_for_status()
                names = {m.get("name") for m in resp.json().get("models", [])}
        except Exception as exc:
            return ProviderStatus(
                **base,
                available=False,
                state="unavailable",
                detail=f"Ollama is not reachable at {s.ollama_base_url} ({type(exc).__name__}).",
                hint="Start Ollama (`ollama serve`) or set OLLAMA_BASE_URL. In Docker the default is "
                "http://host.docker.internal:11434.",
            )
        if s.ollama_runtime_model in names or _tagged(s.ollama_runtime_model) in names:
            return ProviderStatus(**base, available=True, state="ready")
        retry_ok = time.monotonic() - self._provision_failed_at > PROVISION_RETRY_SECONDS
        if s.ollama_auto_provision and self._provision_task is None and retry_ok:
            # Self-healing: Ollama may have come up after the app started.
            self.start_ollama_provisioning()
        if self._provision_state == "running" or self._provision_task is not None:
            return ProviderStatus(
                **base,
                available=False,
                state="provisioning",
                detail=f"Preparing {s.ollama_model} (download and context-window setup).",
                hint="First start downloads the model (~2 GB for qwen2.5:3b). Watch the app logs.",
            )
        base_present = s.ollama_model in names or _tagged(s.ollama_model) in names
        detail = (
            f"Model {s.ollama_runtime_model} is not created yet."
            if base_present
            else f"Model {s.ollama_model} is not pulled."
        )
        if self._provision_error:
            detail += f" Last provisioning error: {self._provision_error}"
        return ProviderStatus(
            **base,
            available=False,
            state="unavailable",
            detail=detail,
            hint=f"Run `ollama pull {s.ollama_model}` then restart, or keep OLLAMA_AUTO_PROVISION=true.",
        )

    # ------------------------------------------------------------------ resolve
    async def resolve(self, requested: ProviderName | None) -> Resolution:
        primary: ProviderName = requested or self.settings.llm_provider
        status = await self.status(primary)
        if status.available:
            return Resolution(self.target(primary), primary, fallback_used=False)

        fallback = self.settings.llm_fallback_provider
        if fallback and fallback != primary:
            fb_status = await self.status(fallback)
            if fb_status.available:
                log.warning("provider_fallback", requested=primary, fallback=fallback, reason=status.detail)
                return Resolution(
                    self.target(fallback),
                    primary,
                    fallback_used=True,
                    note=f"{status.label} unavailable ({status.detail}); answered with {fb_status.label}.",
                )
        raise ProviderUnavailable(
            f"{status.label} is unavailable: {status.detail}",
            hint=status.hint,
            details={"provider": primary, "state": status.state},
        )

    # ------------------------------------------------------------------ provisioning
    def start_ollama_provisioning(self) -> None:
        if self.settings.ollama_auto_provision and self._provision_task is None:
            self._provision_task = asyncio.create_task(self._provision_ollama())

    async def _provision_ollama(self) -> None:
        """Pull the base model if missing and create the context-pinned alias.

        Ollama serves a 4k context by default and silently truncates longer
        prompts, which breaks RAG prompts and 1,250-word essays. We create
        `lenny-<model>:ctx<N>` with num_ctx pinned instead of relying on every
        evaluator configuring OLLAMA_CONTEXT_LENGTH on their Ollama server.
        """
        s = self.settings
        url = s.ollama_base_url.rstrip("/")
        self._provision_state = "running"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(3600.0, connect=5.0)) as client:
                tags = (await client.get(f"{url}/api/tags")).json().get("models", [])
                names = {m.get("name") for m in tags}
                if s.ollama_model not in names and _tagged(s.ollama_model) not in names:
                    log.info("ollama_pull_started", model=s.ollama_model)
                    resp = await client.post(f"{url}/api/pull", json={"model": s.ollama_model, "stream": False})
                    resp.raise_for_status()
                    log.info("ollama_pull_done", model=s.ollama_model)
                if s.ollama_num_ctx > 0 and s.ollama_runtime_model not in names:
                    resp = await client.post(
                        f"{url}/api/create",
                        json={
                            "model": s.ollama_runtime_model,
                            "from": s.ollama_model,
                            "parameters": {"num_ctx": s.ollama_num_ctx},
                            "stream": False,
                        },
                    )
                    resp.raise_for_status()
                    log.info("ollama_alias_created", model=s.ollama_runtime_model, num_ctx=s.ollama_num_ctx)
            self._provision_state = "done"
            self._provision_error = None
        except Exception as exc:
            self._provision_state = "failed"
            self._provision_failed_at = time.monotonic()
            self._provision_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            log.warning("ollama_provision_failed", error=self._provision_error)
        finally:
            self.invalidate("ollama")
            self._provision_task = None


def _tagged(model: str) -> str:
    return model if ":" in model else f"{model}:latest"
