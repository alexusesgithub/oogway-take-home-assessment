# The Lenny Growth Assistant

An internal assistant for product and growth teams. It answers questions **only from Lenny's Podcast transcripts**, cites the guest, episode and timestamp for every claim, turns answers into **Ship 30 for 30-style essays**, and produces **Markdown or HTML one-pagers** that render in a sandboxed viewer beside the chat.

It runs fully locally on **Ollama** (the demo default). Switch to **Claude** with one environment variable or from the header menu, with no code changes.

![Answer with citations and sources](docs/images/answer.png)

| | |
|---|---|
| **Grounded answers** | Hybrid-ready BM25 retrieval over 291 episodes / 17,427 passages. `[S1]` chips open the exact excerpt, with a YouTube link at the right second. Off-topic questions get an honest "not covered" instead of a guess. |
| **Ship 30 essays** | A versioned skill encodes the Ship 30 for 30 principles (headline formula, Golden Intersection, 1/3/1 rhythm, Tequila Test…). Each essay is scored against a 9-point checklist shown in the UI. |
| **Artifacts** | One-pagers, checklists and comparison tables as Markdown or HTML, with versions, copy and download. HTML is sanitised, CSP-locked and rendered in a `sandbox=""` iframe. |
| **Trust signals** | Hallucinated citations are removed. Uncited claims are matched to sources ("inferred") or flagged as "unsupported". Quotes are verified verbatim. Route, model and timing are shown on every answer. |
| **Operable** | One-command Docker Compose, JSON logs with request ids, `/health/ready`, `/api/metrics`, graceful degradation when the model or DB is down, 131 automated tests. |

**Docs:** [PRD](docs/PRD.md) · [Architecture](docs/architecture.md) · [Design](docs/design.md) · [Manual UI test plan](docs/manual-test-plan.md) · [Demo script](docs/demo-script.md) · [Agent transcripts & dev log](agent-transcripts/)

---

## Quick start (about 5 minutes)

### Prerequisites
- **Docker** with Compose v2 (Docker Desktop on macOS/Windows)
- **Ollama** installed and running on the host: <https://ollama.com/download>
- ~4 GB free RAM for the 3B model, ~3 GB disk. Internet for the first run (model pull + transcript clone).

### Run
```bash
git clone <this-repo> lenny-growth-assistant && cd lenny-growth-assistant
ollama pull qwen2.5:3b            # optional: the app also pulls it automatically on first start
docker compose up --build         # builds UI + API, starts Postgres, migrates, indexes transcripts
```
Open **http://localhost:8000**. No `.env` file is needed for the local demo.

On first start, the app:
1. applies database migrations;
2. clones the transcript repository and indexes it (~20 s);
3. creates `lenny-qwen2.5-3b:ctx8192`, a copy of the model with an 8k context window pinned, because Ollama's default 4k window silently truncates prompts.

The header shows a green dot next to **Ollama · qwen2.5:3b** when it is ready.

> **Speed expectations on a CPU-only laptop:** answers take ~30–40 s (first words after ~20 s), essays ~2–3 min, artifacts ~1–2 min. With a GPU, or with Claude, they take seconds. The UI shows live progress for each stage.

### Try this in order (the demo script)
1. *How does Superhuman measure product-market fit?* Click a citation chip to open the source and **Watch at mm:ss**.
2. Follow-up: *What about B2B companies?* This shows session context and follow-up retrieval.
3. *Turn that into a Ship 30 essay*. The essay opens in the Artifact Viewer, with the checklist under the message.
4. *Make an HTML one-pager of the key ideas*. This renders a sandboxed HTML preview; switch to the **HTML** tab, then **Download**.
5. *What's a good chocolate cake recipe?* The assistant declines because the transcripts don't cover it, and the model is never called.
6. Start a **New chat**: its context is independent. Old chats persist in the sidebar.

---

## Configuration

