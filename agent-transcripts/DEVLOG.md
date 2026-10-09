# Development log: decisions, failed attempts and corrections

This log summarises how the coding agent (Claude Code, Claude Opus 5.5) built the system under the candidate's direction, and where it went wrong and corrected course. The raw session is in [`session-transcript.md`](session-transcript.md).

Every entry follows the same pattern: **what we tried → what we observed (with numbers) → what we changed**. Most product decisions in the PRD trace back to one of these entries.

---

## Phase 0: De-risking before writing product code

### D1. Environment: Docker daemon not running
- **Tried:** `docker info` failed with *cannot find dockerDesktopLinuxEngine*, and Docker Desktop wasn't at the default install path.
- **Fix:** found it under `%LOCALAPPDATA%\Programs\DockerDesktop` and launched it.
- **Lesson for the README:** "Docker must be running" is a real first-run failure, so it's covered in troubleshooting.

### D2. Can the Claude Agent SDK drive a local model?
The requirement was to build the agent layer on the Claude Agent SDK *and* demo it on Ollama, which looks contradictory at first.

- **Spike:** Ollama 0.40 exposes an Anthropic-compatible `/v1/messages`. Running `claude_agent_sdk.query()` with `ANTHROPIC_BASE_URL=http://localhost:11434`, `tools=[]` and a custom system prompt streamed an answer from `qwen2.5:3b`.
  - First stream event arrived at **6.0 s**.
  - Only **398 input tokens** were sent, so the custom system prompt kept Claude Code's large default prompt out.
- **Decision:** one runtime for both providers. Switching provider is configuration only, which satisfies the "no code change" toggle.

### D3. Tool calling with a 3B model: works, but drops citations
- **Spike:** an in-process MCP `search_transcripts` tool. qwen2.5:3b *did* call it correctly, but its answer **omitted the `[S1]` marker** the tool output asked for.
- **Decision:** **retrieval-first.**
  - The backend always retrieves before calling the model and owns the citation registry.
  - Agentic search is an extra on top, enabled by default only for Claude (`AGENT_TOOLS=auto`).
  - Grounding must never depend on a small model's tool discipline.

### D4. Embeddings are too slow on this laptop
- **Measured:** `nomic-embed-text` on CPU ran at **4.9 chunks/s**. The corpus is ~18k chunks, so embedding would take **~1 hour**.
- **Decision:** PostgreSQL full-text (BM25) is the default. Embeddings stay optional (`EMBEDDINGS_ENABLED`), and RRF hybrid search is implemented and tested, but off by default.

### D5. Ollama silently truncates long prompts
- **Measured:** a ~9k-token prompt reported `input_tokens: 2047`. `ollama ps` showed a **4096** context.
- **Fix:** the app calls Ollama `/api/create` to make `lenny-qwen2.5-3b:ctx8192`, with `num_ctx` pinned. It is auto-provisioned on startup and retried if Ollama comes up later.
- **Verified:** a 5,035-token prompt was fully processed.
- **Also:** prompts are budgeted, trimming history first, then excerpt length, then source count.

---

## Phase 1: Knowledge base

### D6. Sponsor reads polluting retrieval
- **First attempt:** flag chunks containing ≥2 ad phrases. This **missed most ads**: 372 chunks still contained "brought to you by".
- **Observed:** ads span several consecutive Lenny turns, and often start mid-turn ("Thank you for sharing that. This episode is brought to you by…").
- **Fix:** turn-level removal that keeps the text before the ad phrase and drops same-speaker continuations within 150 s. 372 residual chunks became **2**, and both are legitimate discussion of the phrase.

### D7. Alembic + asyncpg migration failure
- **Error:** `cannot insert multiple commands into a prepared statement`.
- **Fix:** split the DDL script into individual statements.

### D8. Retrieval quality: six iterations, driven by real queries
Each change was tested against a probe set:

- "How does Superhuman measure PMF?"
- "pricing a B2B SaaS product"
- "improve onboarding activation"
- "build a growth team"
- out-of-domain: "chocolate cake recipe", "who won the 2022 world cup"

