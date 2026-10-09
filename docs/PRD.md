# PRD: The Lenny Growth Assistant

| | |
|---|---|
| **Owner** | Forward Deployed Engineer |
| **Client** | Product & Growth team (internal tool) |
| **Status** | v1.0 delivered: local demo on Ollama, cloud-ready on Claude |
| **Related** | [architecture.md](architecture.md) · [design.md](design.md) · [manual-test-plan.md](manual-test-plan.md) |

---

## 1. Forward deployment brief (discovery)

### 1.1 User and problem

**Primary user: the product or growth practitioner** (PM, growth lead, PMM) preparing a decision, a planning doc or a team update. Example: *Priya, a senior PM on the activation team, has a quarterly planning review on Thursday and needs to argue for rebuilding onboarding around one activation metric.*

**Job to be done:** *"When I'm facing a product or growth decision, help me quickly find what proven operators actually said about it, with sources I can cite, and turn it into something I can share."*

**Today's pain:**

| Pain | Today | With the assistant |
|---|---|---|
| **Finding** | ~300 episodes ≈ 300+ hours of audio. YouTube search matches titles, not ideas. | Seconds to the relevant passages across all guests. |
| **Trusting** | Generic chatbots answer confidently from memory, misattribute quotes ("Lenny said…"), and can't show where an idea came from. | Every claim is tied to a guest, episode and timestamp. Unsupported claims are flagged, and the assistant says "not covered" instead of guessing. |
| **Re-using** | Turning research into a team memo, newsletter or one-pager takes hours of copy-paste and formatting. | A cited essay or a polished one-pager in one step, ready to download. |
| **Operating** | Prompts, models and infrastructure are opaque to the team. | The team never sees prompts or models. Ops sees health, metrics and logs. |

**Secondary users:**

- **Enablement / internal-comms lead.** Writes the weekly "growth learning" post (Ship 30 essays).
- **Client engineer.** Runs and extends the system after handoff (README, architecture doc, CLI, tests).

### 1.2 Success metrics

All of these are measurable from day one via `GET /api/metrics`, which aggregates the per-message `quality` and `metrics` columns. Feedback comes from 👍/👎 on each answer.

| # | Metric | Definition | Target (pilot) | Why it matters |
|---|---|---|---|---|
| **M1 (north star)** | **Grounded-answer rate** | Share of answered questions (excluding declines) with ≥1 valid citation and no invalid ones | **≥ 95%** | Trust is the product. An uncited answer is a liability. |
| M2 | Unsupported-statement rate | Unsupported blocks ÷ substantive blocks (deterministic attribution check) | ≤ 10% on Claude, tracked on local | Direct hallucination signal, independent of the model's own claims |
| M3 | Correct-decline rate | Off-topic eval questions answered "not covered" | 100% | Saying "I don't know" is a feature |
| M4 | Helpfulness | 👍 ÷ (👍 + 👎) | ≥ 70% | User value, not just correctness |
| M5 | Latency | p50 / p95 time to complete answer | Claude: p95 < 15 s. Local CPU: p95 < 90 s | Adoption dies above these thresholds |
| M6 | Availability | Share of turns not degraded or errored | ≥ 98% | Operational health |
| M7 (adoption, post-pilot) | Weekly active users; essays and artifacts shared | Usage logs | 50% of the team weekly by week 4 | Is it part of the workflow? |

**Current measured baseline** (local qwen2.5:3b on a CPU-only laptop, development runs):

- Answers: 100% grounded (all had valid citations), 0 invalid citations after validation, 30–40 s per answer.
- Out-of-domain probes declined deterministically in < 1 s.
- Essays (sectioned strategy): 870–970 words, 6–7 of 9 Ship 30 checks, ~2.5 min. Single-pass was 578 words, 4/9.

### 1.3 Assumptions (the brief was incomplete)

