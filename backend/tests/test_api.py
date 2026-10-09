"""API contract tests: sessions, chat turns (JSON + SSE), routing, failure
modes, artifacts, persistence and ops endpoints. The LLM is faked."""

import json

import pytest

from app.agent.runtime import AgentRequest
from app.errors import ModelTimeout
from tests.conftest import set_provider_state

pytestmark = pytest.mark.db


async def new_session(client) -> str:
    resp = await client.post("/api/sessions", json={})
    assert resp.status_code == 201
    return resp.json()["id"]


async def ask(client, session_id: str, content: str, **extra) -> dict:
    resp = await client.post(
        f"/api/sessions/{session_id}/messages", json={"content": content, "stream": False, **extra}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln and not ln.startswith(":")]
        if not lines:
            continue
        name = next(ln[7:] for ln in lines if ln.startswith("event: "))
        data = "\n".join(ln[6:] for ln in lines if ln.startswith("data: "))
        events.append((name, json.loads(data)))
    return events


# ----------------------------------------------------------------- health & config


async def test_health_endpoints(client):
    live = await client.get("/health/live")
    assert live.json()["status"] == "ok"
    ready = (await client.get("/health/ready")).json()
    assert ready["status"] == "ok"
    assert ready["checks"]["database"]["ok"] is True
    assert ready["checks"]["knowledge_base"]["episodes"] == 4
    assert ready["checks"]["provider:ollama"]["ok"] is True


async def test_config_exposes_provider_toggle_and_skills(client):
    config = (await client.get("/api/config")).json()
    assert config["default_provider"] == "ollama"
    assert {p["name"] for p in config["providers"]} == {"ollama", "anthropic"}
    assert {s["route"] for s in config["skills"]} == {"answer", "essay", "artifact"}
    assert config["knowledge_base"]["episodes"] == 4


