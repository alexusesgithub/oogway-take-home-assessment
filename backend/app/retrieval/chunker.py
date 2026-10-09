"""Transcript parsing and chunking (pure functions, no I/O).

Transcripts look like::

    ---
    guest: Adam Fishman
    title: ...
    ---
    # Title
    ## Transcript
    Adam Fishman (00:00:00):
    Onboarding is the only part of your product experience ...

    Lenny (00:01:12):
    ...

Chunking strategy (see docs/architecture.md#ingestion):

* Split on **speaker turns** so a chunk never cuts a speaker mid-thought and
  every chunk carries a real timestamp we can deep-link to on YouTube.
* Pack consecutive turns up to ``target_words``; split single monologues
  longer than ``max_words`` on sentence boundaries.
* A short turn that ends a chunk (typically Lenny's question) is carried into
  the next chunk too, so answers keep the question that prompted them.
* Sponsor reads are flagged and excluded from retrieval.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import yaml

TURN_RE = re.compile(r"^(?P<speaker>[^\n()]{1,80}?) \((?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\):\s*$", re.M)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
SPONSOR_RE = re.compile(
    r"brought to you by|this episode is sponsored|today'?s sponsor|use code|promo code|"
    r"\.com/lenny|go to [a-z0-9]+\.com|free trial|percent off",
    re.I,
)
CARRY_OVER_MAX_WORDS = 60


@dataclass(frozen=True)
class Turn:
    speaker: str
    start_seconds: int | None
    text: str

    @property
    def words(self) -> int:
        return len(self.text.split())


@dataclass(frozen=True)
class ChunkDraft:
    index: int
    speakers: str
    start_seconds: int | None
    text: str
    word_count: int
    is_sponsor: bool


@dataclass
class ParsedEpisode:
    slug: str
    title: str
    guest: str
    publish_date: date | None
    youtube_url: str | None
    video_id: str | None
    duration_seconds: int | None
    description: str | None
    keywords: list[str]
    body: str
    content_hash: str
    body_hash: str
    word_count: int
    extra: dict[str, Any] = field(default_factory=dict)


def parse_timestamp(ts: str) -> int:
    parts = [int(p) for p in ts.split(":")]
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def format_timestamp(seconds: int | None) -> str:
    if seconds is None:
        return ""
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_episode(slug: str, raw: str) -> ParsedEpisode:
    """Split YAML front matter from the transcript body and normalise metadata."""
    meta: dict[str, Any] = {}
    body = raw
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            meta = yaml.safe_load(raw[3:end]) or {}
            body = raw[end + 4 :]
    marker = body.find("## Transcript")
    if marker != -1:
        body = body[marker + len("## Transcript") :]

    publish = meta.get("publish_date")
    if isinstance(publish, str):
        try:
            publish = date.fromisoformat(publish)
        except ValueError:
            publish = None
    duration = meta.get("duration_seconds")
    title = str(meta.get("title") or slug.replace("-", " ").title()).strip()
    guest = str(meta.get("guest") or slug.replace("-", " ").title()).strip()
    keywords = [str(k).strip() for k in (meta.get("keywords") or []) if str(k).strip()]
    return ParsedEpisode(
        slug=slug,
        title=" ".join(title.split()),
        guest=guest,
        publish_date=publish if isinstance(publish, date) else None,
        youtube_url=meta.get("youtube_url"),
        video_id=meta.get("video_id"),
        duration_seconds=int(duration) if isinstance(duration, (int, float)) else None,
        description=(meta.get("description") or None),
        keywords=keywords,
        body=body.strip(),
        content_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        body_hash=hashlib.sha256(" ".join(body.split()).encode("utf-8")).hexdigest(),
        word_count=len(body.split()),
    )


def split_turns(body: str) -> list[Turn]:
    """Return speaker turns. Falls back to paragraphs when no speaker labels exist."""
    matches = list(TURN_RE.finditer(body))
    if not matches:
        paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        return [Turn(speaker="", start_seconds=None, text=" ".join(p.split())) for p in paras]

    turns: list[Turn] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        text = " ".join(body[m.end() : end].split())
        if text:
            turns.append(Turn(m.group("speaker").strip(), parse_timestamp(m.group("ts")), text))
    return turns


SPONSOR_RE_INLINE = re.compile(
    r"(this|today'?s)( entire)? (episode|podcast)('s| is) (brought to you|sponsored) by", re.I
)
SPONSOR_MAX_SECONDS = 150


def drop_sponsor_reads(turns: list[Turn]) -> tuple[list[Turn], int]:
    """Remove ad reads ("This episode is brought to you by ...").

    An ad read can start mid-turn and usually continues over a few consecutive
    turns by the same speaker. We keep the text before the ad phrase, drop the
    rest of that turn, and drop continuation turns by the same speaker while
    they stay within SPONSOR_MAX_SECONDS of the ad start, unless they look like
    the conversation resuming (a welcome or a question).
    """
    kept: list[Turn] = []
    dropped = 0
    ad: Turn | None = None
    for turn in turns:
        match = SPONSOR_RE_INLINE.search(turn.text)
        if match:
            ad = turn
            dropped += 1
            prefix = turn.text[: match.start()].strip()
            if len(prefix.split()) >= 5:
                kept.append(Turn(turn.speaker, turn.start_seconds, prefix))
            continue
        if ad is not None:
            same_speaker = turn.speaker == ad.speaker
            within = (
                turn.start_seconds is not None
                and ad.start_seconds is not None
                and turn.start_seconds - ad.start_seconds <= SPONSOR_MAX_SECONDS
            )
            resumes = "welcome" in turn.text.lower() or turn.text.rstrip().endswith("?")
            if same_speaker and within and not resumes:
                dropped += 1
                continue
            ad = None
        kept.append(turn)
    return kept, dropped


HOST_NAMES = ("lenny", "lenny rachitsky")


def dominant_guest(turns: list[Turn]) -> str | None:
    """The non-host speaker with the most words (the real guest)."""
    words: dict[str, int] = {}
    for t in turns:
        if t.speaker and t.speaker.lower() not in HOST_NAMES:
            words[t.speaker] = words.get(t.speaker, 0) + t.words
    return max(words, key=words.__getitem__) if words else None


def title_mentions(title: str, person: str) -> bool:
    """True if the person's surname (or single name) appears in the title."""
    parts = [p for p in re.split(r"\s+", person.strip()) if p]
    return bool(parts) and parts[-1].lower() in title.lower()


