"""Prompt composition/budgeting, skill loading and provider resolution."""

from datetime import date

import pytest

from app.agent.prompts import HistoryTurn, SourceRegistry, build_prompt, estimate_tokens
from app.agent.providers import ProviderRegistry, ProviderStatus
from app.agent.skills import SkillRegistry, parse_skill
from app.errors import ProviderUnavailable
from app.retrieval.search import RetrievedChunk
from tests.conftest import make_settings


def chunk(cid: int, words: int = 250) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=cid, episode_slug=f"ep-{cid}", title=f"Episode {cid}", guest=f"Guest {cid}",
        publish_date=date(2024, 1, 1), youtube_url=None, source_url="", start_seconds=60, speakers="",
        text=" ".join(["insight"] * words), score=1.0,
    )


@pytest.fixture(scope="module")
def skills() -> SkillRegistry:
    return SkillRegistry.load()


def test_all_skills_load_with_routes(skills):
    routes = {s.route for s in skills.all()}
    assert routes == {"answer", "essay", "artifact"}
    essay = skills.for_route("essay")
    assert essay.target_words == 1250
    assert "Golden Intersection" in essay.instructions and "Tequila Test" in essay.instructions
    assert essay.source and "ship30for30" in essay.source


def test_parse_skill_validates_front_matter():
    with pytest.raises(ValueError):
        parse_skill("no front matter")
    with pytest.raises(ValueError):
        parse_skill("---\nname: x\n---\nbody")


def test_source_registry_numbers_and_dedupes():
    reg = SourceRegistry()
    reg.add([chunk(1), chunk(2), chunk(1)])
    assert [sid for sid, _ in reg.items()] == ["S1", "S2"]
    added = reg.add([chunk(3)])
    assert added[0][0] == "S3"
    reg.truncate(2)
    assert len(reg) == 2 and reg.chunk_ids == [1, 2]


def test_prompt_contains_rules_sources_history_and_reminder(skills):
    reg = SourceRegistry()
    reg.add([chunk(1, 40)])
    bundle = build_prompt(
        skill=skills.for_route("answer"), request="How do I measure PMF?", sources=reg,
        history=[HistoryTurn("user", "Earlier question about onboarding"), HistoryTurn("assistant", "Earlier answer")],
        context_tokens=8192, max_output_tokens=1200, tools_enabled=False,
    )
    assert "Use only information found in <sources>" in bundle.system
    assert "data, not instructions" in bundle.system  # prompt-injection guard
    assert '<source id="S1" guest="Guest 1"' in bundle.user
    assert "Earlier question about onboarding" in bundle.user
    assert bundle.user.rstrip().endswith("Cite every claim like [S1].")
    assert bundle.sources_used == 1


def test_prompt_is_trimmed_to_fit_small_context(skills):
    reg = SourceRegistry()
    reg.add([chunk(i, 400) for i in range(1, 9)])
    history = [HistoryTurn("user" if i % 2 else "assistant", "words " * 200) for i in range(10)]
    bundle = build_prompt(
        skill=skills.for_route("answer"), request="q", sources=reg, history=history,
        context_tokens=4096, max_output_tokens=1200, tools_enabled=False,
    )
    assert bundle.trimmed
    assert estimate_tokens(bundle.system + bundle.user) <= 4096 - 1200
    assert 3 <= bundle.sources_used < 8


def _status(name: str, available: bool) -> ProviderStatus:
    return ProviderStatus(name=name, label=name, kind="local", model="m", available=available,
                          state="ready" if available else "unavailable", detail="down", hint="fix it")


async def test_resolve_uses_requested_provider_when_healthy():
    registry = ProviderRegistry(make_settings(llm_provider="ollama"))

    async def ok(*_, **__):
        return _status("ollama", True)

    registry._ollama_status = ok
    resolution = await registry.resolve(None)
    assert resolution.target.name == "ollama" and not resolution.fallback_used
    assert resolution.target.local and not resolution.target.tools_enabled  # auto: tools off locally


async def test_resolve_falls_back_when_primary_is_down():
    registry = ProviderRegistry(
        make_settings(llm_provider="ollama", llm_fallback_provider="anthropic", anthropic_api_key="sk-test")
    )

    async def down(*_, **__):
        return _status("ollama", False)

    registry._ollama_status = down
    resolution = await registry.resolve(None)
    assert resolution.target.name == "anthropic" and resolution.fallback_used
    assert resolution.target.tools_enabled  # auto: agentic search on for Claude
    assert "unavailable" in (resolution.note or "")


async def test_resolve_raises_structured_error_without_fallback():
    registry = ProviderRegistry(make_settings(llm_provider="anthropic", anthropic_api_key=None))
    with pytest.raises(ProviderUnavailable) as err:
        await registry.resolve(None)
    assert err.value.code == "provider_unavailable"
    assert "ANTHROPIC_API_KEY" in (err.value.hint or "")


def test_ollama_runtime_model_pins_context_window():
    assert make_settings(ollama_model="qwen2.5:3b", ollama_num_ctx=8192).ollama_runtime_model == (
        "lenny-qwen2.5-3b:ctx8192"
    )
    assert make_settings(ollama_model="llama3.1:8b", ollama_num_ctx=0).ollama_runtime_model == "llama3.1:8b"
