"""Hybrid retrieval over transcript chunks.

1. **Lexical (always on)** - PostgreSQL full-text search. The user question is
   normalised by Postgres itself (stemming + stop words) and turned into an
   OR-query, ranked with `ts_rank_cd`; chunks that also match *all* terms get a
   bonus. Episode title/guest/keywords are indexed with a higher weight than
   the spoken text.
2. **Semantic (optional)** - pgvector cosine similarity over Ollama
   embeddings, when EMBEDDINGS_ENABLED=true and chunks have been embedded.
3. **Fusion** - Reciprocal Rank Fusion (RRF) of the two lists.
4. **Diversity** - at most N chunks per episode, so answers synthesise across
   guests instead of quoting one episode five times.
"""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.retrieval.chunker import format_timestamp

log = structlog.get_logger("retrieval")

# Growth/PM jargon users type as acronyms but guests say out loud.
JARGON = {
    "pmf": "product market fit",
    "plg": "product led growth",
    "slg": "sales led growth",
    "nrr": "net revenue retention",
    "ndr": "net dollar retention",
    "ltv": "lifetime value",
    "cac": "customer acquisition cost",
    "arr": "annual recurring revenue",
    "mrr": "monthly recurring revenue",
    "okr": "objectives key results",
    "okrs": "objectives key results",
    "kpi": "metrics",
    "kpis": "metrics",
    "dau": "daily active users",
    "mau": "monthly active users",
    "nps": "net promoter score",
    "seo": "search engine optimization",
    "gtm": "go to market",
    "pm": "product manager",
    "pms": "product managers",
    "ab": "experiment",
    "a/b": "experiment",
}
_WORD_RE = re.compile(r"[A-Za-z0-9/]+")

PREFILTER_SIZE = 800

# Question boilerplate that is not in Postgres' English stop-word list but
# carries no topical signal ("what's the best way to think about ...").
FILLER_WORDS = frozenset(
    """think thinking best way ways good great really actually tips advice approach
    recommend recommendation guest guests lenny podcast episode say says said talk talked
    someone anyone people get getting use using know tell explain give me us please
    should would could can do does did""".split()
)

FOLLOWUP_HINTS = re.compile(
    r"\b(it|that|this|these|those|they|them|he|she|his|her|their|more|elaborate|example|examples|"
    r"why|how so|what else|expand|above|previous|same)\b",
    re.I,
)


@dataclass
class RetrievedChunk:
    chunk_id: int
    episode_slug: str
    title: str
    guest: str
    publish_date: date | None
    youtube_url: str | None
    source_url: str
    start_seconds: int | None
    speakers: str
    text: str
    score: float
    coverage: float = 0.0  # share of the query's IDF mass found in this chunk
    lexical_rank: int | None = None
    vector_rank: int | None = None

    @classmethod
    def from_source(cls, source: dict[str, Any]) -> RetrievedChunk:
        """Rehydrate from a stored citation snapshot (reuse evidence across turns)."""
        publish = source.get("publish_date")
        youtube = source.get("youtube_url")
        if youtube and "&t=" in youtube:
            youtube = youtube.split("&t=")[0]
        elif youtube and "?t=" in youtube:
            youtube = youtube.split("?t=")[0]
        return cls(
            chunk_id=int(source["chunk_id"]),
            episode_slug=source["episode_slug"],
            title=source["title"],
            guest=source["guest"],
            publish_date=date.fromisoformat(publish) if publish else None,
            youtube_url=youtube,
            source_url=source.get("source_url") or "",
            start_seconds=source.get("start_seconds"),
            speakers=source.get("speakers") or "",
            text=source["excerpt"],
            score=float(source.get("score") or 0.0),
            coverage=float(source.get("coverage") or 0.0),
        )

    def to_source(self, source_id: str) -> dict[str, Any]:
        """Serializable citation record (stored with the message)."""
        return {
            "id": source_id,
            "chunk_id": self.chunk_id,
            "episode_slug": self.episode_slug,
            "title": self.title,
            "guest": self.guest,
            "publish_date": self.publish_date.isoformat() if self.publish_date else None,
            "timestamp": format_timestamp(self.start_seconds),
            "start_seconds": self.start_seconds,
            "youtube_url": deep_link(self.youtube_url, self.start_seconds),
            "source_url": self.source_url,
            "speakers": self.speakers,
            "excerpt": self.text,
            "score": round(self.score, 4),
            "coverage": round(self.coverage, 3),
        }


