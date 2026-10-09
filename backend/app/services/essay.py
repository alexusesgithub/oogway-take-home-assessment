"""Sectioned essay generation for small local models.

A 3B model asked for "1,250 words with this skeleton" in one pass reliably
stops at ~600 words and drops sections (observed with qwen2.5:3b). Long-form
quality improves a lot when the same skill is executed as several short,
focused generations:

    outline -> hook + intro -> one call per section (with only its sources)
    -> takeaway + TL;DR

Each call has a small prompt (cheap prompt-eval on CPU) and one job. The
writing principles still come from the ship30-essay SKILL.md; this module
only changes *how* the skill is executed. Cloud models keep the single pass.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.agent.prompts import GROUNDING_RULES, SourceRegistry, format_sources
from app.agent.providers import ProviderTarget
from app.agent.runtime import AgentRequest, AgentRuntime
from app.agent.skills import Skill

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]

OUTLINE_RE = re.compile(r"^\s*(\d)[.)]\s*(.+?)\s*(?:\|\s*(.*))?$")


@dataclass
class Outline:
    headline: str
    sections: list[tuple[str, list[str]]] = field(default_factory=list)


def principles(skill: Skill) -> str:
    """The 'writing principles' block of the skill, reused in every part."""
    text = skill.instructions
    start = text.find("## The writing principles")
    end = text.find("## Required skeleton")
    return text[start:end].strip() if start != -1 and end != -1 else text


def parse_outline(text: str, valid_ids: list[str], topic: str, guests: list[str]) -> Outline:
    """Parse `HEADLINE: ...` + `1. Title | S1, S3` lines; repair what is missing (pure)."""
    headline = ""
    sections: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        clean = line.strip().strip("*").strip()
        if clean.upper().startswith("HEADLINE:"):
            headline = clean.split(":", 1)[1].strip().strip('"#* ')
            continue
        m = OUTLINE_RE.match(clean)
        if m and len(sections) < 4:
            title = m.group(2).strip().strip('"*# ').rstrip("|").strip()
            title = re.sub(r"^(section|lesson|step|mistake)\s*\d*\s*[:.-]\s*", "", title, flags=re.I).strip()
            ids = [i for i in re.findall(r"S\d+", m.group(3) or "") if i in valid_ids]
            if title:
                sections.append((title, ids))

    if len(sections) < 3:  # model ignored the format: fall back to one lesson per source guest
        sections = sections[:1]
        for guest in guests:
            if len(sections) >= 4:
                break
            if not any(guest in t for t, _ in sections):
                sections.append((f"What {guest} gets right", []))
    # Spread unassigned sources round-robin so every section has evidence.
    for i, (title, ids) in enumerate(sections):
        if not ids:
            sections[i] = (title, [valid_ids[(i * 2) % len(valid_ids)], valid_ids[(i * 2 + 1) % len(valid_ids)]])
    if not headline or not re.search(r"\d", headline):
        headline = f"{len(sections)} Lessons From Lenny's Guests on {topic.strip().rstrip('?').capitalize()}"
    return Outline(headline=headline, sections=sections)


class SectionedEssayWriter:
    def __init__(self, runtime: AgentRuntime, emit: Emit, timeout_seconds: float) -> None:
        self.runtime = runtime
        self.emit = emit
        self.timeout = timeout_seconds
        self.calls = 0
        self.first_token_ms: int | None = None

    async def _call(self, target: ProviderTarget, system: str, prompt: str, max_tokens: int, stream: bool) -> str:
        self.calls += 1
        parts: list[str] = []
        final_text = ""
        request = AgentRequest(
            target=target,
            system_prompt=system,
            prompt=prompt,
            max_output_tokens=max_tokens,
            timeout_seconds=self.timeout,
        )
        async for event in self.runtime.run(request):
            if event.type == "delta":
                parts.append(event.data["text"])
                if stream:
                    await self.emit("delta", event.data)
            elif event.type == "final":
                final_text = event.data.get("text") or ""
                if self.first_token_ms is None:
                    self.first_token_ms = event.data.get("ttft_ms")
        return (final_text or "".join(parts)).strip()

    async def write(self, target: ProviderTarget, skill: Skill, request: str, registry: SourceRegistry) -> str:
        items = registry.items()
        valid_ids = [sid for sid, _ in items]
        by_id = dict(items)
        guests = list(dict.fromkeys(c.guest for _, c in items))
        rules = f"{GROUNDING_RULES}\n{principles(skill)}"
        brief = format_sources(items, max_words=70)

        await self.emit("status", {"stage": "generating", "label": "Planning the essay outline"})
        outline_text = await self._call(
            target,
            rules,
            f"<sources>\n{brief}\n</sources>\n\n<request>\n{request}\n</request>\n\n"
            "Plan a Ship 30 essay using ONE structure (Lessons, Steps or Mistakes). Reply in exactly this format:\n"
            "HEADLINE: <number + who + what + outcome>\n1. <section title> | <source ids, e.g. S1, S3>\n"
            "2. <section title> | <ids>\n3. <section title> | <ids>\n4. <section title> | <ids>",
            300,
            stream=False,
        )
        outline = parse_outline(outline_text, valid_ids, request, guests)

        async def part(label: str, sources: list[str], instruction: str, max_tokens: int) -> str:
            await self.emit("status", {"stage": "generating", "label": label})
            evidence = format_sources([(sid, by_id[sid]) for sid in sources if sid in by_id], max_words=170)
            text = await self._call(
                target,
                rules,
                f"<sources>\n{evidence}\n</sources>\n\nEssay headline: {outline.headline}\n"
                f"Sections: {'; '.join(t for t, _ in outline.sections)}\n\n{instruction}",
                max_tokens,
                stream=True,
            )
            await self.emit("delta", {"text": "\n\n"})
            return text

        await self.emit("delta", {"text": f"# {outline.headline}\n\n"})
        intro = await part(
            "Writing the hook and intro",
            valid_ids[:4],
            "Write ONLY the opening of the essay, as plain prose with no headings, labels or brackets. "
            "First a punchy hook: one or two short sentences, under 30 words. Then a blank line. Then an intro "
            "paragraph of about 150 words that says who this is for, the problem they face, what they will get, "
            f"and which guests you are curating ({', '.join(guests[:4])}). Cite claims like [S1].",
            500,
        )
        body = [f"# {outline.headline}", intro]
        for i, (title, ids) in enumerate(outline.sections, start=1):
            section = await part(
                f"Writing section {i} of {len(outline.sections)}",
                ids,
                f'Write ONLY section {i} of the essay, at least 250 words. Start with the heading "## {i}. {title}". '
                "Open with one sentence that states the lesson. Follow with a paragraph of three or four sentences "
                "explaining what the guest said and why it works. Then give 3 practical bullet points. Then a "
                "closing paragraph of two sentences. Make one short phrase **bold**. Write flowing prose: do not "
                "add labels such as 'Key point:' or 'Supporting detail:'. Cite claims like [S1].",
                650,
            )
            if not section.lstrip().startswith("##"):
                section = f"## {i}. {title}\n\n{section}"
            body.append(section)
        takeaway = await part(
            "Writing the takeaway",
            valid_ids[:3],
            'Write ONLY the ending of the essay. Start with "## The takeaway", then about 150 words of prose with one specific action '
            "the reader can take this week and a 3-item checklist. Finish with a line **TL;DR** followed by three "
            "one-line bullets. Cite claims like [S1].",
            450,
        )
        if "## The takeaway" not in takeaway:
            takeaway = f"## The takeaway\n\n{takeaway}"
        body.append(takeaway)
        return "\n\n".join(p.strip() for p in body if p.strip())
