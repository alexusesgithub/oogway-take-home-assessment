"""Skill registry.

Skills live in `app/skills/<name>/SKILL.md` using the Claude Agent Skills
format (YAML front matter + Markdown instructions), so they are versioned,
reviewable artifacts rather than prompt strings buried in code. The router
activates exactly one skill per turn; its instructions are composed into the
agent's system prompt and its output is checked by an optional validator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    route: str
    version: str
    output: str  # chat | artifact
    instructions: str
    top_k: int
    top_k_local: int
    max_output_tokens: int
    artifact_kind: str | None = None
    target_words: int | None = None
    source: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def render(self, **values: str) -> str:
        text = self.instructions
        for key, value in values.items():
            text = text.replace("{{" + key + "}}", value)
        return text


def parse_skill(raw: str) -> Skill:
    if not raw.startswith("---"):
        raise ValueError("SKILL.md must start with YAML front matter")
    end = raw.index("\n---", 3)
    meta = yaml.safe_load(raw[3:end]) or {}
    body = raw[end + 4 :].strip()
    retrieval = meta.get("retrieval") or {}
    for required in ("name", "description", "route"):
        if not meta.get(required):
            raise ValueError(f"SKILL.md missing '{required}'")
    return Skill(
        name=meta["name"],
        description=" ".join(str(meta["description"]).split()),
        route=meta["route"],
        version=str(meta.get("version", "0")),
        output=meta.get("output", "chat"),
        instructions=body,
        top_k=int(retrieval.get("top_k", 8)),
        top_k_local=int(retrieval.get("top_k_local", 5)),
        max_output_tokens=int(meta.get("max_output_tokens", 1500)),
        artifact_kind=meta.get("artifact_kind"),
        target_words=meta.get("target_words"),
        source=meta.get("source"),
        meta=meta,
    )


class SkillRegistry:
    def __init__(self, skills: dict[str, Skill]) -> None:
        self._by_route = {s.route: s for s in skills.values()}
        self._by_name = skills

    @classmethod
    def load(cls, directory: Path = SKILLS_DIR) -> SkillRegistry:
        skills = {}
        for path in sorted(directory.glob("*/SKILL.md")):
            skill = parse_skill(path.read_text(encoding="utf-8"))
            skills[skill.name] = skill
        if not skills:
            raise RuntimeError(f"No skills found in {directory}")
        return cls(skills)

    def for_route(self, route: str) -> Skill:
        return self._by_route[route]

    def all(self) -> list[Skill]:
        return list(self._by_name.values())