@dataclass
class SearchResult:
    query: str
    expanded_query: str
    chunks: list[RetrievedChunk]
    mode: str  # lexical | hybrid
    latency_ms: int
    candidates: int = 0
    debug: dict[str, Any] = field(default_factory=dict)

    @property
    def best_coverage(self) -> float:
        return max((c.coverage for c in self.chunks), default=0.0)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["chunks"] = [c.to_source(f"S{i + 1}") for i, c in enumerate(self.chunks)]
        return data


def deep_link(youtube_url: str | None, start_seconds: int | None) -> str | None:
    if not youtube_url:
        return None
    if not start_seconds:
        return youtube_url
    sep = "&" if "?" in youtube_url else "?"
    return f"{youtube_url}{sep}t={int(start_seconds)}s"


def expand_query(query: str) -> str:
    """Drop question filler and append long-form expansions for jargon (pure)."""
    tokens = _WORD_RE.findall(query)
    kept = [t for t in tokens if t.lower() not in FILLER_WORDS] or tokens
    extra: list[str] = []
    for token in kept:
        expansion = JARGON.get(token.lower())
        if expansion and expansion not in extra:
            extra.append(expansion)
    return " ".join(kept + extra)


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_SPEAKER_LINE_RE = re.compile(r"^([^:\n]{2,60}):\s+(.*)$")


def focus_excerpt(text: str, query: str, max_words: int) -> str:
    """Keep the `max_words` window of sentences densest in query terms.

    Chunks are ~280 words; a CPU-bound local model pays for every prompt
    token, so for small context budgets we send only the part of each chunk
    that actually talks about the question (5-letter prefixes approximate
    stemming). Speaker labels are re-inserted so that a question from the
    host is never attributed to the guest. Pure and deterministic.
    """
    if len(text.split()) <= max_words:
        return text
    terms = {t.lower()[:5] for t in _WORD_RE.findall(query) if len(t) >= 4 and t.lower() not in FILLER_WORDS}

    units: list[tuple[str, str]] = []  # (speaker, sentence)
    for line in text.splitlines():
        m = _SPEAKER_LINE_RE.match(line.strip())
        speaker, body = (m.group(1), m.group(2)) if m else ("", line.strip())
        units.extend((speaker, sent) for sent in _SENTENCE_SPLIT_RE.split(body) if sent.strip())
    if not units:
        return " ".join(text.split()[:max_words])
    lengths = [len(u[1].split()) for u in units]
    hits = [sum(1 for w in u[1].lower().split() if w.strip(".,!?\"'()")[:5] in terms) for u in units]

    # Rank windows by query-term hits; on ties prefer the window with the least
    # irrelevant lead-in (fewer zero-hit sentences before the first hit).
    best = (-1, 0, 0, 1)  # (score, -lead_in, start, end)
    for start in range(len(units)):
        total, score, end = 0, 0, start
        while end < len(units) and total + lengths[end] <= max_words:
            total += lengths[end]
            score += hits[end]
            end += 1
        if end == start:  # one sentence longer than the whole budget
            end, score = start + 1, hits[start]
        lead_in = next((i for i, h in enumerate(hits[start:end]) if h), end - start)
        candidate = (score, -lead_in, start, end)
        if candidate[:2] > best[:2]:
            best = candidate
    _, _, start, end = best

    lines: list[str] = []
    current = None
    for speaker, sentence in units[start:end]:
        if speaker != current:
            lines.append(f"{speaker}: {sentence}" if speaker else sentence)
            current = speaker
        else:
            lines[-1] += f" {sentence}"
    excerpt = "\n".join(lines)
    if end - start == 1 and lengths[start] > max_words:  # clip only a single oversized sentence
        excerpt = " ".join(excerpt.split(" ")[: max_words + 2])
    return f"{'… ' if start > 0 else ''}{excerpt}{' …' if end < len(units) else ''}"