| # | Attempt | Observation | Change |
|---|---|---|---|
| 1 | OR-query + `ts_rank_cd` | Superhuman episode ranked **5th**, because `ts_rank` has no IDF and "superhuman" weighs the same as "product" | Add IDF |
| 2 | IDF "coverage" from a `ts_stat` materialised view | Superhuman → #1, but pricing lost the pricing expert (no term frequency) | Add TF |
| 3 | Per-term `ts_rank` as TF | **Worse**: title matches dominated | Revert to real BM25 |
| 4 | BM25 from `unnest(tsvector)` (exact TF), field boosts | "growth team" still wrong. Root cause: **thousands of chunks tied at coverage 1.0 were truncated arbitrarily** by the 400-row prefilter | Tie-break by density; stoplist for question filler ("think", "best", "way") |
| 5 | DF inspection | `growth` appeared in **16,941 / 18,154** chunks, because title and keywords are repeated on every chunk | Compute DF over spoken text only |
| 6 | Proximity | BM25 has no notion of "growth team" adjacency | Bigram `phraseto_tsquery` bonus. Adam Fishman's growth-team episode now surfaces |

The outcome was also a usable "no evidence" signal. **Best-chunk coverage** is 1.0 for in-domain questions and 0.55–0.65 for out-of-domain ones. That separation became the 0.5 / 0.75 decline and weak-evidence thresholds.

### D9. Data-quality defects in the source repository
- **Found while debugging:** 33 `video_id`s are shared across episode folders.
  - 12 are exact duplicate transcripts (`nicole-forsgren` and `nicole-forsgren-20`).
  - Others carry *another episode's* title and YouTube link. For example, `chip-conley`'s front matter is Maggie Crowley's episode.
  - Some guest names are partial ("Melissa" is Melissa Perri).
- **Fix (`plan_episodes`):**
  - skip duplicate bodies;
  - take the guest from the transcript's own speaker labels;
  - withhold title and video when the id is shared *and* the title doesn't name the speaker (15 episodes).
- **Principle:** wrong attribution is worse than missing attribution.

---

## Phase 2: Agent layer

### D10. Router false positives
- **Observed:** "How do I build a landing page?" routed to the artifact skill.
- **Fix:** plain questions stay answers unless they contain an explicit ask ("can you", "for me"). This is covered by parametrised tests.

### D11. Self-inflicted tooling bugs (and how they were caught)
- **Bug:** patching Python through bash heredocs and generated scripts corrupted regex escapes. `\b` became a literal backspace character, and `\n` inside a pattern became a real newline.
- **Bug:** on Windows, `Path.write_text()` used **cp1252** (mangling "…") and translated newlines **twice** (`\r\r\n`).
- **Caught by:** a UTF-8 validity scan over all sources, `file` reporting "CRLF, CR line terminators", and a `SyntaxError` on import.
- **Fix:** byte-level normalisation, `.gitattributes eol=lf`, and switching to exact-string edits for regex-heavy code.

### D12. The first live answer had zero citations
- **Observed (qwen2.5:3b):** 0 `[S#]` markers. The model **copied the source header** (`[Episode title](date)`) as if it were the citation, and garbled one claim. Prompt eval took **32 s** to first token.
- **Changes:**
  1. Sources go in `<source id="S1" guest="…">` tags, because small models echo bracketed headers but not XML attributes.
  2. An explicit citation-style example in the rules.
  3. An echo scrubber in post-processing.
  4. A **deterministic attribution check**: uncited lines are matched to sources by content-word overlap, and either get an *inferred* citation or are flagged *unsupported*. Quotes are verified verbatim.
  5. **Excerpt focusing** for local models (the densest 170-word window, speaker labels kept).
- **Result:** 0 → 4 native citations, 100% citation coverage, and first token at ~20–25 s instead of ~32 s.

### D13. The key passage was in the next chunk
- **Observed:** Rahul Vohra's "40% very disappointed" explanation was in the chunk *after* the retrieved one.
- **Fix:** small-to-big. Each hit is merged with its following chunk, with the repeated carry-over turn removed. Local focusing then picks the best window across both.

---

## Phase 3–4: API, UI and end-to-end testing through the real UI

The UI was driven with headless Chrome (puppeteer-core) and screenshots were inspected at each step.