Copy [`.env.example`](.env.example) to `.env` to change anything. **Every variable is optional.**

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | Default provider: `ollama` or `anthropic` |
| `LLM_FALLBACK_PROVIDER` | *(empty)* | Provider to use automatically if the default is down |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | Where Ollama runs (from inside Docker) |
| `OLLAMA_MODEL` | `qwen2.5:3b` | Any Ollama chat model, e.g. `qwen2.5:7b`, `llama3.1:8b` |
| `OLLAMA_NUM_CTX` | `8192` | Context window pinned on the derived model (`0` = use as-is) |
| `ANTHROPIC_API_KEY` | *(empty)* | Enables Claude |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | e.g. `claude-sonnet-5-5` for lower cost |
| `LLM_TIMEOUT_SECONDS` | `300` | Per model call |
| `AGENT_TOOLS` | `auto` | Agentic transcript search: on for Claude, off for small local models |
| `ESSAY_STRATEGY` | `auto` | `sectioned` (outline + per-section calls) for local, `single` for Claude |
| `EMBEDDINGS_ENABLED` | `false` | Hybrid (BM25 + vector) retrieval via `nomic-embed-text` |
| `TRANSCRIPTS_REF` | `main` | Branch/tag/commit of the transcript repo to index |
| `ADMIN_TOKEN` | *(empty)* | Required header for `POST /api/kb/ingest` |
| `LOG_FORMAT` | `json` | `console` for human-readable logs |

### Using Claude (cloud)
```bash
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env
docker compose up -d              # restart picks up the key
```
Pick **Claude (cloud)** in the header menu for a chat, or make it the default with `LLM_PROVIDER=anthropic`. For local-first with a cloud safety net, set `LLM_PROVIDER=ollama` and `LLM_FALLBACK_PROVIDER=anthropic`.

**Fallback behaviour:** if the selected provider is unhealthy, or fails before streaming anything, the fallback answers and the message says so. With no fallback, you get an **evidence-only answer**: the most relevant passages with citations, the error and a fix hint, instead of a dead end.

### No Ollama on the host?
Run it in Compose instead (CPU only):
```bash
OLLAMA_BASE_URL=http://ollama:11434 docker compose --profile ollama up --build
```

---

## Running without Docker (development)

```bash
docker compose up -d db                                   # just Postgres (+pgvector)

cd backend
python -m venv .venv && . .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql+asyncpg://lenny:lenny@localhost:5432/lenny OLLAMA_BASE_URL=http://localhost:11434 \
       TRANSCRIPTS_DIR=../data/transcripts LOG_FORMAT=console APP_ENV=development
alembic upgrade head
uvicorn app.main:app --reload --port 8000                 # API on :8000 (auto-ingests on first start)

cd ../frontend && npm ci && npm run dev                   # UI on :5173 (proxies /api to :8000)
```

**Operator CLI** (from `backend/`):
```bash
python -m app.cli status                  # episodes, chunks, last ingestion run
python -m app.cli ingest [--full]         # refresh from the transcript repo (idempotent)
python -m app.cli search "growth team"    # inspect retrieval for any question
python -m app.cli embed                   # back-fill embeddings (EMBEDDINGS_ENABLED=true)
```
These run inside the container too: `docker compose exec app python -m app.cli status`.

---

## Tests

```bash
docker compose --profile test run --rm tests       # backend suite inside Docker, against the compose DB
# or locally:
cd backend && pytest -q                            # 124 tests; DB tests auto-skip if Postgres is down
cd frontend && npm test                            # 7 tests (SSE parser, citation linking, formatting)
```

| Area | What is covered |
|---|---|
| **Retrieval** (`test_ingest_search.py`, `test_retrieval_unit.py`) | Ingestion counts, idempotent refresh, sponsor removal, duplicate/metadata guardrails, ranking, out-of-domain coverage, RRF, diversity, excerpt focusing |
| **Routing and skills** (`test_router.py`, `test_prompts_providers.py`) | Route table, mode overrides, artifact revisions, skill loading, prompt budget trimming, provider fallback |
| **Grounding** (`test_postprocess.py`) | Citation normalisation and invalid-id removal, attribution, quote verification, artifact extraction, essay checklist |
| **Security** (`test_sanitize.py`) | XSS, exfiltration and phishing vectors blocked, including CSS-escape, `image-set()` and obfuscated-scheme bypasses; CSP present |
| **API and persistence** (`test_api.py`) | Session CRUD and per-user isolation, history and independent context, SSE event contract, error envelope, not-covered/degraded/timeout paths, artifact versions, sectioned essay, metrics |