def query_bigrams(query: str) -> list[str]:
    """Adjacent word pairs; Postgres drops pairs that contain a stop word."""
    words = _WORD_RE.findall(query.lower())
    return [f"{a} {b}" for a, b in zip(words, words[1:], strict=False)][:12]


QUESTION_WORDS = frozenset(
    "what how why when which who where is are was the a an of for to in on with about and or but so it this that".split()
)
FOLLOWUP_START_RE = re.compile(r"^\s*(and|but|also|so|what about|how about|why|same)\b", re.I)


def build_retrieval_query(message: str, previous_user_messages: Sequence[str]) -> str:
    """Make follow-ups retrievable: "what about for B2B?" alone matches nothing,
    so short or anaphoric follow-ups are combined with the previous question."""
    message = message.strip()
    if not previous_user_messages:
        return message
    words = len(message.split())
    content = [w for w in _WORD_RE.findall(message) if w.lower() not in FILLER_WORDS | QUESTION_WORDS]
    looks_like_followup = (
        FOLLOWUP_START_RE.match(message) is not None
        or len(content) <= 2
        or (words <= 20 and FOLLOWUP_HINTS.search(message) is not None)
    )
    if looks_like_followup:
        return f"{previous_user_messages[-1].strip()} {message}"
    return message


def rrf_fuse(rankings: Sequence[Sequence[int]], k: int = 60) -> dict[int, float]:
    """Reciprocal Rank Fusion: score(d) = sum(1 / (k + rank_i(d)))."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return scores


def diversify(chunks: Sequence[RetrievedChunk], top_k: int, max_per_episode: int) -> list[RetrievedChunk]:
    picked: list[RetrievedChunk] = []
    per_episode: dict[str, int] = {}
    for chunk in chunks:
        if per_episode.get(chunk.episode_slug, 0) >= max_per_episode:
            continue
        picked.append(chunk)
        per_episode[chunk.episode_slug] = per_episode.get(chunk.episode_slug, 0) + 1
        if len(picked) >= top_k:
            break
    return picked


# Lexical ranking = BM25 computed inside Postgres (two stages):
#   1. prefilter: chunks matching any query term, ordered by the share of the
#      query's IDF mass they contain (cheap, uses the GIN index);
#   2. BM25 on the best `prefilter` candidates, with exact term frequencies
#      read from the tsvector (unnest) and a field boost for matches in the
#      episode title (weight A) and keywords (weight B), i.e. BM25F-lite.
# IDF comes from the kb_lexeme_stats materialized view (refreshed on ingest).
# Lexemes are already normalised, so per-term queries use the 'simple' config.
_LEXICAL_SQL = text(
    """
    WITH stats AS (
        SELECT greatest(count(*), 1) AS total, greatest(avg(word_count), 1) AS avgdl FROM chunks
    ),
    terms AS (
        SELECT DISTINCT t.lexeme,
               ln(1 + (stats.total - coalesce(s.ndoc, 0) + 0.5) / (coalesce(s.ndoc, 0) + 0.5)) AS idf
        FROM unnest(tsvector_to_array(to_tsvector('english', :query))) AS t(lexeme)
        CROSS JOIN stats
        LEFT JOIN kb_lexeme_stats s ON s.word = t.lexeme
    ),
    q AS (
        SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS any_terms,
               sum(idf) AS idf_total
        FROM terms
    ),
    prefilter AS (
        SELECT c.id, c.tsv, c.word_count,
               (SELECT coalesce(sum(t.idf), 0) FROM terms t
                 WHERE c.tsv @@ to_tsquery('simple', quote_literal(t.lexeme))) / q.idf_total AS coverage
        FROM chunks c, q
        WHERE c.tsv @@ q.any_terms AND NOT c.is_sponsor
        ORDER BY coverage DESC, ts_rank_cd('{0.1, 0.2, 0.4, 1.0}', c.tsv, q.any_terms, 1) DESC
        LIMIT :prefilter
    ),
    tf AS (
        SELECT p.id, p.word_count, p.coverage, t.idf,
               (SELECT count(*) FROM unnest(u.weights) w WHERE w IN ('C', 'D'))
                 + CASE WHEN 'A' = ANY(u.weights) THEN 2.0 ELSE 0 END
                 + CASE WHEN 'B' = ANY(u.weights) THEN 0.5 ELSE 0 END AS freq
        FROM prefilter p
        CROSS JOIN LATERAL unnest(p.tsv) AS u
        JOIN terms t ON t.lexeme = u.lexeme
    ),
    bm25 AS (
        SELECT tf.id,
               sum(tf.idf * (tf.freq * 2.2) / (tf.freq + 1.2 * (0.25 + 0.75 * tf.word_count / stats.avgdl)))
                 / max(q.idf_total) AS bm25
        FROM tf, stats, q
        GROUP BY tf.id
    ),
    -- Proximity: adjacent query words that also appear adjacent in the chunk
    -- ("growth team", "product market") get a bonus BM25 alone cannot express.
    bigrams AS (
        SELECT phraseto_tsquery('english', b) AS pq
        FROM unnest(CAST(:bigrams AS text[])) AS b
        WHERE numnode(phraseto_tsquery('english', b)) = 3
    )
    SELECT p.id, p.coverage,
           b.bm25 + 0.3 * (SELECT count(*) FROM bigrams WHERE p.tsv @@ bigrams.pq) AS score
    FROM prefilter p JOIN bm25 b ON b.id = p.id
    ORDER BY score DESC
    LIMIT :limit
    """
)

_VECTOR_SQL = text(
    """
    SELECT c.id, 1 - (c.embedding <=> CAST(:embedding AS vector)) AS score
    FROM chunks c
    WHERE c.embedding IS NOT NULL AND NOT c.is_sponsor
    ORDER BY c.embedding <=> CAST(:embedding AS vector)
    LIMIT :limit
    """
)

_HYDRATE_SQL = text(
    """
    SELECT c.id, c.text, c.speakers, c.start_seconds,
           e.slug, e.title, e.guest, e.publish_date, e.youtube_url, e.source_path
    FROM chunks c JOIN episodes e ON e.id = c.episode_id
    WHERE c.id = ANY(:ids)
    """
)


def _has_terms(query: str) -> bool:
    return bool(_WORD_RE.search(query))


_NEXT_CHUNK_SQL = text(
    """
    SELECT prev.id AS prev_id, nxt.text
    FROM chunks prev
    JOIN chunks nxt ON nxt.episode_id = prev.episode_id AND nxt.chunk_index = prev.chunk_index + 1
    WHERE prev.id = ANY(:ids) AND NOT nxt.is_sponsor
    """
)


def merge_continuation(text: str, following: str) -> str:
    """Append the next chunk, dropping the turn it repeats (the chunker carries
    a short trailing turn into the next chunk)."""
    tail = text.rstrip().splitlines()[-1:] if text.strip() else []
    lines = following.strip().splitlines()
    if tail and lines and lines[0].strip() == tail[0].strip():
        lines = lines[1:]
    return text.rstrip() + ("\n" + "\n".join(lines) if lines else "")


class Retriever:
    def __init__(self, embedder: Any | None = None, candidate_pool: int = 60) -> None:
        self.embedder = embedder
        self.candidate_pool = candidate_pool

    async def _append_next_chunks(self, db: AsyncSession, chunks: list[RetrievedChunk]) -> None:
        """Small-to-big retrieval: an answer often continues past a chunk
        boundary (the question in one chunk, the substance in the next)."""
        rows = (await db.execute(_NEXT_CHUNK_SQL, {"ids": [c.chunk_id for c in chunks]})).all()
        following = {r.prev_id: r.text for r in rows}
        for chunk in chunks:
            if chunk.chunk_id in following:
                chunk.text = merge_continuation(chunk.text, following[chunk.chunk_id])

    async def search(
        self,
        db: AsyncSession,
        query: str,
        *,
        top_k: int = 6,
        max_per_episode: int = 2,
        exclude_chunk_ids: Sequence[int] = (),
        expand_neighbors: bool = False,
    ) -> SearchResult:
        started = time.perf_counter()
        expanded = expand_query(query)
        if not _has_terms(expanded):
            return SearchResult(query, expanded, [], "lexical", 0)

        lexical_rows = (
            await db.execute(
                _LEXICAL_SQL,
                {
                    "query": expanded,
                    "bigrams": query_bigrams(expanded),
                    "limit": self.candidate_pool,
                    "prefilter": PREFILTER_SIZE,
                },
            )
        ).all()
        lexical_ids = [r.id for r in lexical_rows]
        lexical_scores = {r.id: float(r.score) for r in lexical_rows}
        coverage = {r.id: float(r.coverage) for r in lexical_rows}

        vector_ids: list[int] = []
        mode = "lexical"
        if self.embedder is not None and self.embedder.enabled:
            try:
                vector = await self.embedder.embed_query(query)
                rows = (await db.execute(_VECTOR_SQL, {"embedding": str(vector), "limit": self.candidate_pool})).all()
                vector_ids = [r.id for r in rows]
                mode = "hybrid" if vector_ids else "lexical"
            except Exception as exc:  # embeddings are an enhancement; never fail the turn
                log.warning("vector_search_failed", error=str(exc)[:300])

        if vector_ids:
            fused = rrf_fuse([lexical_ids, vector_ids])
        else:
            fused = lexical_scores
        excluded = set(exclude_chunk_ids)
        ordered_ids = [cid for cid, _ in sorted(fused.items(), key=lambda kv: -kv[1]) if cid not in excluded]
        if not ordered_ids:
            return SearchResult(query, expanded, [], mode, int((time.perf_counter() - started) * 1000))

        rows = (await db.execute(_HYDRATE_SQL, {"ids": ordered_ids})).all()
        by_id = {r.id: r for r in rows}
        lexical_pos = {cid: i + 1 for i, cid in enumerate(lexical_ids)}
        vector_pos = {cid: i + 1 for i, cid in enumerate(vector_ids)}
        candidates = [
            RetrievedChunk(
                chunk_id=cid,
                episode_slug=by_id[cid].slug,
                title=by_id[cid].title,
                guest=by_id[cid].guest,
                publish_date=by_id[cid].publish_date,
                youtube_url=by_id[cid].youtube_url,
                source_url=by_id[cid].source_path,
                start_seconds=by_id[cid].start_seconds,
                speakers=by_id[cid].speakers,
                text=by_id[cid].text,
                score=fused[cid],
                coverage=coverage.get(cid, 0.0),
                lexical_rank=lexical_pos.get(cid),
                vector_rank=vector_pos.get(cid),
            )
            for cid in ordered_ids
            if cid in by_id
        ]
        chunks = diversify(candidates, top_k, max_per_episode)
        if expand_neighbors and chunks:
            await self._append_next_chunks(db, chunks)
        latency = int((time.perf_counter() - started) * 1000)
        log.info(
            "retrieval_done",
            mode=mode,
            candidates=len(candidates),
            returned=len(chunks),
            top_score=round(chunks[0].score, 4) if chunks else None,
            best_coverage=round(max((c.coverage for c in chunks), default=0.0), 3),
            latency_ms=latency,
        )
        return SearchResult(query, expanded, chunks, mode, latency, candidates=len(candidates))