### D14. An artifact request was declined as "not covered"
- **Observed:** "Make an HTML one-pager of the key onboarding tactics" got the not-covered reply. "html" and "one-pager" are rare in transcripts, so they **dominated the IDF mass** and coverage fell below 0.5.
- **Fix:** essay and artifact routes search only the **topic** words. They reuse the previous answer's evidence when the request builds on it, detected by topic overlap with the previous turn.

### D15. Essays were half the requested length
- **Observed:** a single-pass essay from qwen2.5:3b was **578 words, 4/9 checklist**.
- **Fix:** `ESSAY_STRATEGY=auto` runs the same skill **sectioned** for local models: outline → intro → one call per section with its own sources → takeaway. Result: **871 words, 7/9**, streamed with per-section progress.
- **Second observation:** the model copied instruction nouns as labels ("Section:", "Key Point:", "Supporting Detail:") and wrapped the hook in template brackets.
- **Fix:** instructions describe structure without naming parts, and outline titles are cleaned. The essay prose became clean ("5 Lessons From Superhuman's Rahul Vohra for Finding and Improving Product-Market Fit"). Across later runs: **870–970 words, 6–7 of 9 checks**. The hook check still often fails because the 3B model merges the hook into the intro, and the checklist shows that honestly.
- Claude keeps the single-pass path.

### D16. Artifact message polish
- **Observed:** the chat message echoed the skill contract ("One sentence telling the user what you made:…"), and a leftover empty code fence rendered as a grey bar.
- **Fix:** reworded the contract, added message cleaning, and added a default on-brand stylesheet inside the sandbox so terse model HTML still looks designed.

### D17. Tests found real bugs
Writing the suite (104 backend tests) surfaced two production bugs:

- the citation normaliser didn't expand `[S1; S2 and S3]`;
- `focus_excerpt` counted re-inserted speaker labels against the budget and clipped the key sentence, and its tie-break preferred windows with irrelevant lead-in.

One test was itself wrong (a synthetic "good" essay with 22-sentence paragraphs) and was corrected.

### D18. Docker: the app container ran the test suite
- **Observed:** after adding a `test` stage at the end of the Dockerfile, `docker compose up` built the **last** stage for the app. The app container ran pytest in a restart loop (76 passed, 28 skipped).
- **Fix:** `target: app` in compose.
- **Re-verified on a fresh volume:**
  - migrations, then auto-ingest (291 episodes / 17,427 chunks in **17.6 s**);
  - `/health/ready` = ok;
  - the Agent SDK's bundled CLI inside the slim Linux image reached host Ollama via `host.docker.internal` and returned a grounded answer (3 citations, 35 s);
  - `docker compose --profile test run --rm tests` passed **104** tests.

### D19. Failure drills on the running stack
- **DB outage:** `docker compose stop db` → `/health/ready` reported `down`, and API calls returned a structured `503 database_unavailable` with a hint. After `start db`, everything recovered **without restarting the app** (pool pre-ping).
- **Ollama outage:** a second app instance was pointed at a dead Ollama port → readiness `degraded` with the fix hint. A covered question returned an **evidence-only** answer (top passages + citations, `provider_unavailable`) instead of an error page.

### D20. Evidence the sandbox works
The puppeteer driver injects a `localStorage` script into every frame. Inside the artifact iframe it threw *"SecurityError: The document is sandboxed and lacks the 'allow-same-origin' flag"*. That is the opaque-origin isolation working as designed.

---

### D21. Independent review: 13 findings, all fixed
A separate agent reviewed the code read-only, ran probes and reported defects ranked by severity. Every finding was fixed, and 17 regression tests were added (121 backend tests now).

