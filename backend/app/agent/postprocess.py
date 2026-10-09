"""Post-processing of model output (pure functions, unit-tested).

* **Citations**: normalise `[S1, S3]` / `(S2)` to `[S1][S3]`, drop ids that do
  not exist in this turn's sources (a hallucinated citation is worse than
  none), and measure how much of the answer is cited.
* **Artifacts**: extract `<artifact type=... title=...>` blocks, falling back to
  fenced code blocks or a bare HTML document because small models do not
  always follow the wrapper contract.
* **Essay checks**: score a Ship 30 essay against the skill's checklist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

CITATION_GROUP_RE = re.compile(r"[\[(]\s*(S\d+(?:(?:\s*[,;&]\s*(?:and\s+)?|\s+and\s+)S\d+)*)\s*[\])]")
CITATION_RE = re.compile(r"\[S(\d+)\]")
NOT_COVERED_RE = re.compile(
    r"(don'?t|do not|doesn'?t|does not) (directly )?(cover|address|mention|discuss)|"
    r"not covered|no (relevant )?(information|evidence)|couldn'?t find|could not find",
    re.I,
)


# --------------------------------------------------------------------------- citations


@dataclass
class CitationReport:
    content: str
    cited: list[str]
    invalid: list[str]
    coverage: float  # share of substantive paragraphs/bullets with a citation
    not_covered: bool

    def as_quality(self) -> dict[str, Any]:
        return {
            "cited_sources": len(self.cited),
            "invalid_citations": self.invalid,
            "citation_coverage": round(self.coverage, 2),
            "answer_type": "not_covered" if self.not_covered else "grounded" if self.cited else "uncited",
        }


def normalise_citations(text: str) -> str:
    def _expand(match: re.Match[str]) -> str:
        ids = re.findall(r"S\d+", match.group(1))
        return "".join(f"[{i}]" for i in ids)

    return CITATION_GROUP_RE.sub(_expand, text)


# Small-model failure modes: echoing the source header/tags instead of synthesising.
ECHOED_HEADER_RE = re.compile(r"^\s*(?:[-*]\s*)?\[[^\]\n]{12,}\]\([^)\n]*\)\s*:?\s*", re.M)
ECHOED_TAG_RE = re.compile(r"</?source\b[^>]*>", re.I)
# A bracketed disclaimer is a copied instruction template, not a real decline.
ECHOED_DISCLAIMER_RE = re.compile(r"^\s*\[[^\]\n]*(don'?t|do not) cover[^\]\n]*\]\s*", re.I)


def scrub_echoes(text: str) -> str:
    text = ECHOED_TAG_RE.sub("", text)
    text = ECHOED_DISCLAIMER_RE.sub("", text)
    return ECHOED_HEADER_RE.sub(lambda m: "- " if m.group(0).lstrip().startswith(("-", "*")) else "", text)


def process_citations(text: str, valid_ids: set[str]) -> CitationReport:
    text = normalise_citations(scrub_echoes(text))
    invalid: list[str] = []

    def _check(match: re.Match[str]) -> str:
        sid = f"S{match.group(1)}"
        if sid in valid_ids:
            return match.group(0)
        invalid.append(sid)
        return ""

    text = CITATION_RE.sub(_check, text)
    cited = list(dict.fromkeys(f"S{n}" for n in CITATION_RE.findall(text)))
    cited.sort(key=lambda s: int(s[1:]))

    blocks = [
        b
        for b in re.split(r"\n\s*\n|\n(?=\s*(?:[-*]|\d+\.)\s)", text)
        if len(b.split()) >= 12 and not b.lstrip().startswith("#")
    ]
    covered = sum(1 for b in blocks if CITATION_RE.search(b))
    coverage = covered / len(blocks) if blocks else (1.0 if cited else 0.0)
    head = " ".join(text.split()[:60])
    return CitationReport(
        content=text,
        cited=cited,
        invalid=sorted(set(invalid), key=lambda s: int(s[1:])),
        coverage=coverage,
        not_covered=bool(NOT_COVERED_RE.search(head)) and len(cited) <= 1,
    )


# --------------------------------------------------------------------------- attribution

STOPWORDS = frozenset(
    """the a an and or of to in on for with is are was were be been being it its this that these those you your
    we our they their them he she his her i me my as at by from about into than then so but if not no do does did
    have has had can could would should will just also more most very really what when where which who how why
    there here one all any some such out up get got like lot thing things way going know think said says say""".split()
)
QUOTE_RE = re.compile(r"[\"“]([^\"”]{25,400})[\"”]")


def _content_words(text: str) -> set[str]:
    return {w[:6] for w in re.findall(r"[a-z0-9']+", text.lower()) if len(w) > 2 and w not in STOPWORDS}


def _normalise_for_quote(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


@dataclass
class AttributionReport:
    content: str
    inferred: list[str]
    supported_blocks: int
    unsupported_blocks: int
    unverified_quotes: list[str]

    def as_quality(self) -> dict[str, Any]:
        total = self.supported_blocks + self.unsupported_blocks
        return {
            "inferred_citations": len(self.inferred),
            "unsupported_blocks": self.unsupported_blocks,
            "supported_ratio": round(self.supported_blocks / total, 2) if total else None,
            "unverified_quotes": self.unverified_quotes[:5],
        }


def attribute_blocks(
    text: str, sources: dict[str, str], min_overlap: float = 0.3, min_shared: int = 4
) -> AttributionReport:
    """Deterministic grounding check that does not trust the model.

    For every substantive line/paragraph without a citation, find the source
    with the highest content-word overlap. Strong overlap -> append that
    citation (recorded as *inferred*). Weak overlap -> count the block as
    unsupported, a hallucination signal surfaced in the UI and metrics.
    Quoted passages are checked verbatim against the sources.
    """
    source_words = {sid: _content_words(body) for sid, body in sources.items()}
    source_norm = " || ".join(_normalise_for_quote(body) for body in sources.values())
    inferred: list[str] = []
    supported = unsupported = 0
    out_lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        is_heading = stripped.startswith("#")
        words = _content_words(CITATION_RE.sub("", stripped))
        if is_heading or len(stripped.split()) < 10 or len(words) < 5:
            out_lines.append(line)
            continue
        if CITATION_RE.search(stripped):
            supported += 1
            out_lines.append(line)
            continue
        best_sid, best_shared, best_ratio = None, 0, 0.0
        for sid, sw in source_words.items():
            shared = len(words & sw)
            ratio = shared / len(words)
            if ratio > best_ratio:
                best_sid, best_shared, best_ratio = sid, shared, ratio
        if best_sid and best_ratio >= min_overlap and best_shared >= min_shared:
            supported += 1
            inferred.append(best_sid)
            out_lines.append(f"{line.rstrip()} [{best_sid}]")
        else:
            unsupported += 1
            out_lines.append(line)

    unverified = [
        q.strip()[:80]
        for q in QUOTE_RE.findall(text)
        if len(q.split()) >= 5 and _normalise_for_quote(q) not in source_norm
    ]
    return AttributionReport("\n".join(out_lines), inferred, supported, unsupported, unverified)


# --------------------------------------------------------------------------- artifacts


@dataclass
class ExtractedArtifact:
    kind: str  # markdown | html
    title: str
    content: str
    message: str  # the chat text that accompanies the artifact
    method: str  # tag | fence | bare_html | whole_output


ARTIFACT_TAG_RE = re.compile(r"<artifact\b([^>]*)>(.*?)(?:</artifact>|$)", re.I | re.S)
ATTR_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"|(\w+)\s*=\s*\'([^\']*)\'')
FENCE_RE = re.compile(r"```(html|markdown|md)?\s*\n(.*?)(?:```|$)", re.I | re.S)
HTML_DOC_RE = re.compile(r"(<!doctype html.*?</html>|<html\b.*?</html>)", re.I | re.S)
TITLE_TAG_RE = re.compile(r"<title>(.*?)</title>", re.I | re.S)
H1_HTML_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
H1_MD_RE = re.compile(r"^#\s+(.+)$", re.M)


INSTRUCTION_ECHO_RE = re.compile(
    r"^\s*(one sentence telling the user what you made|a single short sentence[^:]*|here is the artifact)\s*:\s*",
    re.I,
)
EMPTY_FENCE_RE = re.compile(r"```[a-z]*\s*```", re.I)


def _clean_message(text: str) -> str:
    """Chat text left after removing the artifact: drop empty fences and echoed instructions."""
    text = EMPTY_FENCE_RE.sub("", text)
    text = re.sub(r"^\s*```[a-z]*\s*$", "", text, flags=re.I | re.M)
    return INSTRUCTION_ECHO_RE.sub("", text.strip()).strip()


def _strip_tags(text: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", text).split())


def _guess_title(kind: str, content: str, fallback: str) -> str:
    if kind == "html":
        m = TITLE_TAG_RE.search(content) or H1_HTML_RE.search(content)
        if m:
            return _strip_tags(m.group(1))[:120] or fallback
    else:
        m = H1_MD_RE.search(content)
        if m:
            return m.group(1).strip().strip("*").strip()[:120] or fallback
    return fallback


def _looks_like_html(text: str) -> bool:
    return bool(re.search(r"<(html|body|div|section|table|style|h1)\b", text, re.I))


def extract_artifact(output: str, expected_kind: str | None, fallback_title: str) -> ExtractedArtifact | None:
    text = output.strip()
    if not text:
        return None

    m = ARTIFACT_TAG_RE.search(text)
    if m:
        attrs = {(a or c).lower(): (b or d) for a, b, c, d in ATTR_RE.findall(m.group(1))}
        body = m.group(2).strip()
        fence = FENCE_RE.fullmatch(body)
        if fence:
            body = fence.group(2).strip()
        kind = attrs.get("type", "").lower()
        if kind not in ("html", "markdown"):
            kind = "html" if _looks_like_html(body) else (expected_kind or "markdown")
        title = attrs.get("title") or _guess_title(kind, body, fallback_title)
        message = _clean_message(text[: m.start()] + text[m.end() :])
        return ExtractedArtifact(kind, title[:200], body, message, "tag")

    doc = HTML_DOC_RE.search(text)
    if doc:
        body = doc.group(1).strip()
        message = _clean_message(text[: doc.start()] + text[doc.end() :])
        return ExtractedArtifact("html", _guess_title("html", body, fallback_title)[:200], body, message, "bare_html")

    fence = FENCE_RE.search(text)
    if fence and len(fence.group(2).split()) > 40:
        lang = (fence.group(1) or "").lower()
        body = fence.group(2).strip()
        kind = "html" if lang == "html" or (not lang and _looks_like_html(body)) else "markdown"
        message = _clean_message(text[: fence.start()] + text[fence.end() :])
        return ExtractedArtifact(kind, _guess_title(kind, body, fallback_title)[:200], body, message, "fence")

    if expected_kind == "html" and _looks_like_html(text):
        return ExtractedArtifact("html", _guess_title("html", text, fallback_title)[:200], text, "", "whole_output")
    if len(text.split()) > 60:
        return ExtractedArtifact(
            "markdown", _guess_title("markdown", text, fallback_title)[:200], text, "", "whole_output"
        )
    return None


# --------------------------------------------------------------------------- essay checks


@dataclass
class Check:
    id: str
    label: str
    passed: bool
    detail: str


@dataclass
class EssayReport:
    word_count: int
    checks: list[Check] = field(default_factory=list)

    @property
    def score(self) -> float:
        return sum(c.passed for c in self.checks) / len(self.checks) if self.checks else 0.0

    def as_quality(self) -> dict[str, Any]:
        return {
            "word_count": self.word_count,
            "score": round(self.score, 2),
            "checks": [c.__dict__ for c in self.checks],
        }


def _sentences(paragraph: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", paragraph.strip()) if s])


def check_essay(markdown: str, target_words: int = 1250, cited_sources: int = 0) -> EssayReport:
    """Score an essay against the Ship 30 checklist encoded in the skill."""
    plain = CITATION_RE.sub("", markdown)
    words = len(re.findall(r"[A-Za-z0-9'’-]+", re.sub(r"[#*_>`-]", " ", plain)))
    lines = markdown.strip().splitlines()
    h1 = next((ln for ln in lines if ln.startswith("# ")), None)
    h2s = [ln for ln in lines if ln.startswith("## ")]
    bullets = [ln for ln in lines if re.match(r"^\s*([-*]|\d+\.)\s+", ln)]
    bolds = re.findall(r"\*\*[^*]+\*\*", markdown)
    paragraphs = [
        p for p in re.split(r"\n\s*\n", markdown) if p.strip() and not p.lstrip().startswith(("#", "-", "*", "1"))
    ]
    hook = paragraphs[0] if paragraphs else ""
    long_paragraphs = [p for p in paragraphs if _sentences(p) > 5]
    takeaway = any(
        re.search(r"takeaway|bottom line|the lesson|what to do|tl;?dr|action", h, re.I) for h in h2s
    ) or bool(re.search(r"\*\*TL;?DR\*\*", markdown, re.I))
    numbered_headline = bool(h1 and re.search(r"\d", h1))

    low, high = int(target_words * 0.8), int(target_words * 1.2)
    checks = [
        Check("length", "≈1,250 words", low <= words <= high, f"{words} words (target {low}–{high})"),
        Check(
            "headline",
            "Headline names a number + topic",
            bool(h1) and numbered_headline,
            h1[2:].strip() if h1 else "no # headline",
        ),
        Check(
            "hook",
            "Short hook (≤ 2 sentences)",
            bool(hook) and _sentences(hook) <= 2 and len(hook.split()) <= 45,
            f"{_sentences(hook)} sentence(s), {len(hook.split())} words" if hook else "missing",
        ),
        Check("structure", "3+ section headings", len(h2s) >= 3, f"{len(h2s)} ## sections"),
        Check("skimmable", "Bullets for lists", len(bullets) >= 4, f"{len(bullets)} bullet lines"),
        Check("bold", "Selective bold (2–12)", 2 <= len(bolds) <= 12, f"{len(bolds)} bold phrases"),
        Check(
            "rhythm",
            "No wall-of-text paragraphs",
            not long_paragraphs,
            f"{len(long_paragraphs)} paragraph(s) over 5 sentences",
        ),
        Check("takeaway", "Specific takeaway / TL;DR", takeaway, "found" if takeaway else "no takeaway section"),
        Check("grounded", "Cites 3+ transcript sources", cited_sources >= 3, f"{cited_sources} distinct sources cited"),
    ]
    return EssayReport(word_count=words, checks=checks)
