# Agent transcripts

This system was built with **Claude Code** (Claude Opus 5.5) as the coding agent, directed and verified by the candidate. The brief evaluates *"judgment and ability to direct, verify, and improve AI-assisted work"*, so this folder shows that process honestly, including the mistakes.

| File | What it is |
|---|---|
| [`DEVLOG.md`](DEVLOG.md) | **Start here.** 22 curated entries, each in the form: what we tried → what we observed (with numbers) → what we changed. It covers the feasibility spikes, six retrieval-tuning iterations, data-quality discoveries, small-model failure modes, bugs caught by tests and drills, and the Docker build-target mistake. |
| [`session-transcript.md`](session-transcript.md) | The full session exported from the agent's log: prompts, narration, **every tool call** (commands, file writes and edits) and truncated tool results, in order. |

## How the work was verified (not just generated)
- **Feasibility spikes before design:** the SDK against Ollama, tool calling, embedding throughput and context truncation were each measured before committing to an architecture.
- **Retrieval tuned on real queries** against the full corpus, with before/after rankings recorded.
- **Real model runs** through the API and through the **real UI** (headless Chrome), with screenshots inspected at each stage. Answer quality issues were fixed at the cause: prompt format, excerpting, sectioned essays.
- **Automated tests:** 124 backend + 7 frontend. They also caught two production bugs.
- **One-command deploy verified from a fresh volume**, including the Agent SDK CLI inside the container talking to host Ollama.
- **Failure drills:** DB stopped mid-run, Ollama unreachable.
- **An independent review pass** of the code by a separate agent before the final commit. It found 13 issues; all were fixed with regression tests (DEVLOG D21).

## Redaction
The export removes hidden reasoning, base64 attachments (the assignment PDF and screenshots), system-context blocks, email addresses, local user and account names, file-system paths under the user profile, and anything resembling an API key or token. The project uses no secrets: the local demo needs none, and the Anthropic key is read from `.env`, which is git-ignored.