| Severity | Finding | Fix |
|---|---|---|
| High | After a turn error, **Retry did nothing and newly typed messages were silently dropped**. A `pending` check in a stale closure was the cause. | Re-entrancy guard moved to a ref. `send` replaces an errored turn. Retry re-sends directly. |
| High/Med | **Citation ids drifted between turns.** Carried sources were re-sorted, so a revised artifact's `[S4]` could point at another passage. | Keep the stored order. Remap `[S#]` in the current artifact by chunk id. Strip markers from history. |
| Medium | **The evidence gate (0.5) let the README's own off-topic demo through** (cake = 0.65), contradicting the docs. | Re-measured 16 in-domain vs 9 off-topic queries and moved the gate to 0.72 / 0.90. The limit (all-common-word questions) is documented. |
| Medium | Each SSE request **held a pooled DB connection for the whole stream**, so ~10 concurrent turns would exhaust the pool. | Release the request session before streaming. |
| Medium | **Switching chats mid-stream mixed sessions**, and fast A→B clicks could render A under B. | Abort and clear on every switch; stale loads are ignored via a token. |
| Medium | **Revision detection was too broad** ("create a checklist and *add* …" overwrote the essay as v2), and essays couldn't be revised at all. | A revision must refer back ("it", "the essay") or be a short edit, and never "create a/new …". Essay edits go through the artifact skill. |
| Med/Low | **The ingest endpoint was open in the default Docker run** (`APP_ENV` defaulted to development). | Secure by default: `production` unless set. |
| Med/Low | **Attribution checked against text the model never saw** (neighbour tails, trimmed excerpts). | Stored and attributed text equals the rendered excerpt. |
| Low | **Sanitizer bypasses** at the first layer (CSP and the sandbox still held): CSS escapes (`=rl(`), `image-set()`, `d&#9;ata:` hrefs, a `<body>` hidden in a comment, citation markers injected into attributes. | Decode CSS escapes, strip image functions, normalise URL whitespace, strip comments first, inject badges in text nodes only. |
| Low | `RETRIEVAL_TOP_K*` documented but unused. | Removed; per-skill budgets live in SKILL.md. |
| Low | The tool-id hint was computed before the source list was trimmed. | Filled in after trimming. |
| Low | Queue wait was not bounded by the timeout, and essays had no total budget. | One deadline covers slot wait + generation. Essays get 3× the per-call limit. |
| Low (a11y) | Screen readers announced the elapsed timer every second, and Escape closed the viewer while typing. | Timer marked `aria-hidden`, Escape scoped, arrow-key navigation for modes and tabs. |

The review also confirmed what was fine: empty and stop-word queries don't error, the advisory lock is always released, the SSE worker always ends with `done` or `error`, and user isolation holds.

### D22. Final live check on the rebuilt container found three more issues
I ran the README's own demo sequence in one session against the real model: off-topic question, then a real question, then a follow-up.

1. **A topic switch broke retrieval.** The 8-word question "What do guests recommend for improving onboarding activation?" was treated as a follow-up (the rule was "≤ 8 words") and merged with the previous *cake* question. Coverage fell to 0.33 and a perfectly answerable question was declined.
   - **Fix:** a message counts as a follow-up only when it looks like one: an opener such as "and" or "what about", pronouns, or ≤ 2 content words.
   - **Also:** when a merged query is used, the standalone query is searched too, and the better-covered result wins (`followup_merge_rejected` log).
2. **The model imitated canned declines from history.** With the deterministic "don't cover this" reply in `<conversation>`, qwen2.5:3b declined a later question that had 1.0 coverage.
   - **Fix:** canned replies (declines, help text, evidence-only) are excluded from model history. The user's question is kept.
3. **The model copied the example phrase from the grounding rules** as a bracketed prefix: `[The transcripts I have don't cover this.] …` on grounded answers.
   - **Fix:** the rule no longer contains a quotable phrase, and post-processing removes the bracketed echo. Real declines are still detected.

**Result:** the three-turn demo runs as intended:

| Turn | Behaviour | Time |
|---|---|---|
| Off-topic question | Deterministic decline | 45 ms |
| Real question | Grounded, 3 sources | 35 s |
| Follow-up | Grounded, 5 sources | 28 s |

Each fix has a regression test (124 backend tests).

## What I would do next with more time
- Collect a labelled 50-question eval set from the client, and gate releases on grounded-answer rate and unsupported-statement rate.
- Run hybrid retrieval on a GPU host and A/B it against BM25.
- Use model-invoked skills, query rewriting and reranking on the Claude path only.
- Add a server-side cancel for Stop, and SSE resume.