The UI is covered by the [manual test plan](docs/manual-test-plan.md). The LLM is faked in automated tests, so they are fast, free and deterministic. Live model behaviour is covered by the manual plan.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Header shows **Ollama · Unavailable** | Ollama isn't reachable. Run `ollama serve` (or start the desktop app). On Linux without Docker Desktop, make Ollama listen on all interfaces: `OLLAMA_HOST=0.0.0.0 ollama serve`. Check `curl localhost:11434/api/tags`. |
| Header shows **Preparing model…** | First-start download of the model (~2 GB). Watch `docker compose logs -f app` for `ollama_pull_done` / `ollama_alias_created`. |
| Answers are "evidence-only" | The model call failed. The message shows the error code and hint, and `docker compose logs app \| grep <request_id>` shows the details. Set `LLM_FALLBACK_PROVIDER` to auto-switch. |
| `model_timeout` on essays | CPU-only machines are slow. Raise `LLM_TIMEOUT_SECONDS`, use a GPU, or switch to Claude. |
| Banner "Building the knowledge base…" persists | Ingestion needs GitHub access. Check `GET /api/kb/status` (`last_run.error`). Offline: copy the repo to `./data/transcripts` and mount it, since a non-git folder is used as-is. |
| `/health/ready` returns `down` | Postgres is unreachable. Check `docker compose ps` and `DATABASE_URL`. |
| Claude shows **Not configured** | Set `ANTHROPIC_API_KEY` in `.env` and restart the app container. |
| Port 8000 or 5432 already in use | Set `APP_PORT` / `POSTGRES_PORT` in `.env`. |
| Answers feel shallow | The 3B model is the price of running on any laptop. Try `OLLAMA_MODEL=qwen2.5:7b` or Claude; retrieval and grounding stay the same. |

Every response carries an `X-Request-ID`. Error bodies include it too, and every JSON log line for that request carries the same id.

---

## Architecture at a glance

```
React UI ──fetch/SSE──▶ FastAPI ─▶ ChatService ─┬─▶ Router (rules) ─▶ Skill (SKILL.md)
                                                ├─▶ Retriever (BM25 in Postgres, optional pgvector)
                                                ├─▶ Agent runtime (Claude Agent SDK) ─┬─▶ Ollama (local)
                                                │                                     └─▶ Anthropic (cloud)
                                                ├─▶ Post-processing (citations, attribution, artifacts)
                                                └─▶ PostgreSQL (sessions, messages, artifacts, KB)
```
See [docs/architecture.md](docs/architecture.md) for the schema, API, retrieval algorithm, security model and deployment topology.

## Project structure
```
backend/
  app/
    api/            HTTP routes (sessions, chat SSE, artifacts, health, config, metrics)
    agent/          router, skills loader, prompts, providers, Claude Agent SDK runtime, post-processing
    services/       chat orchestration, sectioned essay writer
    retrieval/      chunker, ingestion, search, embeddings
    artifacts/      HTML sanitiser
    skills/         grounded-answer, ship30-essay, artifact-builder (SKILL.md)
    db/             SQLAlchemy models, engine
  alembic/          migrations
  tests/            pytest suite + fixture transcripts
frontend/src/       React + TypeScript UI (components, hooks, api client, styles)
docs/               PRD, architecture, design, manual test plan, screenshots
agent-transcripts/  coding-agent session log and curated dev log
docker-compose.yml  db + app (+ optional ollama, tests)
```

## Known limitations
- Lexical retrieval misses pure paraphrases. Enable embeddings for hybrid search; on a GPU this takes minutes.
- The 3B local model writes plainer prose and sometimes fewer than 1,250 words. The essay checklist and "unsupported statement" flags make that visible rather than hiding it.
- No authentication: this is designed to sit behind company SSO (see PRD scope).
- Artifacts are HTML/CSS only. JavaScript is deliberately blocked (see the architecture security section).

## Data source
Transcripts: [ChatPRD/lennys-podcast-transcripts](https://github.com/ChatPRD/lennys-podcast-transcripts). Every citation links to the transcript file at the exact commit that was indexed.
