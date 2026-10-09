"""Agent runtime built on the Anthropic Claude Agent SDK.

One code path serves both providers: the SDK drives the Claude Code agent
loop, and for the local provider it is pointed at Ollama's
Anthropic-compatible Messages API via ANTHROPIC_BASE_URL.

Hardening applied to every run:
* **No built-in tools** (`tools=[]`): the agent cannot run shell commands,
  read files or browse. The only tool it may get is our in-process
  `search_transcripts` MCP tool.
* **Isolation**: `setting_sources=[]`, a private CLAUDE_CONFIG_DIR, no session
  persistence and strict MCP config, so nothing from the host's Claude setup
  (logins, MCP servers, CLAUDE.md, hooks) leaks into the app.
* **Bounded**: max turns, output-token cap, wall-clock timeout and a global
  concurrency semaphore (a CPU-bound local model can only serve so many).
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import structlog

from app.agent.providers import ProviderTarget
from app.errors import ModelError, ModelTimeout, ProviderUnavailable

log = structlog.get_logger("agent")

SearchFn = Callable[[str], Awaitable[str]]


@dataclass
class AgentRequest:
    target: ProviderTarget
    system_prompt: str
    prompt: str
    max_output_tokens: int
    timeout_seconds: float
    search_tool: SearchFn | None = None


@dataclass
class AgentEvent:
    """`delta` (text), `reset` (discard streamed draft, a tool call follows),
    `tool_call` (name, input), `final` (text, usage)."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)


class AgentRuntime(Protocol):
    def run(self, request: AgentRequest) -> AsyncIterator[AgentEvent]: ...


