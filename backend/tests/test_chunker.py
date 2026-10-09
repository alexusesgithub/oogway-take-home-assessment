"""Ingestion: parsing, sponsor removal, chunking and data-quality guardrails."""

from pathlib import Path

from app.retrieval.chunker import (
    Turn,
    chunk_episode,
    chunk_turns,
    dominant_guest,
    drop_sponsor_reads,
    format_timestamp,
    parse_episode,
    parse_timestamp,
    split_turns,
    title_mentions,
)
from app.retrieval.ingest import plan_episodes

EPISODES = Path(__file__).parent / "fixtures" / "transcripts" / "episodes"


def load(slug: str):
    return parse_episode(slug, (EPISODES / slug / "transcript.md").read_text(encoding="utf-8"))


def test_parse_episode_reads_front_matter_and_body():
    ep = load("rahul-vohra")
    assert ep.guest == "Rahul Vohra"
    assert ep.title.startswith("Superhuman's secret to success")
    assert ep.publish_date.isoformat() == "2025-03-23"
    assert ep.video_id == "TEST_SUPERHUMAN"
    assert "pmf" in ep.keywords
    assert ep.body.startswith("Lenny (00:00:00):")
    assert len(ep.content_hash) == 64


def test_split_turns_extracts_speakers_and_timestamps():
    turns = split_turns(load("rahul-vohra").body)
    assert turns[0].speaker == "Lenny"
    assert turns[0].start_seconds == 0
    rahul = [t for t in turns if t.speaker == "Rahul Vohra"]
    assert rahul[0].start_seconds == 120
    assert "40 percent" in rahul[0].text


def test_split_turns_falls_back_to_paragraphs_without_labels():
    turns = split_turns("First paragraph here.\n\nSecond paragraph here.")
    assert [t.text for t in turns] == ["First paragraph here.", "Second paragraph here."]
    assert turns[0].start_seconds is None


def test_sponsor_reads_are_removed_including_continuations():
    turns, dropped = drop_sponsor_reads(split_turns(load("rahul-vohra").body))
    text = " ".join(t.text for t in turns)
    assert dropped == 2  # the ad turn and its same-speaker continuation
    assert "Acme" not in text
    assert "very disappointed" in text


def test_sponsor_read_mid_turn_keeps_the_prefix():
    turns = [Turn("Lenny", 10, "I love that framing, it is so useful. This episode is brought to you by Acme. Acme rocks.")]
    kept, dropped = drop_sponsor_reads(turns)
    assert dropped == 1
    assert kept[0].text == "I love that framing, it is so useful."


def test_chunks_respect_size_limits_and_carry_questions_forward():
    turns = [
        Turn("Lenny", 0, "What is the key to retention?"),
        Turn("Guest", 5, "word " * 250),
        Turn("Lenny", 60, "And how do you measure it?"),
        Turn("Guest", 65, "measure " * 250),
    ]
    chunks = chunk_turns(turns, target_words=280, max_words=420)
    assert len(chunks) == 2
    assert all(c.word_count <= 420 + 20 for c in chunks)
    # The short question that closed chunk 1 opens chunk 2 as context.
    assert chunks[1].text.startswith("Lenny: And how do you measure it?")
    assert chunks[1].start_seconds == 60


def test_long_monologue_is_split_on_sentences():
    turns = [Turn("Guest", 0, "This is a sentence. " * 200)]
    chunks = chunk_turns(turns, target_words=280, max_words=420)
    assert len(chunks) >= 2
    assert all(c.word_count <= 430 for c in chunks)


def test_chunk_episode_produces_timestamped_chunks():
    chunks = chunk_episode(load("lauryn-isford"))
    assert chunks and all(c.start_seconds is not None for c in chunks)
    assert "Lauryn Isford" in chunks[0].speakers


def test_timestamp_round_trip():
    assert parse_timestamp("01:02:03") == 3723
    assert parse_timestamp("02:03") == 123
    assert format_timestamp(3723) == "1:02:03"
    assert format_timestamp(123) == "2:03"
    assert format_timestamp(None) == ""


def test_guest_inference_and_title_check():
    turns = split_turns(load("chip-conley").body)
    assert dominant_guest(turns) == "Chip Conley"
    assert title_mentions("Mastering onboarding | Lauryn Isford", "Lauryn Isford")
    assert not title_mentions("Mastering onboarding | Lauryn Isford", "Chip Conley")


def test_plan_episodes_skips_duplicates_and_withholds_mismatched_metadata():
    parsed = [load(p.name) for p in sorted(EPISODES.iterdir())]
    plans, duplicates = plan_episodes(parsed)
    assert duplicates == ["rahul-vohra-20"]
    by_slug = {p.episode.slug: p for p in plans}
    # chip-conley carries Lauryn Isford's title and video id: metadata withheld.
    assert by_slug["chip-conley"].metadata_verified is False
    assert by_slug["chip-conley"].guest == "Chip Conley"
    # The genuine owner of the shared video id keeps its metadata.
    assert by_slug["lauryn-isford"].metadata_verified is True
    assert by_slug["rahul-vohra"].metadata_verified is True
