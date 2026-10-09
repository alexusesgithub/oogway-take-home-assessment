"""Knowledge-base ingestion: load -> parse -> chunk -> index -> (embed).

* **Load**: shallow `git clone`/`fetch` of TRANSCRIPTS_REPO_URL at
  TRANSCRIPTS_REF into TRANSCRIPTS_DIR. If the directory exists and is not a
  git checkout (offline mirror), it is used as-is.
* **Refresh**: idempotent. Each episode's SHA-256 content hash is compared with
  the stored one; unchanged episodes are skipped, changed ones re-chunked,
  deleted ones removed. Re-running is cheap and safe.
* **Trace**: every chunk keeps episode slug + timestamp; every episode keeps a
  commit-pinned GitHub URL; every run is recorded in `ingestion_runs`.
* **Concurrency**: a Postgres advisory lock guarantees one ingestion at a time
  even with several API replicas.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import Chunk, Episode, IngestionRun
from app.retrieval.chunker import (
    ParsedEpisode,
    chunk_episode,
    dominant_guest,
    parse_episode,
    split_turns,
    title_mentions,
)
from app.retrieval.embeddings import OllamaEmbedder

log = structlog.get_logger("ingest")

ADVISORY_LOCK_ID = 74_210_001
_INSERT_CHUNK_SQL = text(
    """
    INSERT INTO chunks (episode_id, chunk_index, speakers, start_seconds, text, word_count, is_sponsor, tsv)
    VALUES (
        :episode_id, :chunk_index, :speakers, :start_seconds, :text, :word_count, :is_sponsor,
        setweight(to_tsvector('english', :title), 'A')
        || setweight(to_tsvector('english', :keywords), 'B')
        || setweight(to_tsvector('english', :speakers), 'C')
        || setweight(to_tsvector('english', :text), 'D')
    )
    """
)


@dataclass
class IngestStats:
    episodes_seen: int = 0
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    duplicates_skipped: int = 0
    metadata_withheld: int = 0
    failed: list[str] = field(default_factory=list)
    chunks_written: int = 0
    duration_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "failed": self.failed[:50]}


class IngestionInProgress(RuntimeError):
    pass


async def _run(cmd: list[str], cwd: Path | None = None) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])} failed: {err.decode(errors='replace')[:400]}")
    return out.decode().strip()


async def sync_source(settings: Settings) -> tuple[Path, str | None]:
    """Clone or fast-forward the transcript repository. Returns (path, commit sha)."""
    path = Path(settings.transcripts_dir).resolve()  # noqa: ASYNC240 - one-off background job
    if (path / "episodes").is_dir() and not (path / ".git").exists():
        log.info("source_local_mirror", path=str(path))
        return path, None
    if not (path / ".git").exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        log.info("source_clone", url=settings.transcripts_repo_url, ref=settings.transcripts_ref)
        await _run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                settings.transcripts_ref,
                settings.transcripts_repo_url,
                str(path),
            ]
        )
    else:
        log.info("source_fetch", ref=settings.transcripts_ref)
        await _run(["git", "fetch", "--depth", "1", "origin", settings.transcripts_ref], cwd=path)
        await _run(["git", "reset", "--hard", "FETCH_HEAD"], cwd=path)
    sha = await _run(["git", "rev-parse", "HEAD"], cwd=path)
    return path, sha


def _source_url(settings: Settings, sha: str | None, slug: str) -> str:
    ref = sha or settings.transcripts_ref
    return f"{settings.transcripts_repo_url.rstrip('/')}/blob/{ref}/episodes/{slug}/transcript.md"


@dataclass
class EpisodePlan:
    """A parsed episode plus the data-quality decisions made about it."""

    episode: ParsedEpisode
    guest: str
    metadata_verified: bool


def plan_episodes(parsed: list[ParsedEpisode]) -> tuple[list[EpisodePlan], list[str]]:
    """Apply data-quality guardrails to the source repository (pure, unit-tested).

    The public transcript archive has known defects: exact duplicate
    transcripts under two folders, and scraped YouTube metadata attached to
    the wrong transcript (two folders sharing one video_id). We
    * skip exact duplicate bodies (first slug alphabetically wins);
    * take the guest name from the transcript's own speaker labels;
    * withhold title/YouTube link when the video_id is shared AND the title
      does not mention the guest who actually speaks in the transcript, rather
      than cite a wrong video.
    """
    video_counts: dict[str, int] = {}
    for ep in parsed:
        if ep.video_id:
            video_counts[ep.video_id] = video_counts.get(ep.video_id, 0) + 1

    plans: list[EpisodePlan] = []
    duplicates: list[str] = []
    seen_bodies: set[str] = set()
    for ep in sorted(parsed, key=lambda e: e.slug):
        if ep.body_hash in seen_bodies:
            duplicates.append(ep.slug)
            continue
        seen_bodies.add(ep.body_hash)
        speaker = dominant_guest(split_turns(ep.body))
        guest = speaker or ep.guest
        shared_video = bool(ep.video_id) and video_counts.get(ep.video_id or "", 0) > 1
        verified = not (shared_video and speaker and not title_mentions(ep.title, speaker))
        plans.append(EpisodePlan(ep, guest, verified))
    return plans, duplicates


async def _upsert_episode(
    db: AsyncSession, settings: Settings, sha: str | None, plan: EpisodePlan, existing: Episode | None
) -> int:
    ep = plan.episode
    drafts = chunk_episode(ep, settings.chunk_target_words, settings.chunk_max_words)
    verified = plan.metadata_verified
    title = ep.title if verified else f"{plan.guest} on Lenny's Podcast"
    values = dict(
        title=title,
        guest=plan.guest,
        publish_date=ep.publish_date if verified else None,
        youtube_url=ep.youtube_url if verified else None,
        video_id=ep.video_id if verified else None,
        duration_seconds=ep.duration_seconds if verified else None,
        description=ep.description if verified else None,
        keywords=ep.keywords,
        source_path=_source_url(settings, sha, ep.slug),
        content_hash=ep.content_hash,
        word_count=ep.word_count,
        metadata_verified=verified,
        updated_at=datetime.now(UTC),
    )
    if existing is None:
        episode = Episode(slug=ep.slug, **values)
        db.add(episode)
        await db.flush()
        episode_id = episode.id
    else:
        episode_id = existing.id
        await db.execute(update(Episode).where(Episode.id == episode_id).values(**values))
        await db.execute(delete(Chunk).where(Chunk.episode_id == episode_id))

    title_field = f"{title} {plan.guest}"
    keywords_field = " ".join(ep.keywords)
    if drafts:
        await db.execute(
            _INSERT_CHUNK_SQL,
            [
                {
                    "episode_id": episode_id,
                    "chunk_index": d.index,
                    "speakers": d.speakers,
                    "start_seconds": d.start_seconds,
                    "text": d.text,
                    "word_count": d.word_count,
                    "is_sponsor": d.is_sponsor,
                    "title": title_field,
                    "keywords": keywords_field,
                }
                for d in drafts
            ],
        )
    return len(drafts)


async def ingest(
    factory: async_sessionmaker[AsyncSession], settings: Settings, trigger: str = "cli", full: bool = False
) -> IngestStats:
    """Run one ingestion pass. Raises IngestionInProgress if another is running."""
    started = time.perf_counter()
    stats = IngestStats()
    async with factory() as lock_db:
        got = (await lock_db.execute(text("SELECT pg_try_advisory_lock(:id)"), {"id": ADVISORY_LOCK_ID})).scalar()
        if not got:
            raise IngestionInProgress("Another ingestion run is in progress.")
        try:
            async with factory() as db:
                run = IngestionRun(status="running", trigger=trigger)
                db.add(run)
                await db.commit()
                run_id = run.id
            log.info("ingest_started", run_id=run_id, trigger=trigger)
            try:
                root, sha = await sync_source(settings)
                await _ingest_tree(factory, settings, root, sha, stats, full=full)
                stats.duration_ms = int((time.perf_counter() - started) * 1000)
                async with factory() as db:
                    await db.execute(
                        update(IngestionRun)
                        .where(IngestionRun.id == run_id)
                        .values(status="succeeded", source_ref=sha, stats=stats.as_dict(), finished_at=func.now())
                    )
                    await db.commit()
                log.info("ingest_succeeded", run_id=run_id, source_ref=sha, **stats.as_dict())
            except Exception as exc:
                stats.duration_ms = int((time.perf_counter() - started) * 1000)
                async with factory() as db:
                    await db.execute(
                        update(IngestionRun)
                        .where(IngestionRun.id == run_id)
                        .values(status="failed", error=str(exc)[:2000], stats=stats.as_dict(), finished_at=func.now())
                    )
                    await db.commit()
                log.error("ingest_failed", run_id=run_id, error=str(exc)[:500])
                raise
        finally:
            await lock_db.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": ADVISORY_LOCK_ID})
            await lock_db.commit()
    return stats


async def _ingest_tree(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    root: Path,
    sha: str | None,
    stats: IngestStats,
    full: bool = False,
) -> None:
    episode_dirs = sorted(p for p in (root / "episodes").iterdir() if (p / "transcript.md").is_file())
    if not episode_dirs:
        raise RuntimeError(f"No transcripts found under {root / 'episodes'}")

    parsed: list[ParsedEpisode] = []
    for ep_dir in episode_dirs:
        stats.episodes_seen += 1
        try:
            parsed.append(parse_episode(ep_dir.name, (ep_dir / "transcript.md").read_text(encoding="utf-8")))
        except Exception as exc:  # one bad file must not abort the whole refresh
            stats.failed.append(ep_dir.name)
            log.warning("episode_parse_failed", slug=ep_dir.name, error=str(exc)[:300])
    plans, duplicates = plan_episodes(parsed)
    stats.duplicates_skipped = len(duplicates)
    stats.metadata_withheld = sum(not p.metadata_verified for p in plans)
    if duplicates:
        log.info("duplicate_transcripts_skipped", slugs=duplicates)

    async with factory() as db:
        existing = {e.slug: e for e in (await db.execute(select(Episode))).scalars()}

    for plan in plans:
        slug = plan.episode.slug
        current = existing.get(slug)
        if not full and current is not None and current.content_hash == plan.episode.content_hash:
            stats.unchanged += 1
            continue
        try:
            async with factory() as db:
                stats.chunks_written += await _upsert_episode(db, settings, sha, plan, current)
                await db.commit()
            if current is None:
                stats.added += 1
            else:
                stats.updated += 1
        except Exception as exc:
            stats.failed.append(slug)
            log.warning("episode_ingest_failed", slug=slug, error=str(exc)[:300])

    keep = {p.episode.slug for p in plans} | set(stats.failed)
    removed = [slug for slug in existing if slug not in keep]
    async with factory() as db:
        if removed:
            await db.execute(delete(Episode).where(Episode.slug.in_(removed)))
            stats.removed = len(removed)
        await db.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY kb_lexeme_stats"))
        await db.commit()


async def embed_pending(factory: async_sessionmaker[AsyncSession], settings: Settings, limit: int | None = None) -> int:
    """Back-fill embeddings for chunks that do not have one yet."""
    embedder = OllamaEmbedder(settings)
    done = 0
    while limit is None or done < limit:
        async with factory() as db:
            rows = (
                await db.execute(
                    select(Chunk.id, Chunk.text)
                    .where(Chunk.embedding.is_(None), Chunk.is_sponsor.is_(False))
                    .order_by(Chunk.id)
                    .limit(settings.embed_batch_size)
                )
            ).all()
            if not rows:
                break
            vectors = await embedder.embed_documents([r.text for r in rows])
            for row, vec in zip(rows, vectors, strict=True):
                await db.execute(update(Chunk).where(Chunk.id == row.id).values(embedding=vec))
            await db.commit()
        done += len(rows)
        if done % (settings.embed_batch_size * 20) == 0:
            log.info("embed_progress", embedded=done)
    log.info("embed_done", embedded=done)
    return done


async def kb_status(db: AsyncSession) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                """
                SELECT (SELECT count(*) FROM episodes) AS episodes,
                       (SELECT count(*) FROM chunks) AS chunks,
                       (SELECT count(*) FROM chunks WHERE embedding IS NOT NULL) AS embedded
                """
            )
        )
    ).one()
    last = (await db.execute(select(IngestionRun).order_by(IngestionRun.id.desc()).limit(1))).scalar_one_or_none()
    return {
        "episodes": row.episodes,
        "chunks": row.chunks,
        "embedded_chunks": row.embedded,
        "last_run": None
        if last is None
        else {
            "id": last.id,
            "status": last.status,
            "trigger": last.trigger,
            "source_ref": last.source_ref,
            "started_at": last.started_at.isoformat(),
            "finished_at": last.finished_at.isoformat() if last.finished_at else None,
            "stats": last.stats,
            "error": last.error,
        },
    }
