"""Chat orchestration: one user turn end-to-end.

    persist user msg -> route -> resolve provider -> retrieve -> (guardrails)
    -> compose prompt -> run agent (stream) -> post-process -> persist -> done

Every step emits events (for SSE) and structured logs. Failure handling:

* no / weak evidence     -> honest "not covered" answer without calling the model
* provider unavailable   -> fallback provider if configured, else degraded mode
* model error / timeout  -> fallback if nothing was streamed yet, else degraded
* degraded mode          -> the most relevant passages with citations, so the
                            user still gets grounded value and a clear reason
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import repositories as repo
from app.agent.postprocess import attribute_blocks, check_essay, extract_artifact, process_citations
from app.agent.prompts import HistoryTurn, SourceRegistry, build_prompt, format_sources
from app.agent.providers import ProviderRegistry, ProviderTarget, Resolution
from app.agent.router import Route, RouteDecision, route_message
from app.agent.runtime import AgentRequest, AgentRuntime
from app.agent.skills import Skill, SkillRegistry
from app.artifacts.sanitize import sanitize_html
from app.config import ProviderName, Settings
from app.db.models import Artifact, Message
from app.errors import AppError, ProviderUnavailable
from app.retrieval.search import RetrievedChunk, Retriever, build_retrieval_query, focus_excerpt
from app.serializers import artifact_out, message_out
from app.services.essay import SectionedEssayWriter

log = structlog.get_logger("chat")

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]

# Measured on the real corpus: 16 in-domain questions scored >= 0.79 (14 of them 1.0);
# off-topic probes mostly 0.33-0.65. See docs/architecture.md section 6.
MIN_COVERAGE = 0.72  # below: decline without calling the model
WEAK_COVERAGE = 0.9  # below: tell the model the evidence is weak
LOCAL_EXCERPT_WORDS = 170

CHITCHAT_REPLY = """Hi! I'm the **Lenny Growth Assistant**. I answer product and growth questions using \
only Lenny's Podcast transcripts ({episodes} episodes), and every claim links to the exact episode and \
timestamp it came from.

Try one of these:
- *How does Superhuman measure product-market fit?*
- *What do guests recommend for improving onboarding activation?*
- *Turn that into a Ship 30 essay* (a ~1,250-word essay, opened in the Artifact Viewer)
- *Make an HTML one-pager of the key tactics*"""

NOT_COVERED_REPLY = """**The transcripts I have don't cover this.** I searched {episodes} Lenny's Podcast \
episodes and found no passages that address *"{query}"* closely enough to answer without guessing.

