"""Orchestration helpers: citation renumbering across turns, topic handling."""

from datetime import date

from app.agent.prompts import SourceRegistry
from app.retrieval.search import RetrievedChunk
from app.services.chat import _overlaps, _topic_words, remap_citations, strip_citations


def chunk(cid: int) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=cid, episode_slug=f"e{cid}", title="T", guest="G", publish_date=date(2024, 1, 1),
        youtube_url=None, source_url="", start_seconds=0, speakers="", text="x", score=1.0,
    )


def test_remap_citations_follows_chunk_ids_not_positions():
    # The artifact was written when S2 -> chunk 20 and S4 -> chunk 40.
    old_sources = [{"id": "S1", "chunk_id": 10}, {"id": "S2", "chunk_id": 20},
                   {"id": "S3", "chunk_id": 30}, {"id": "S4", "chunk_id": 40}]
    registry = SourceRegistry()
    registry.add([chunk(40), chunk(20), chunk(99)])  # this turn: S1=40, S2=20, S3=99
    text = "Survey users [S2]. Track the score [S4]. Unrelated [S3]."
    assert remap_citations(text, old_sources, registry) == "Survey users [S2]. Track the score [S1]. Unrelated ."


def test_strip_citations_from_history():
    assert strip_citations("Claim [S1][S12] here.") == "Claim  here."


def test_topic_words_drop_format_vocabulary():
    assert _topic_words("Make an HTML one-pager of the key onboarding tactics") == ["onboarding"]
    assert _overlaps(["onboarding"], "What do guests recommend for improving onboarding activation?")
    assert not _overlaps(["pricing"], "What do guests recommend for improving onboarding activation?")
