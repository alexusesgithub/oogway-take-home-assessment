"""Deterministic intent router.

Why rules instead of an LLM classifier: the demo runs a 3B local model, which
is unreliable at tool/skill selection, and an extra classification call would
add seconds of CPU latency to every turn. Rules are instant, testable and
explainable; the UI also lets users force a mode, and every routing decision
is returned to the client with its reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

ArtifactKind = Literal["markdown", "html"]
Mode = Literal["auto", "answer", "essay", "artifact"]


class Route(StrEnum):
    ANSWER = "answer"
    ESSAY = "essay"
    ARTIFACT = "artifact"
    CHITCHAT = "chitchat"


@dataclass(frozen=True)
class RouteDecision:
    route: Route
    reason: str
    artifact_kind: ArtifactKind | None = None
    revises_artifact: bool = False


CHITCHAT_RE = re.compile(
    r"^\s*(hi|hello|hey|yo|hiya|good (morning|afternoon|evening)|thanks|thank you|thx|ty|"
    r"who are you|what can you do|help|what do you do|how does this work)[\s!.?]*$",
    re.I,
)
ESSAY_RE = re.compile(
    r"\b(essay|ship ?30|article|blog ?post|newsletter( post| issue)?|long[- ]form|write[- ]?up|"
    r"op[- ]?ed|think ?piece)\b",
    re.I,
)
ARTIFACT_NOUNS = (
    r"one[- ]?pager|cheat ?sheet|checklist|playbook|template|table|matrix|framework doc|"
    r"web ?page|landing page|html|markdown|\.md\b|document|doc\b|artifact|infographic|card|"
    r"poster|slide|brief|memo|report|guide|handout|worksheet|comparison|scorecard|dashboard|page"
)
ARTIFACT_VERBS = (
    r"make|create|generate|build|design|draft|produce|turn|convert|export|render|put|format|lay out|give me"
)
ARTIFACT_RE = re.compile(rf"\b({ARTIFACT_VERBS})\b.*\b({ARTIFACT_NOUNS})", re.I | re.S)
HTML_HINT_RE = re.compile(
    r"\b(html|css|web ?page|landing page|infographic|visual|styled|poster|card|dashboard|page)\b", re.I
)
# "How do I build a landing page?" is a question about the topic, not a request
# to produce one. Questions only route to essay/artifact with an explicit ask.
QUESTION_RE = re.compile(r"^\s*(how|what|why|when|which|who|where|should|is|are|do|does|did)\b", re.I)
EXPLICIT_ASK_RE = re.compile(r"\b(can you|could you|would you|please|for me|for us|i need|i want)\b", re.I)
REVISION_RE = re.compile(
    r"\b(update|change|revise|edit|tweak|modify|make it|add|remove|shorter|longer|rename|"
    r"darker|lighter|colou?r|font|bigger|smaller|instead)\b",
    re.I,
)


def _artifact_kind(message: str) -> ArtifactKind:
    return "html" if HTML_HINT_RE.search(message) else "markdown"


def route_message(message: str, mode: Mode = "auto", has_artifact: bool = False) -> RouteDecision:
    """Pick the skill for a user message.

    `has_artifact` lets short edit requests ("make it darker") continue the
    artifact the session is already working on.
    """
    text = message.strip()
    if mode == "answer":
        return RouteDecision(Route.ANSWER, "mode selected: Ask")
    if mode == "essay":
        return RouteDecision(Route.ESSAY, "mode selected: Essay", artifact_kind="markdown")
    if mode == "artifact":
        return RouteDecision(
            Route.ARTIFACT,
            "mode selected: Artifact",
            artifact_kind=_artifact_kind(text),
            revises_artifact=has_artifact and bool(REVISION_RE.search(text)),
        )

    if CHITCHAT_RE.match(text):
        return RouteDecision(Route.CHITCHAT, "greeting or help request")
    if QUESTION_RE.match(text) and not EXPLICIT_ASK_RE.search(text):
        return RouteDecision(Route.ANSWER, "question (default)")
    if ESSAY_RE.search(text) and not re.search(r"\bhtml\b", text, re.I):
        match = ESSAY_RE.search(text)
        return RouteDecision(Route.ESSAY, f'essay request ("{match.group(0)}")', artifact_kind="markdown")
    artifact = ARTIFACT_RE.search(text)
    if artifact:
        kind = _artifact_kind(text)
        return RouteDecision(
            Route.ARTIFACT,
            f'artifact request ("{artifact.group(2)}")',
            artifact_kind=kind,
            revises_artifact=has_artifact and bool(REVISION_RE.search(text)),
        )
    if has_artifact and len(text.split()) <= 14 and REVISION_RE.search(text):
        return RouteDecision(
            Route.ARTIFACT, "short edit to the current artifact", artifact_kind=None, revises_artifact=True
        )
    return RouteDecision(Route.ANSWER, "question (default)")