def _split_long_turn(turn: Turn, max_words: int) -> list[Turn]:
    if turn.words <= max_words:
        return [turn]
    pieces: list[Turn] = []
    buf: list[str] = []
    count = 0
    for sentence in SENTENCE_RE.split(turn.text):
        n = len(sentence.split())
        if buf and count + n > max_words:
            pieces.append(Turn(turn.speaker, turn.start_seconds, " ".join(buf)))
            buf, count = [], 0
        buf.append(sentence)
        count += n
    if buf:
        pieces.append(Turn(turn.speaker, turn.start_seconds, " ".join(buf)))
    return pieces


def _render(turns: list[Turn]) -> str:
    return "\n".join(f"{t.speaker}: {t.text}" if t.speaker else t.text for t in turns)


def is_sponsor_text(text: str) -> bool:
    return len(SPONSOR_RE.findall(text)) >= 2


def chunk_turns(turns: list[Turn], target_words: int = 280, max_words: int = 420) -> list[ChunkDraft]:
    expanded: list[Turn] = []
    for t in turns:
        expanded.extend(_split_long_turn(t, max_words))

    groups: list[list[Turn]] = []
    current: list[Turn] = []
    count = 0
    for turn in expanded:
        if current and count + turn.words > target_words:
            groups.append(current)
            tail = current[-1]
            # Carry a short trailing turn (usually the interviewer's question)
            # forward so the answer that follows keeps its context.
            if tail.words <= CARRY_OVER_MAX_WORDS and len(current) > 1:
                current, count = [tail], tail.words
            else:
                current, count = [], 0
        current.append(turn)
        count += turn.words
    if current:
        groups.append(current)

    drafts: list[ChunkDraft] = []
    for group in groups:
        text = _render(group)
        speakers = ", ".join(dict.fromkeys(t.speaker for t in group if t.speaker))
        drafts.append(
            ChunkDraft(
                index=len(drafts),
                speakers=speakers,
                start_seconds=next((t.start_seconds for t in group if t.start_seconds is not None), None),
                text=text,
                word_count=len(text.split()),
                is_sponsor=is_sponsor_text(text),
            )
        )
    return drafts


def chunk_episode(episode: ParsedEpisode, target_words: int = 280, max_words: int = 420) -> list[ChunkDraft]:
    turns, _ = drop_sponsor_reads(split_turns(episode.body))
    return chunk_turns(turns, target_words, max_words)
