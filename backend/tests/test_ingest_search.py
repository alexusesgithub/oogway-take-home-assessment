"""Ingestion + retrieval against a real PostgreSQL (fixture transcripts)."""

import pytest
from sqlalchemy import select, text

from app.db.models import Chunk, Episode, IngestionRun
from app.retrieval.ingest import ingest, kb_status
from app.retrieval.search import Retriever

pytestmark = pytest.mark.db


async def test_initial_ingest_indexes_episodes_and_records_run(kb, session_factory):
    assert kb.added == 4  # 5 folders, 1 exact duplicate skipped
    assert kb.duplicates_skipped == 1
    assert kb.metadata_withheld == 1
    assert kb.failed == []
    async with session_factory() as db:
        status = await kb_status(db)
        assert status["episodes"] == 4
        assert status["chunks"] == kb.chunks_written > 0
        assert status["last_run"]["status"] == "succeeded"
        run = (await db.execute(select(IngestionRun).order_by(IngestionRun.id.desc()))).scalars().first()
        assert run.trigger == "test" and run.finished_at is not None


async def test_metadata_guardrail_is_persisted(kb, session_factory):
    async with session_factory() as db:
        chip = (await db.execute(select(Episode).where(Episode.slug == "chip-conley"))).scalar_one()
        assert chip.metadata_verified is False
        assert chip.youtube_url is None
        assert chip.title == "Chip Conley on Lenny's Podcast"
        lauryn = (await db.execute(select(Episode).where(Episode.slug == "lauryn-isford"))).scalar_one()
        assert lauryn.youtube_url == "https://www.youtube.com/watch?v=TEST_ONBOARDING"


async def test_sponsor_reads_never_reach_the_index(kb, session_factory):
    async with session_factory() as db:
        texts = (await db.execute(select(Chunk.text))).scalars().all()
        assert not any("Acme" in t for t in texts)


async def test_reingest_is_idempotent(kb, session_factory, settings):
    stats = await ingest(session_factory, settings, trigger="test")
    assert stats.unchanged == 4 and stats.added == 0 and stats.updated == 0 and stats.chunks_written == 0


async def test_lexical_search_finds_the_right_episode(kb, session_factory):
    retriever = Retriever(embedder=None)
    async with session_factory() as db:
        result = await retriever.search(db, "How does Superhuman measure PMF?", top_k=3, max_per_episode=2)
    assert result.mode == "lexical"
    assert result.chunks[0].episode_slug == "rahul-vohra"
    assert result.chunks[0].coverage >= 0.75
    source = result.chunks[0].to_source("S1")
    assert source["youtube_url"].startswith("https://www.youtube.com/watch?v=TEST_SUPERHUMAN")
    assert "/blob/" in source["source_url"]


async def test_search_ranks_topic_episode_first(kb, session_factory):
    retriever = Retriever(embedder=None)
    async with session_factory() as db:
        onboarding = await retriever.search(db, "how to improve onboarding activation", top_k=3)
        plg = await retriever.search(db, "product-led growth versus sales-led", top_k=3)
    assert onboarding.chunks[0].episode_slug == "lauryn-isford"
    assert plg.chunks[0].episode_slug == "elena-verna"


async def test_out_of_domain_query_has_low_coverage(kb, session_factory):
    retriever = Retriever(embedder=None)
    async with session_factory() as db:
        result = await retriever.search(db, "chocolate cake recipe with frosting", top_k=3)
    assert result.best_coverage < 0.5


async def test_neighbour_expansion_appends_following_chunk(kb, session_factory):
    retriever = Retriever(embedder=None)
    async with session_factory() as db:
        plain = await retriever.search(db, "Superhuman survey very disappointed", top_k=1)
        expanded = await retriever.search(db, "Superhuman survey very disappointed", top_k=1, expand_neighbors=True)
        n_chunks = (
            await db.execute(
                text("SELECT count(*) FROM chunks c JOIN episodes e ON e.id = c.episode_id WHERE e.slug = :s"),
                {"s": plain.chunks[0].episode_slug},
            )
        ).scalar()
    if n_chunks > 1:
        assert len(expanded.chunks[0].text) >= len(plain.chunks[0].text)
