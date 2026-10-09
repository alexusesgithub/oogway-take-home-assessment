# Demo video script (2–3 minutes, camera on)

**Before recording**

- Run `docker compose up --build` and confirm the header dot is green for **Ollama · qwen2.5:3b**.
- On a CPU-only laptop, **pre-generate the essay and the one-pager** in a chat ("Demo") so you can show them instantly. Ask one fresh question live to prove the local model works.
- Open `docker compose logs -f app` in a second window. You'll show one log line.
- Browser at 1440×900, light mode, zoom 100%.

| Time | Show | Say (suggested) |
|---|---|---|
| **0:00–0:20** · Problem | Camera + empty state | "Product and growth teams have 300 hours of Lenny's Podcast and no way to use it. Search finds titles, not ideas. Generic chatbots answer from memory and misattribute quotes. I built an internal assistant that answers *only* from the transcripts and shows exactly where every claim came from." |
| **0:20–1:05** · Grounded answer, live on Ollama | Point at the header: **Ollama (local) · qwen2.5:3b**. Ask *"How does Superhuman measure product-market fit?"* Let the stages play: *Searching → 5 passages found → Writing*. | "This runs entirely on my laptop through Ollama. The agent layer is the Claude Agent SDK, pointed at Ollama's Anthropic-compatible API, so switching to Claude is a config change." (When it finishes:) "Every claim has a citation. Click one and you get the exact excerpt, the guest, and a link that opens YouTube at that second." Click **Watch at 53:12**. |
| **1:05–1:20** · Honesty | Ask *"What's a good chocolate cake recipe?"* | "If the transcripts don't cover it, it says so in under a second. It never calls the model, so there's no chance to make something up." |
| **1:20–1:50** · Essay + artifact | Open the pre-generated **Ship 30 essay** in the viewer, expand the checklist. Then the **HTML one-pager**: Preview, HTML tab, footer. | "The Ship 30 skill is a versioned SKILL.md that encodes the guide's principles: headline formula, Golden Intersection, 1/3/1 rhythm. Every essay is scored against a checklist, shown right here. Artifacts render beside the chat. Generated HTML is untrusted, so it's sanitised server-side, locked down with a CSP that blocks every network request, and rendered in a sandboxed iframe with no scripts. The footer tells you what was removed." |
| **1:50–2:30** · One trade-off | Point at the badges on an answer ("4 sources cited", "1 inferred citation", "unsupported"), then at a `turn_completed` log line. | "The key trade-off: a 3B local model is unreliable at tool use and citation discipline. So instead of letting the model decide when to search, the backend **always retrieves first** and owns the citations. After generation, a deterministic check validates every citation, removes invented ones, and flags sentences that don't match any source. With Claude I switch agentic search on. With a small model I keep control in code. The cost is less autonomy, and what we get back is grounding you can measure. That's the grounded-answer rate on the metrics endpoint." |
| **2:30–2:50** · Ops + close | Header menu (provider toggle + fallback note), then README quick start. | "One `docker compose up` brings up Postgres, migrations and indexing. Health, metrics and JSON logs with request ids are all there. If Ollama goes down you still get the evidence, never a blank screen. Everything is in the README, PRD and architecture docs. Thanks!" |

**Tips**

- If a live answer is slow, keep talking over the stage labels. The narrated progress is part of the design.
- Upload to YouTube as *Unlisted* or *Public*, then paste the link in the submission form.