async def test_errors_use_structured_envelope(client):
    sid = await new_session(client)
    blank = await client.post(f"/api/sessions/{sid}/messages", json={"content": "   ", "stream": False})
    assert blank.status_code == 422
    body = blank.json()["error"]
    assert body["code"] == "validation_error" and body["request_id"]
    assert body["details"]["issues"][0]["loc"][-1] == "content"

    bad_mode = await client.post(f"/api/sessions/{sid}/messages", json={"content": "hi", "mode": "poem"})
    assert bad_mode.status_code == 422

    missing = await client.get("/api/sessions/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"


# ----------------------------------------------------------------- sessions & persistence


async def test_sessions_are_isolated_per_user(client, app):
    import httpx

    sid = await new_session(client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers={"X-User-Id": "someone-else"}) as other:
        assert (await other.get(f"/api/sessions/{sid}")).status_code == 404
        assert sid not in [s["id"] for s in (await other.get("/api/sessions")).json()]


async def test_turn_is_persisted_with_metadata(client, fake_runtime):
    sid = await new_session(client)
    turn = await ask(client, sid, "How does Superhuman measure product-market fit?")
    answer = turn["assistant_message"]
    assert answer["status"] == "complete"
    assert answer["route"] == "answer" and answer["provider"] == "ollama"
    assert answer["citations"] == ["S1"]
    assert answer["quality"]["answer_type"] == "grounded"
    assert answer["sources"][0]["episode_slug"] == "rahul-vohra"
    assert answer["metrics"]["retrieval_ms"] >= 0 and "total_ms" in answer["metrics"]

    detail = (await client.get(f"/api/sessions/{sid}")).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["title"].startswith("How does Superhuman")
    assert detail["messages"][0]["created_at"] < detail["messages"][1]["created_at"]
    assert len(fake_runtime.requests) == 1


async def test_follow_up_uses_session_history_and_previous_question(client, fake_runtime):
    sid = await new_session(client)
    await ask(client, sid, "How does Superhuman measure product-market fit?")
    await ask(client, sid, "What about how they improved the score?")
    prompt = fake_runtime.requests[-1].prompt
    assert "<conversation>" in prompt and "How does Superhuman measure" in prompt

    # A different session has independent context.
    other = await new_session(client)
    await ask(client, other, "How should teams think about onboarding activation?")
    assert "Superhuman" not in fake_runtime.requests[-1].prompt.split("<sources>")[0]


async def test_delete_session_cascades(client):
    sid = await new_session(client)
    await ask(client, sid, "How does Superhuman measure product-market fit?")
    assert (await client.delete(f"/api/sessions/{sid}")).status_code == 204
    assert (await client.get(f"/api/sessions/{sid}")).status_code == 404


async def test_rename_and_feedback(client):
    sid = await new_session(client)
    renamed = await client.patch(f"/api/sessions/{sid}", json={"title": "PMF research"})
    assert renamed.json()["title"] == "PMF research"
    turn = await ask(client, sid, "How does Superhuman measure product-market fit?")
    mid = turn["assistant_message"]["id"]
    fb = await client.post(f"/api/messages/{mid}/feedback", json={"rating": 1})
    assert fb.json()["feedback"] == 1
    user_mid = turn["user_message"]["id"]
    assert (await client.post(f"/api/messages/{user_mid}/feedback", json={"rating": 1})).status_code == 422


# ----------------------------------------------------------------- streaming


async def test_sse_stream_event_sequence(client):
    sid = await new_session(client)
    resp = await client.post(
        f"/api/sessions/{sid}/messages", json={"content": "How does Superhuman measure product-market fit?"}
    )
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(resp.text)
    names = [e for e, _ in events]
    assert names[0] == "user_message"
    assert "meta" in names and "delta" in names
    assert names[-1] == "done"
    assert names.index("meta") < names.index("delta")
    streamed = "".join(d["text"] for e, d in events if e == "delta")
    final = events[-1][1]["assistant_message"]
    assert "40 percent" in streamed and final["citations"] == ["S1"]


# ----------------------------------------------------------------- guardrails & failure modes


async def test_out_of_domain_question_is_declined_without_calling_the_model(client, fake_runtime):
    sid = await new_session(client)
    turn = await ask(client, sid, "What is a good chocolate cake recipe with frosting?")
    answer = turn["assistant_message"]
    assert answer["quality"]["answer_type"] == "not_covered"
    assert "don't cover this" in answer["content"]
    assert fake_runtime.requests == []


async def test_chitchat_is_answered_without_retrieval_or_model(client, fake_runtime):
    sid = await new_session(client)
    turn = await ask(client, sid, "hi")
    assert turn["assistant_message"]["route"] == "chitchat"
    assert "Lenny Growth Assistant" in turn["assistant_message"]["content"]
    assert fake_runtime.requests == []


async def test_provider_outage_degrades_to_evidence_only(client, app, fake_runtime):
    set_provider_state(app, "ollama", False)
    try:
        sid = await new_session(client)
        turn = await ask(client, sid, "How does Superhuman measure product-market fit?")
    finally:
        set_provider_state(app, "ollama", True)
    answer = turn["assistant_message"]
    assert answer["status"] == "degraded"
    assert answer["error"]["code"] == "provider_unavailable"
    assert answer["sources"] and "[S1]" in answer["content"]
    assert fake_runtime.requests == []


async def test_model_timeout_degrades_with_retryable_error(client, fake_runtime):
    fake_runtime.error = ModelTimeout("The model did not finish within 30s.")
    sid = await new_session(client)
    turn = await ask(client, sid, "How does Superhuman measure product-market fit?")
    answer = turn["assistant_message"]
    assert answer["status"] == "degraded"
    assert answer["error"]["code"] == "model_timeout" and answer["error"]["retryable"] is True


async def test_hallucinated_citations_are_stripped(client, fake_runtime):
    fake_runtime.responder = lambda _: "Superhuman asks how disappointed users would be [S1][S42]."
    sid = await new_session(client)
    answer = (await ask(client, sid, "How does Superhuman measure product-market fit?"))["assistant_message"]
    assert "[S42]" not in answer["content"]
    assert answer["quality"]["invalid_citations"] == ["S42"]


async def test_provider_override_is_validated_and_reported(client):
    sid = await new_session(client)
    turn = await ask(client, sid, "How does Superhuman measure product-market fit?", provider="anthropic")
    # Anthropic is "unavailable" in tests and no fallback is set -> degraded, not a crash.
    assert turn["assistant_message"]["status"] == "degraded"


# ----------------------------------------------------------------- skills: essay & artifacts


async def test_artifact_flow_persists_sanitised_html(client, fake_runtime):
    def responder(req: AgentRequest) -> str:
        assert "artifact-builder" in req.system_prompt
        return (
            '<artifact type="html" title="Onboarding one-pager"><!DOCTYPE html><html><head><style>h1{color:#c2410c}'
            "</style></head><body><h1 onclick=\"steal()\">Onboarding</h1><p>Define activation from retention [S1].</p>"
            "<script>steal()</script></body></html></artifact>\nHere is your one-pager."
        )

    sid = await new_session(client)
    await ask(client, sid, "What do guests recommend for onboarding activation?")
    fake_runtime.responder = responder
    turn = await ask(client, sid, "Make an HTML one-pager of the key onboarding tactics")
    artifact = turn["artifact"]
    assert turn["assistant_message"]["route"] == "artifact"
    assert turn["assistant_message"]["artifact_id"] == artifact["id"]
    assert artifact["kind"] == "html" and artifact["title"] == "Onboarding one-pager" and artifact["version"] == 1
    assert "<script>" in artifact["content"]  # raw kept for audit
    assert "<script" not in artifact["rendered_html"] and "onclick" not in artifact["rendered_html"]
    assert "Content-Security-Policy" in artifact["rendered_html"]
    assert artifact["sanitizer_report"]["scripts"] == 1

    render = await client.get(f"/api/artifacts/{artifact['id']}/render")
    assert "sandbox" in render.headers["content-security-policy"]
    detail = (await client.get(f"/api/sessions/{sid}")).json()
    assert len(detail["artifacts"]) == 1


async def test_artifact_revision_creates_new_version(client, fake_runtime):
    fake_runtime.responder = lambda _: '<artifact type="markdown" title="Checklist"># Checklist\n\n- [ ] Survey users [S1]</artifact>'
    sid = await new_session(client)
    await ask(client, sid, "How does Superhuman measure product-market fit?")
    first = await ask(client, sid, "Create a markdown checklist for running the PMF survey")
    second = await ask(client, sid, "make it shorter")
    assert first["artifact"]["version"] == 1
    assert second["artifact"]["version"] == 2
    assert "<current_artifact>" in fake_runtime.requests[-1].prompt


async def test_essay_flow_runs_skill_and_scores_checklist(client, fake_runtime):
    calls = []

    def responder(req: AgentRequest) -> str:
        calls.append(req.prompt)
        if "HEADLINE:" in req.prompt:
            return "HEADLINE: 3 Lessons on PMF\n1. Ask the question | S1\n2. Segment | S1\n3. Track it | S1"
        if "Write ONLY section" in req.prompt:
            return "## Section\n\nRahul Vohra asks users how disappointed they would be [S1].\n\n- **Survey** users [S1]"
        if "Write ONLY the ending" in req.prompt:
            return "## The takeaway\n\nRun the survey this week [S1].\n\n**TL;DR**\n- one\n- two\n- three"
        return "Most teams guess at PMF. Don't.\n\nThis essay curates Rahul Vohra [S1]."

    fake_runtime.responder = responder
    sid = await new_session(client)
    turn = await ask(client, sid, "Write an essay on how Superhuman measures product-market fit")
    answer = turn["assistant_message"]
    assert answer["route"] == "essay"
    assert answer["metrics"]["strategy"] == "sectioned"  # local provider -> sectioned
    assert answer["metrics"]["model_calls"] == 1 + 1 + 3 + 1  # outline, intro, 3 sections, takeaway
    assert turn["artifact"]["kind"] == "markdown"
    assert turn["artifact"]["content"].startswith("# 3 Lessons on PMF")
    checks = {c["id"]: c["passed"] for c in answer["quality"]["essay"]["checks"]}
    assert checks["headline"] and checks["structure"] and checks["takeaway"]
    assert checks["length"] is False  # the fake essay is short, and the checklist says so


# ----------------------------------------------------------------- ops


async def test_search_and_metrics_endpoints(client):
    search = (await client.get("/api/search", params={"q": "onboarding activation", "k": 2})).json()
    assert search["chunks"][0]["episode_slug"] == "lauryn-isford"
    metrics = (await client.get("/api/metrics")).json()
    overall = metrics["overall"]
    assert overall["answers"] > 0
    assert 0 <= overall["grounded_answer_rate"] <= 1
    assert "ollama" in metrics["by_provider"]


async def test_ingest_endpoint_requires_admin_token_outside_development(client):
    resp = await client.post("/api/kb/ingest")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"