class ClaudeAgentRuntime:
    def __init__(self, max_concurrency: int = 2, config_dir: str | None = None) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._config_dir = Path(config_dir or Path.home() / ".lenny-assistant" / "claude-agent")
        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._workdir = self._config_dir / "workdir"
        self._workdir.mkdir(parents=True, exist_ok=True)

    def _env(self, target: ProviderTarget, max_output_tokens: int) -> dict[str, str]:
        env = {
            "CLAUDE_CONFIG_DIR": str(self._config_dir),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
            "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1",
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(max_output_tokens),
            # Never inherit a host login/key for the wrong provider.
            "ANTHROPIC_API_KEY": "",
            "ANTHROPIC_AUTH_TOKEN": "",
            "ANTHROPIC_BASE_URL": "",
        }
        if target.local:
            env.update(
                {
                    "ANTHROPIC_BASE_URL": target.base_url or "",
                    "ANTHROPIC_AUTH_TOKEN": "ollama",
                    # Background helper calls must also stay on the local model.
                    "ANTHROPIC_DEFAULT_HAIKU_MODEL": target.runtime_model,
                    "ANTHROPIC_DEFAULT_SONNET_MODEL": target.runtime_model,
                    "ANTHROPIC_DEFAULT_OPUS_MODEL": target.runtime_model,
                    "ANTHROPIC_SMALL_FAST_MODEL": target.runtime_model,
                }
            )
        else:
            env["ANTHROPIC_API_KEY"] = target.api_key or ""
            if target.base_url:
                env["ANTHROPIC_BASE_URL"] = target.base_url
            else:
                env.pop("ANTHROPIC_BASE_URL")
        return env

    def _options(self, request: AgentRequest) -> Any:
        from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, tool

        target = request.target
        mcp_servers: dict[str, Any] = {}
        allowed: list[str] = []
        if request.search_tool is not None:
            search = request.search_tool

            @tool(
                "search_transcripts",
                "Search Lenny's Podcast transcripts for more evidence. Input: a focused search query "
                "(e.g. 'Superhuman product-market fit survey'). Returns numbered excerpts to cite.",
                {"query": str},
            )
            async def search_transcripts(args: dict[str, Any]) -> dict[str, Any]:
                text = await search(str(args.get("query", "")))
                return {"content": [{"type": "text", "text": text}]}

            mcp_servers["kb"] = create_sdk_mcp_server("kb", tools=[search_transcripts])
            allowed = ["mcp__kb__search_transcripts"]

        return ClaudeAgentOptions(
            model=target.runtime_model,
            system_prompt=request.system_prompt,
            tools=[],
            allowed_tools=allowed,
            mcp_servers=mcp_servers,
            strict_mcp_config=True,
            max_turns=4 if allowed else 1,
            setting_sources=[],
            include_partial_messages=True,
            cwd=str(self._workdir),
            env=self._env(target, request.max_output_tokens),
            extra_args={"no-session-persistence": None},
            stderr=lambda line: log.debug("agent_cli_stderr", line=line[:300]),
        )

    async def run(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeSDKError,
            CLIConnectionError,
            ResultMessage,
            TextBlock,
            ToolUseBlock,
            query,
        )
        from claude_agent_sdk.types import StreamEvent

        target = request.target
        started = time.perf_counter()
        first_token_ms: int | None = None
        streamed_any_text = False
        options = self._options(request)

        async with self._semaphore:
            queue_wait_ms = int((time.perf_counter() - started) * 1000)
            log.info(
                "agent_run_started",
                provider=target.name,
                model=target.runtime_model,
                tools=bool(request.search_tool),
                queue_wait_ms=queue_wait_ms,
            )
            stream = query(prompt=request.prompt, options=options)
            iterator = stream.__aiter__()
            deadline = time.monotonic() + request.timeout_seconds
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError
                    try:
                        message = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
                    except StopAsyncIteration:
                        break

                    if isinstance(message, StreamEvent):
                        event = message.event
                        etype = event.get("type")
                        if etype == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta" and delta.get("text"):
                                if first_token_ms is None:
                                    first_token_ms = int((time.perf_counter() - started) * 1000)
                                streamed_any_text = True
                                yield AgentEvent("delta", {"text": delta["text"]})
                        elif etype == "content_block_start":
                            block = event.get("content_block", {})
                            if block.get("type") == "tool_use":
                                yield AgentEvent("reset")
                    elif isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, ToolUseBlock):
                                yield AgentEvent(
                                    "tool_call",
                                    {"name": block.name.removeprefix("mcp__kb__"), "input": block.input},
                                )
                            elif isinstance(block, TextBlock) and not streamed_any_text:
                                # Provider did not stream partials; emit the block at once.
                                if first_token_ms is None:
                                    first_token_ms = int((time.perf_counter() - started) * 1000)
                                yield AgentEvent("delta", {"text": block.text})
                    elif isinstance(message, ResultMessage):
                        if message.is_error:
                            raise _result_error(message, target)
                        usage = message.usage or {}
                        yield AgentEvent(
                            "final",
                            {
                                "text": message.result or "",
                                "num_turns": message.num_turns,
                                "input_tokens": usage.get("input_tokens"),
                                "output_tokens": usage.get("output_tokens"),
                                "cost_usd": message.total_cost_usd if not target.local else 0.0,
                                "ttft_ms": first_token_ms,
                                "model_ms": int((time.perf_counter() - started) * 1000),
                                "queue_wait_ms": queue_wait_ms,
                            },
                        )
            except TimeoutError as exc:
                log.warning("agent_timeout", provider=target.name, timeout_s=request.timeout_seconds)
                raise ModelTimeout(
                    f"The model did not finish within {int(request.timeout_seconds)}s.",
                    hint="Local models on CPU are slow for long outputs. Raise LLM_TIMEOUT_SECONDS, use a "
                    "smaller request, or switch provider.",
                    details={"provider": target.name},
                ) from exc
            except CLIConnectionError as exc:
                raise ProviderUnavailable(
                    "Could not start the agent runtime (Claude Agent SDK CLI).",
                    hint="Reinstall dependencies (pip install -r requirements.txt); see logs for the CLI error.",
                    details={"error": str(exc)[:300]},
                ) from exc
            except ClaudeSDKError as exc:
                raise ModelError(
                    f"The agent runtime failed: {str(exc)[:200]}",
                    hint="Check provider status in the header and the app logs for this request_id.",
                    details={"provider": target.name},
                ) from exc
            finally:
                closer = getattr(iterator, "aclose", None)
                if closer is not None:
                    try:
                        await asyncio.wait_for(closer(), timeout=5)
                    except Exception as exc:  # best effort cleanup of the CLI subprocess
                        log.debug("agent_stream_close_failed", error=str(exc)[:200])
            log.info(
                "agent_run_finished",
                provider=target.name,
                ttft_ms=first_token_ms,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )


def _result_error(message: Any, target: ProviderTarget) -> Exception:
    status = getattr(message, "api_error_status", None)
    errors = "; ".join(getattr(message, "errors", None) or [])[:300] or (message.result or "")[:300]
    if status in (401, 403):
        return ProviderUnavailable(
            f"{target.name} rejected the credentials (HTTP {status}).",
            hint="Check ANTHROPIC_API_KEY." if not target.local else "Check OLLAMA_BASE_URL.",
            details={"provider": target.name, "status": status},
        )
    if status == 404:
        return ProviderUnavailable(
            f"Model '{target.runtime_model}' was not found by {target.name}.",
            hint="Check the model name in .env (ANTHROPIC_MODEL / OLLAMA_MODEL).",
            details={"provider": target.name, "status": status},
        )
    if status in (429, 529):
        return ModelError(
            f"{target.name} is rate-limited or overloaded (HTTP {status}).",
            hint="Retry in a moment or switch provider.",
            details={"provider": target.name, "status": status},
        )
    return ModelError(
        f"The model returned an error{f' (HTTP {status})' if status else ''}: {errors}",
        details={"provider": target.name, "status": status, "subtype": message.subtype},
    )


def default_config_dir() -> str:
    return os.environ.get("AGENT_CONFIG_DIR") or str(Path.home() / ".lenny-assistant" / "claude-agent")