| # | Assumption | If wrong… |
|---|---|---|
| A1 | The transcript source is the linked public repository [ChatPRD/lennys-podcast-transcripts](https://github.com/ChatPRD/lennys-podcast-transcripts) (303 episode folders). It contains **podcast** transcripts only, no newsletter posts. | Add a loader that produces `ParsedEpisode`. Chunking, indexing and citations are source-agnostic. |
| A2 | Internal use of the transcripts is permitted. Answers quote short excerpts and link back to the original episode. | Restrict excerpts and keep links only. |
| A3 | It is an internal tool behind company SSO, so no login is built in v1. A per-browser anonymous id separates users' chats. | Put an OIDC proxy in front and map the identity header to `X-User-Id` (no code change). |
| A4 | English only, and up to ~30 concurrent users in a pilot. | Multilingual needs a different FTS config or embeddings. Scale Postgres and app replicas (the app is stateless). |
| A5 | The demo machine is a CPU-only laptop. The evaluator's machine may be similar, so the default local model must be small (qwen2.5:3b, ~2 GB). | Bigger models are one env var away (`OLLAMA_MODEL`). |
| A6 | Production users will mostly use Claude. Local mode exists for privacy, offline use and the demo. | Both are first-class. Defaults are a single setting. |
| A7 | "Grounded" means answers use **only** transcript knowledge, even when the model knows more. | A "general knowledge" mode could be added as an explicit, labelled toggle. |
| A8 | Users want shareable outputs (Markdown/HTML), not executable mini-apps. | JS artifacts would need a separate sandbox origin (see Risks). |
| A9 | Source metadata can be wrong. We found 12 duplicate transcripts and 15 transcripts carrying another episode's title and video. Wrong attribution is worse than missing attribution. | Guardrails withhold suspect links instead of citing the wrong video. |

### 1.4 Scope choices

**Included:**

| Capability | Why |
|---|---|
| Grounded Q&A with citations, follow-ups and per-session context | Core job; required |
| Honest "not covered" with an evidence threshold, without calling the model | Trust (M1/M3) and cost |
| Persistent sessions (PostgreSQL), sidebar history, rename and delete | Required; supports re-use |
| Provider toggle (Ollama ↔ Claude), visible in the UI, with fallback and evidence-only degraded mode | Required; resilience |
| Ship 30 essay skill with an encoded checklist plus sectioned generation for small models | Required; makes quality measurable |
| Markdown/HTML artifacts, versioned revisions, sandboxed viewer, copy and download | Required; security was explicitly asked for |
| Ingestion with idempotent refresh, provenance (commit-pinned links) and data-quality guardrails | Required; traceability |
| Ops: JSON logs with request ids, health/ready, metrics endpoint, retrieval debug endpoint, CLI | Handoff to a client team |
| One-command Docker Compose, automated tests, manual UI test plan | Required; evaluator must run it |

**Intentionally excluded:**

| Excluded | Reason | Path forward |
|---|---|---|
| Authentication / multi-tenant | An internal tool belongs behind SSO. Building auth would be throwaway work. | OIDC proxy plus the `X-User-Id` mapping |
| Embeddings on by default | ~1 h of CPU to embed 17k chunks on the demo laptop. Lexical BM25 with proximity performed well on the domain's vocabulary. | `EMBEDDINGS_ENABLED=true` (implemented: hybrid RRF) |
| LLM query rewriting, LLM reranking, LLM routing | Each adds seconds per turn on a 3B CPU model and is unreliable at that size | Enable for Claude once eval data exists |
| JavaScript in artifacts | Executing model-written JS is a meaningful risk for a v1 internal tool | Separate cookie-less origin, then `allow-scripts` |
| Evaluation harness with LLM-as-judge | Needs a labelled question set from the client | The metrics endpoint plus the "Try this" demo set seed it |
| Newsletter content | Not present in the provided repository | New loader (A1) |
| Streaming resume after page reload | Answers are persisted server-side even if the tab disconnects, so resume is cosmetic | SSE replay endpoint |

### 1.5 Risks and trade-offs

| Risk | Likelihood / impact | Mitigation in v1 | Residual |
|---|---|---|---|
| **Hallucination / misattribution** | High on 3B models / high | Retrieval-first (the model never answers without evidence); strict grounding prompt; `[S#]` validation (invalid ids removed); a deterministic attribution check flags unsupported statements; verbatim quote check; evidence gate declines low-coverage questions without the model | 3B models still paraphrase loosely. Flags make it visible. Claude is markedly better. |
| **Local-model quality** | High / medium | Source focusing, a tag-based prompt layout, scrubbing of echoed headers, sectioned essays, pinned 8k context | Plainer prose than Claude. Documented, with the model being one env var. |
| **Latency** (CPU) | High / medium | Short focused prompts (TTFT ~32 s → ~20 s); streaming with stage labels and timers; the not-covered path skips the model; a concurrency limit | 30–40 s answers on CPU. GPU or Claude gives seconds. |
| **Cost** (cloud) | Medium / low | Retrieval caps context (8 sources); helper calls disabled; tool turns capped at 4; cost recorded per message | Opus pricing. `ANTHROPIC_MODEL=claude-sonnet-5-5` for cost-sensitive use. |
| **Data leakage** | Low / high | Local mode never leaves the machine. Cloud sends only the question, recent turns and excerpts. Logs hold metadata, not content. Secrets come only from the environment. | The Anthropic data policy applies in cloud mode. |
| **Unsafe artifact rendering** | Medium / high | Three layers: allowlist sanitiser, CSP `default-src 'none'`, and a `sandbox=""` iframe. The UI reports what was removed. | The CSS-only visual spoofing surface inside the frame is accepted. |
| **Prompt injection** via transcripts or user text | Low / medium | Evidence is tagged as data; the agent has no side-effecting tools (built-ins disabled); output is post-validated | A model could still be steered in tone. Nothing it can *do* is dangerous. |
| **Source data quality** | Observed / medium | Duplicate detection, guest inference from speaker labels, withholding suspect title and video links, sponsor-read removal | Undetected errors in the transcription text itself. |
| **Dependency on the Claude Agent SDK CLI** | Low / medium | The bundled CLI is pinned via the wheel version; failures map to `provider_unavailable` with a hint plus degraded mode | SDK upgrades need a smoke test (manual plan T1). |

---

## 2. User flows

**F1. Ask and follow up**

1. The user types a question (Auto mode).
2. The UI shows *Searching transcripts* and then *N passages found* with guest chips.
3. The answer streams in with `[n]` citation chips.
4. Clicking a chip expands the source card: excerpt, **Watch at mm:ss**, and **Transcript**.
5. A follow-up ("what about B2B?") reuses the session context. Retrieval combines it with the previous question.

**F2. Essay**

1. The user types *"Turn that into a Ship 30 essay"* (or picks Essay mode).
2. The essay streams in stages: *Planning outline → hook and intro → section n of 4 → takeaway*.
3. When done, the Artifact Viewer opens with the Markdown essay. The chat message summarises its word count, sources and checklist score.
4. The **Ship 30 checklist** expands to show each check, pass or fail.

**F3. Artifact and revise**

1. The user types *"Make an HTML one-pager of the key ideas"*.
2. The artifact card appears in chat and the viewer opens with a sandboxed preview, plus **HTML** source, copy, download and full screen.
3. The user types *"make it shorter"*. This produces version 2, and the selector switches between versions.

**F4. Switch model**

1. The header menu shows *Ollama (local)* and *Claude (cloud)* with status dots.
2. If Claude isn't configured, its entry is disabled and the hint explains how to add a key.
3. The chosen provider is remembered per browser. Each answer shows which model produced it.

**F5. Failure**

1. Ollama is stopped mid-demo. The header turns red and a banner explains the fix.
2. The next question still returns the top passages with citations, marked *evidence-only*, with the error code and hint.
3. If `LLM_FALLBACK_PROVIDER` is set, Claude answers instead and the answer is labelled.

---

## 3. Acceptance criteria

| ID | Given / When / Then | Verified by |
|---|---|---|
| AC1 | **When** a user starts a new chat, **then** a session with its own id and timestamps is stored, and its context is independent of other sessions. | `test_follow_up_uses_session_history_and_previous_question`, manual T3 |
| AC2 | **When** a user asks a covered question, **then** the answer cites ≥1 valid `[S#]` that resolves to guest, episode, timestamp and links. | `test_turn_is_persisted_with_metadata`, manual T1 |
| AC3 | **When** the model cites a non-existent source, **then** the citation is removed and recorded. | `test_hallucinated_citations_are_stripped` |
| AC4 | **When** the question is off-topic (lexical coverage < 0.72), **then** the reply says the transcripts don't cover it and the model is not called. | `test_out_of_domain_question_is_declined_without_calling_the_model`, T6 |
| AC5 | **When** a follow-up is asked, **then** the previous question informs retrieval and the conversation is included in the prompt. | `test_follow_up_…`, `test_followups_are_combined_with_previous_question` |
| AC6 | **When** an essay is requested, **then** the `ship30-essay` skill runs, a Markdown artifact is created, and a 9-check Ship 30 report is attached. | `test_essay_flow_runs_skill_and_scores_checklist`, T4 |
| AC7 | **When** an artifact is requested, **then** it renders beside the chat. HTML is sanitised (no scripts, handlers, frames, forms or remote resources), CSP-locked and sandboxed, and the removals are reported. | `test_artifact_flow_persists_sanitised_html`, `test_sanitize.py`, T5 |
| AC8 | **When** a user asks to revise the artifact, **then** a new version is stored and the previous one remains accessible. | `test_artifact_revision_creates_new_version` |
| AC9 | **When** `LLM_PROVIDER` or `OLLAMA_MODEL`/`ANTHROPIC_MODEL` change, **then** behaviour changes without code changes, and the UI shows the active provider and model. | `test_resolve_*`, T7 |
| AC10 | **When** the provider is down or times out, **then** the user gets an evidence-only answer with a code and hint (or the fallback provider). There are no crashes or blank screens. | `test_provider_outage_degrades_to_evidence_only`, `test_model_timeout_degrades_with_retryable_error`, T8 |
| AC11 | **When** the DB is unreachable, **then** `/health/ready` is `down` (503) and API calls return `database_unavailable` with a hint. | Architecture §10a, T9 |
| AC12 | All errors use one envelope with a stable `code`, `hint` and `request_id`. Validation errors list the offending fields. | `test_errors_use_structured_envelope` |
| AC13 | A user can't read another user's sessions or artifacts. | `test_sessions_are_isolated_per_user` |
| AC14 | Ingestion is idempotent and traceable (run records, commit SHA, per-episode hashes). Sponsor reads and duplicates are excluded. | `test_ingest_search.py`, `test_chunker.py` |
| AC15 | `docker compose up --build` on a clean machine yields a working app at :8000 with no manual DB or index steps. | Verified on a fresh volume (dev log D18), T0 |

---

## 4. Implementation plan (as executed)

| Phase | Outcome | Key decisions |
|---|---|---|
| 0. Spike the risks (first hour) | Proved the Claude Agent SDK can drive Ollama via its Anthropic-compatible API, tool calls work with qwen2.5:3b, the corpus shape is understood, and CPU limits are measured (embeddings ~5 chunks/s; Ollama 4k context truncation). | One runtime for both providers. Retrieval-first rather than tool-first. Pinned-context model alias. Lexical-first retrieval. |
| 1. Knowledge base | Speaker-turn chunker, sponsor removal, BM25 in Postgres with IDF, proximity, diversity and small-to-big. Data-quality guardrails. | Tuned on real queries ("Superhuman PMF", "growth team", out-of-domain probes). |
| 2. Agent layer | Router, SKILL.md skills, budgeted prompts, runtime hardening, post-processing (citations, attribution, artifacts, essay checks). | Deterministic routing; trust signals computed outside the model. |
| 3. API and persistence | Sessions, SSE chat with background persistence, error envelope, health, metrics, artifacts. | The server is the source of truth; turns survive disconnects. |
| 4. UI | Chat, sources, provider switch, artifact viewer, responsive and accessible. | Editorial visual language; visible trust and status. |
| 5. Hardening | Sectioned essays for small models, excerpt focusing, echo scrubbing, evidence thresholds. | Each change driven by an observed failure (see the dev log). |
| 6. Ops and handoff | Docker image and compose (with a test profile), docs, test suites, manual plan, agent transcripts. | Verified from a fresh volume. |

**Next 30 days (recommended):**

1. Collect 50 real questions from the team and build an eval set. Track M1–M3 per release.
2. Turn on hybrid retrieval on a GPU host and A/B it against BM25.
3. Add SSO, then share links for artifacts.
4. Make Claude the default for production, with Ollama for offline and private use.
5. Use model-invoked skills and query rewriting for Claude only.