I only answer from the transcripts, so I won't fill the gap from general knowledge. You could try:
- rephrasing with the product or growth concept you care about (e.g. *activation*, *pricing*, *retention*)
- naming a guest or company (e.g. *What did Brian Chesky say about founder mode?*)"""

COMMAND_WORDS = frozenset(
    """turn convert make create write draft generate build design produce give into as an a the this that
    these those it essay ship 30 article post blog newsletter one pager one-pager onepager html markdown md
    key main top best tactics tips ideas lessons summary summarise summarize overview visual styled web landing
    doc document page artifact cheat sheet checklist table please can you for me us of from above previous
    answer conversation version""".split()
)
REFERENTIAL_RE = re.compile(r"\b(this|that|these|those|it|above|previous|conversation|answer|so far)\b", re.I)


@dataclass
class TurnResult:
    user_message: dict[str, Any]
    assistant_message: dict[str, Any]
    artifact: dict[str, Any] | None


def _title_from(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= 60:
        return text
    cut = text[:60].rsplit(" ", 1)[0]
    return f"{cut}…"


def _topic_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-z0-9-]+", text.lower()) if w not in COMMAND_WORDS]


CITATION_MARKER_RE = re.compile(r"\[S(\d+)\]")


def _is_model_context(message: Message) -> bool:
    """Canned system replies (declines, help text, evidence-only) are not real
    conversation: a small model shown them imitates the decline on later turns."""
    if message.role == "user":
        return True
    if message.status != "complete" or message.route == "chitchat":
        return False
    return (message.quality or {}).get("reason") != "no_evidence"


def strip_citations(text: str) -> str:
    return CITATION_MARKER_RE.sub("", text)


def remap_citations(text: str, old_sources: list[dict[str, Any]], registry: SourceRegistry) -> str:
    """Rewrite [S#] markers from a previous turn's numbering to this turn's,
    via chunk ids. Markers whose passage is not in this turn's evidence are dropped."""
    old_to_chunk = {s.get("id"): s.get("chunk_id") for s in old_sources}
    chunk_to_new = {chunk.chunk_id: sid for sid, chunk in registry.items()}

    def _sub(match: re.Match[str]) -> str:
        new = chunk_to_new.get(old_to_chunk.get(f"S{match.group(1)}"))
        return f"[{new}]" if new else ""

    return CITATION_MARKER_RE.sub(_sub, text)


def _overlaps(topic: list[str], previous: str) -> bool:
    """Does the new request talk about the same thing as the previous turn?"""
    prev = {w[:5] for w in re.findall(r"[a-z0-9]+", previous.lower()) if len(w) >= 4}
    return any(w[:5] in prev for w in topic if len(w) >= 4)


def _ground(
    text: str, valid_ids: set[str], source_texts: dict[str, str] | None
) -> tuple[str, list[str], dict[str, Any]]:
    """Validate model citations, then (for Markdown/chat text) run the
    deterministic attribution check. Returns (content, cited ids, quality)."""
    report = process_citations(text, valid_ids)
    quality = report.as_quality()
    content = report.content
    if source_texts:
        attribution = attribute_blocks(content, source_texts)
        content = attribution.content
        quality.update(attribution.as_quality())
        final = process_citations(content, valid_ids)
        quality["cited_sources"] = len(final.cited)
        quality["citation_coverage"] = round(final.coverage, 2)
        if quality["answer_type"] == "uncited" and final.cited:
            quality["answer_type"] = "grounded"
        return content, final.cited, quality
    return content, report.cited, quality


def _degraded_content(reason: str, registry: SourceRegistry) -> str:
    lines = [
        f"**I couldn't reach the language model** ({reason}). Here are the most relevant transcript "
        "passages I found, so you can still read the evidence directly:\n"
    ]
    for sid, chunk in registry.items()[:5]:
        words = chunk.text.split()
        excerpt = " ".join(words[:45]) + (" …" if len(words) > 45 else "")
        lines.append(f'- **{chunk.guest}**, *{chunk.title}*: "{excerpt}" [{sid}]')
    if not len(registry):
        lines.append("_No matching passages were found either._")
    return "\n".join(lines)


class ChatService:
    def __init__(
        self,
        settings: Settings,
        factory: async_sessionmaker[AsyncSession],
        retriever: Retriever,
        providers: ProviderRegistry,
        runtime: AgentRuntime,
        skills: SkillRegistry,
    ) -> None:
        self.settings = settings
        self.factory = factory
        self.retriever = retriever
        self.providers = providers
        self.runtime = runtime
        self.skills = skills

    # ------------------------------------------------------------------ public
    async def run_turn(
        self,
        *,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        content: str,
        mode: str = "auto",
        provider: ProviderName | None = None,
        emit: Emit,
    ) -> TurnResult:
        started = time.perf_counter()
        structlog.contextvars.bind_contextvars(session_id=str(session_id))

        # 1. Persist the user message first: it is never lost, even if the model fails.
        async with self.factory() as db:
            user = await db.get(repo.User, user_id)
            session = await repo.get_session(db, user, session_id)
            history = await repo.session_messages(db, session.id, limit=12)
            current_artifact = await repo.latest_artifact(db, session.id)
            artifact_sources: list[dict[str, Any]] = []
            if current_artifact is not None and current_artifact.message_id is not None:
                producer = await db.get(Message, current_artifact.message_id)
                artifact_sources = producer.sources if producer else []
            user_msg = await repo.add_message(db, session_id=session.id, role="user", content=content)
            new_title = _title_from(content) if not history and session.title == "New chat" else None
            await repo.touch_session(db, session, title=new_title)
            await db.commit()
            user_out = message_out(user_msg)
        await emit("user_message", user_out)

        # 2. Route.
        decision = route_message(content, mode, has_artifact=current_artifact is not None)  # type: ignore[arg-type]
        log.info("routed", route=decision.route.value, reason=decision.reason, mode=mode)
        if decision.route is Route.CHITCHAT:
            episodes = await self._episode_count()
            return await self._finish(
                session_id=session.id,
                user_out=user_out,
                content=CHITCHAT_REPLY.format(episodes=episodes),
                decision=decision,
                emit=emit,
                metrics={"total_ms": int((time.perf_counter() - started) * 1000)},
            )

        skill = self.skills.for_route(decision.route.value)
        if decision.revises_artifact and decision.artifact_kind is None and current_artifact is not None:
            decision = RouteDecision(decision.route, decision.reason, current_artifact.kind, True)

        # 3. Resolve the provider (fallback-aware). Failure is handled after retrieval
        #    so that degraded mode can still show evidence.
        resolution: Resolution | None = None
        provider_error: AppError | None = None
        try:
            resolution = await self.providers.resolve(provider)
        except ProviderUnavailable as exc:
            provider_error = exc

        # 4. Retrieve evidence.
        await emit("status", {"stage": "retrieving", "label": "Searching transcripts"})
        registry, retrieval = await self._retrieve(content, history, decision, skill, resolution)
        best_coverage = max((c.coverage for _, c in registry.items()), default=0.0)

        base_meta = {
            "route": decision.route.value,
            "route_reason": decision.reason,
            "skill": f"{skill.name}@{skill.version}",
            "provider": resolution.target.name if resolution else provider or self.settings.llm_provider,
            "model": resolution.target.model if resolution else None,
            "fallback_used": bool(resolution and resolution.fallback_used),
            "fallback_note": resolution.note if resolution else None,
            "sources": registry.to_sources(),
            "retrieval": retrieval,
        }
        await emit("meta", base_meta)
        metrics: dict[str, Any] = {"retrieval_ms": retrieval["latency_ms"], "best_coverage": round(best_coverage, 3)}

        # 5. Guardrail: nothing relevant -> do not ask the model to improvise.
        if not len(registry) or best_coverage < MIN_COVERAGE:
            log.info("no_evidence", best_coverage=best_coverage, sources=len(registry))
            registry = SourceRegistry()
            metrics["total_ms"] = int((time.perf_counter() - started) * 1000)
            return await self._finish(
                session_id=session.id,
                user_out=user_out,
                content=NOT_COVERED_REPLY.format(
                    episodes=await self._episode_count(), query=" ".join(content.split())[:160]
                ),
                decision=decision,
                emit=emit,
                metrics=metrics,
                quality={"answer_type": "not_covered", "reason": "no_evidence", "best_coverage": best_coverage},
                resolution=resolution,
            )

        if provider_error is not None:
            return await self._degraded(session.id, user_out, decision, registry, provider_error, emit, metrics)

        # 6. Run the agent (with one fallback attempt if nothing streamed yet).
        assert resolution is not None
        attempts: list[Resolution] = [resolution]
        fallback = self.settings.llm_fallback_provider
        if fallback and fallback != resolution.target.name:
            attempts.append(Resolution(self.providers.target(fallback), resolution.requested, True))

        last_error: AppError | None = None
        for attempt_no, attempt in enumerate(attempts):
            if attempt_no > 0:
                status = await self.providers.status(attempt.target.name)
                if not status.available:
                    break
                await emit(
                    "status",
                    {"stage": "fallback", "label": f"Retrying with {status.label}", "provider": attempt.target.name},
                )
            try:
                outcome = await self._generate(
                    attempt,
                    skill,
                    decision,
                    content,
                    history,
                    registry,
                    current_artifact,
                    artifact_sources,
                    best_coverage,
                    emit,
                )
            except AppError as exc:
                last_error = exc
                log.warning("generation_failed", provider=attempt.target.name, code=exc.code, error=exc.message)
                if exc.details.get("streamed"):
                    break
                continue
            metrics.update(outcome["metrics"])
            metrics["total_ms"] = int((time.perf_counter() - started) * 1000)
            return await self._finish(
                session_id=session.id,
                user_out=user_out,
                content=outcome["content"],
                decision=decision,
                emit=emit,
                metrics=metrics,
                quality=outcome["quality"],
                registry=registry,
                citations=outcome["citations"],
                resolution=attempt,
                artifact=outcome.get("artifact"),
                previous_artifact=current_artifact if decision.revises_artifact else None,
            )

        assert last_error is not None
        return await self._degraded(session.id, user_out, decision, registry, last_error, emit, metrics)

    # ------------------------------------------------------------------ steps
    async def _episode_count(self) -> int:
        from sqlalchemy import func, select

        from app.db.models import Episode

        async with self.factory() as db:
            return int((await db.execute(select(func.count()).select_from(Episode))).scalar() or 0)

    async def _retrieve(
        self,
        content: str,
        history: list[Message],
        decision: RouteDecision,
        skill: Skill,
        resolution: Resolution | None,
    ) -> tuple[SourceRegistry, dict[str, Any]]:
        local = resolution.target.local if resolution else True
        top_k = skill.top_k_local if local else skill.top_k
        previous_questions = [m.content for m in history if m.role == "user"]
        query = build_retrieval_query(content, previous_questions)

        # Essays/artifacts: search the *topic*, never the format words ("html",
        # "one-pager" are rare in transcripts and would dominate IDF), and reuse
        # the evidence behind the previous answer when the request builds on it.
        carried: list[RetrievedChunk] = []
        if decision.route in (Route.ESSAY, Route.ARTIFACT):
            topic = _topic_words(content)
            query = " ".join(topic) or content
            last_answer = next((m for m in reversed(history) if m.role == "assistant" and m.sources), None)
            builds_on_previous = last_answer is not None and (
                len(topic) < 3
                or REFERENTIAL_RE.search(content) is not None
                or _overlaps(topic, f"{previous_questions[-1] if previous_questions else ''} {last_answer.content}")
            )
            if builds_on_previous and last_answer is not None:
                # Keep the stored order so carried evidence keeps its S-numbers where possible;
                # any renumbering is handled by remap_citations() on the current artifact.
                carried = [RetrievedChunk.from_source(s) for s in last_answer.sources]
                if previous_questions:
                    query = f"{previous_questions[-1]} {' '.join(topic)}".strip()

        async with self.factory() as db:
            result = await self.retriever.search(
                db,
                query,
                top_k=top_k,
                max_per_episode=self.settings.retrieval_max_per_episode,
                exclude_chunk_ids=[c.chunk_id for c in carried],
                expand_neighbors=True,
            )
            if decision.route is Route.ANSWER and query != content:
                # The follow-up merge is a heuristic: if the message alone retrieves
                # better evidence (a topic switch), trust that instead.
                standalone = await self.retriever.search(
                    db,
                    content,
                    top_k=top_k,
                    max_per_episode=self.settings.retrieval_max_per_episode,
                    expand_neighbors=True,
                )
                if standalone.best_coverage > result.best_coverage:
                    log.info("followup_merge_rejected", merged=result.best_coverage, alone=standalone.best_coverage)
                    query, result = content, standalone
        registry = SourceRegistry()
        if carried:
            keep = max(top_k // 2 + 1, top_k - len(result.chunks))
            registry.add(carried[:keep])
        registry.add(result.chunks[: max(0, top_k - len(registry))])
        if local:
            # CPU-bound local models pay ~10 ms per prompt token: send only the
            # part of each chunk that addresses the question.
            for _, chunk in registry.items():
                chunk.text = focus_excerpt(chunk.text, query, LOCAL_EXCERPT_WORDS)
        info = {
            "query": query,
            "expanded_query": result.expanded_query,
            "mode": result.mode,
            "latency_ms": result.latency_ms,
            "candidates": result.candidates,
            "carried_from_previous_answer": min(len(carried), len(registry)),
        }
        return registry, info

    async def _generate(
        self,
        resolution: Resolution,
        skill: Skill,
        decision: RouteDecision,
        content: str,
        history: list[Message],
        registry: SourceRegistry,
        current_artifact: Artifact | None,
        artifact_sources: list[dict[str, Any]],
        best_coverage: float,
        emit: Emit,
    ) -> dict[str, Any]:
        target = resolution.target
        if decision.route is Route.ESSAY and self._sectioned_essay(target):
            writer = SectionedEssayWriter(self.runtime, emit, self.settings.llm_timeout_seconds)
            started = time.perf_counter()
            try:
                output = await writer.write(target, skill, content, registry)
            except AppError as exc:
                exc.details["streamed"] = writer.calls > 1
                raise
            metrics: dict[str, Any] = {
                "strategy": "sectioned",
                "model_calls": writer.calls,
                "ttft_ms": writer.first_token_ms,
                "model_ms": int((time.perf_counter() - started) * 1000),
            }
        else:
            output, metrics = await self._single_pass(
                target,
                skill,
                decision,
                content,
                history,
                registry,
                current_artifact,
                artifact_sources,
                best_coverage,
                emit,
            )
        return self._postprocess(output, metrics, skill, decision, registry)

    def _sectioned_essay(self, target: ProviderTarget) -> bool:
        strategy = self.settings.essay_strategy
        return strategy == "sectioned" or (strategy == "auto" and target.local)

    async def _single_pass(
        self,
        target: ProviderTarget,
        skill: Skill,
        decision: RouteDecision,
        content: str,
        history: list[Message],
        registry: SourceRegistry,
        current_artifact: Artifact | None,
        artifact_sources: list[dict[str, Any]],
        best_coverage: float,
        emit: Emit,
    ) -> tuple[str, dict[str, Any]]:
        max_out = min(skill.max_output_tokens, target.max_output_tokens)
        artifact_text = None
        if decision.revises_artifact and current_artifact is not None:
            # Old [S#] markers refer to the numbering of the turn that produced the artifact.
            artifact_text = remap_citations(current_artifact.content, artifact_sources, registry)
        bundle = build_prompt(
            skill=skill,
            request=content,
            sources=registry,
            # Past answers' [S#] markers use a different numbering: strip them.
            history=[HistoryTurn(m.role, strip_citations(m.content)) for m in history if _is_model_context(m)],
            context_tokens=target.context_tokens,
            max_output_tokens=max_out,
            tools_enabled=target.tools_enabled,
            weak_evidence=best_coverage < WEAK_COVERAGE,
            artifact_kind=decision.artifact_kind,
            current_artifact=artifact_text,
        )
        registry.truncate(bundle.sources_used)
        if bundle.trimmed.get("source_words"):
            # Store, show and attribute against exactly the excerpt the model saw.
            limit = bundle.trimmed["source_words"]
            for _, chunk in registry.items():
                words = chunk.text.split(" ")
                if len(chunk.text.split()) > limit:
                    chunk.text = " ".join(words[:limit]) + " ..."
        log.info(
            "prompt_built",
            skill=skill.name,
            est_tokens=bundle.estimated_tokens,
            sources=bundle.sources_used,
            trimmed=bundle.trimmed or None,
        )

        tool_calls: list[dict[str, Any]] = []

        async def search_tool(query: str) -> str:
            async with self.factory() as db:
                result = await self.retriever.search(
                    db, query, top_k=4, max_per_episode=2, exclude_chunk_ids=registry.chunk_ids
                )
            added = registry.add(result.chunks)
            tool_calls.append({"query": query, "added": len(added)})
            await emit("sources", {"sources": [c.to_source(sid) for sid, c in added]})
            return format_sources(added) if added else "No additional relevant excerpts found."

        request = AgentRequest(
            target=target,
            system_prompt=bundle.system,
            prompt=bundle.user,
            max_output_tokens=max_out,
            timeout_seconds=self.settings.llm_timeout_seconds,
            search_tool=search_tool if target.tools_enabled else None,
        )
        await emit(
            "status",
            {"stage": "generating", "label": f"Writing with {target.model}", "provider": target.name},
        )
        streamed: list[str] = []
        final: dict[str, Any] = {}
        try:
            async for event in self.runtime.run(request):
                if event.type == "delta":
                    streamed.append(event.data["text"])
                    await emit("delta", event.data)
                elif event.type == "reset":
                    streamed.clear()
                    await emit("reset", {})
                elif event.type == "tool_call":
                    query = (event.data.get("input") or {}).get("query", "")
                    await emit("status", {"stage": "searching", "label": f"Searching transcripts: {query}"})
                elif event.type == "final":
                    final = event.data
        except AppError as exc:
            exc.details["streamed"] = bool(streamed)
            raise

        output = (final.get("text") or "".join(streamed)).strip()
        if not output:
            raise AppError(
                "The model returned an empty response.",
                code="empty_response",
                status_code=502,
                retryable=True,
                details={"streamed": False},
            )

        metrics = {
            "strategy": "single",
            "ttft_ms": final.get("ttft_ms"),
            "model_ms": final.get("model_ms"),
            "queue_wait_ms": final.get("queue_wait_ms"),
            "input_tokens": final.get("input_tokens"),
            "output_tokens": final.get("output_tokens"),
            "prompt_tokens_est": bundle.estimated_tokens,
            "cost_usd": final.get("cost_usd"),
            "tool_calls": len(tool_calls),
            "prompt_trimmed": bundle.trimmed or None,
        }
        return output, metrics

    def _postprocess(
        self, output: str, metrics: dict[str, Any], skill: Skill, decision: RouteDecision, registry: SourceRegistry
    ) -> dict[str, Any]:
        valid_ids = {sid for sid, _ in registry.items()}
        source_texts = {sid: chunk.text for sid, chunk in registry.items()}

        if decision.route is Route.ANSWER:
            content_out, cited, quality = _ground(output, valid_ids, source_texts)
            return {"content": content_out, "citations": cited, "quality": quality, "metrics": metrics}

        fallback_title = "Essay" if decision.route is Route.ESSAY else "Artifact"
        if decision.route is Route.ESSAY:
            body = re.sub(r"^```(?:markdown|md)?\s*|\s*```$", "", output.strip())
            body = body[body.find("#") :] if "#" in body[:400] else body
            extracted = extract_artifact(f'<artifact type="markdown">{body}</artifact>', "markdown", fallback_title)
        else:
            extracted = extract_artifact(output, decision.artifact_kind, fallback_title)

        if extracted is None:
            content_out, cited, quality = _ground(output, valid_ids, source_texts)
            quality["artifact"] = "not_produced"
            return {"content": content_out, "citations": cited, "quality": quality, "metrics": metrics}

        # HTML is checked for citation ids only; line-level attribution applies to Markdown.
        artifact_body, cited, quality = _ground(
            extracted.content, valid_ids, source_texts if extracted.kind == "markdown" else None
        )
        quality["artifact_extraction"] = extracted.method
        title = extracted.title
        if decision.route is Route.ESSAY:
            essay = check_essay(artifact_body, skill.target_words or 1250, len(cited))
            quality["essay"] = essay.as_quality()
            passed = sum(c.passed for c in essay.checks)
            message = (
                f"Here's your Ship 30 essay: **{title}**: about {essay.word_count:,} words, "
                f"{len(cited)} transcript sources cited, {passed}/{len(essay.checks)} checklist items passed. "
                "It's open in the Artifact Viewer."
            )
        else:
            message = process_citations(extracted.message, valid_ids).content or (
                f"I created **{title}**. It's open in the Artifact Viewer."
            )
        artifact = {"kind": extracted.kind, "title": title, "content": artifact_body}
        return {
            "content": message,
            "citations": cited,
            "quality": quality,
            "metrics": metrics,
            "artifact": artifact,
        }

    async def _degraded(
        self,
        session_id: uuid.UUID,
        user_out: dict[str, Any],
        decision: RouteDecision,
        registry: SourceRegistry,
        error: AppError,
        emit: Emit,
        metrics: dict[str, Any],
    ) -> TurnResult:
        await emit("status", {"stage": "degraded", "label": "Model unavailable: showing evidence only"})
        return await self._finish(
            session_id=session_id,
            user_out=user_out,
            content=_degraded_content(error.message.rstrip("."), registry),
            decision=decision,
            emit=emit,
            metrics=metrics,
            quality={"answer_type": "degraded"},
            registry=registry,
            citations=[sid for sid, _ in registry.items()[:5]],
            status="degraded",
            error={"code": error.code, "message": error.message, "hint": error.hint, "retryable": error.retryable},
        )

    async def _finish(
        self,
        *,
        session_id: uuid.UUID,
        user_out: dict[str, Any],
        content: str,
        decision: RouteDecision,
        emit: Emit,
        metrics: dict[str, Any],
        quality: dict[str, Any] | None = None,
        registry: SourceRegistry | None = None,
        citations: list[str] | None = None,
        resolution: Resolution | None = None,
        artifact: dict[str, Any] | None = None,
        previous_artifact: Artifact | None = None,
        status: str = "complete",
        error: dict[str, Any] | None = None,
    ) -> TurnResult:
        async with self.factory() as db:
            message = await repo.add_message(
                db,
                session_id=session_id,
                role="assistant",
                content=content,
                status=status,
                route=decision.route.value,
                route_reason=decision.reason[:200],
                provider=resolution.target.name if resolution else None,
                model=resolution.target.model if resolution else None,
                sources=registry.to_sources() if registry else [],
                citations=citations or [],
                quality=quality or {},
                metrics={k: v for k, v in metrics.items() if v is not None},
                error=error,
            )
            artifact_row: Artifact | None = None
            if artifact:
                report: dict[str, int] = {}
                if artifact["kind"] == "html":
                    report = sanitize_html(artifact["content"], artifact["title"]).report
                    if report:
                        log.info("artifact_sanitized", blocked=report)
                version = (previous_artifact.version + 1) if previous_artifact else 1
                artifact_row = Artifact(
                    session_id=session_id,
                    message_id=message.id,
                    kind=artifact["kind"],
                    title=(
                        previous_artifact.title
                        if previous_artifact and artifact["title"] in ("Artifact", "")
                        else artifact["title"]
                    )[:200],
                    content=artifact["content"],
                    version=version,
                    sanitizer_report=report,
                    created_at=repo.utcnow(),
                )
                db.add(artifact_row)
            session = await db.get(repo.ChatSession, session_id)
            await repo.touch_session(db, session)
            await db.commit()
            assistant_out = message_out(message, artifact_row.id if artifact_row else None)
            artifact_payload = artifact_out(artifact_row) if artifact_row else None

        log.info(
            "turn_completed",
            route=decision.route.value,
            status=status,
            provider=assistant_out.get("provider"),
            citations=len(citations or []),
            artifact=bool(artifact_row),
            **{k: v for k, v in metrics.items() if k in ("total_ms", "ttft_ms", "retrieval_ms")},
        )
        await emit("done", {"assistant_message": assistant_out, "artifact": artifact_payload})
        return TurnResult(user_out, assistant_out, artifact_payload)
