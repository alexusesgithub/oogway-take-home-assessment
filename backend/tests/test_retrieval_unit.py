"""Pure retrieval helpers: query handling, fusion, diversity, excerpting."""

from datetime import date

from app.retrieval.search import (
    RetrievedChunk,
    build_retrieval_query,
    deep_link,
    diversify,
    expand_query,
    focus_excerpt,
    merge_continuation,
    query_bigrams,
    rrf_fuse,
)


def chunk(cid: int, slug: str, score: float = 1.0) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=cid, episode_slug=slug, title="T", guest="G", publish_date=date(2024, 1, 1),
        youtube_url="https://www.youtube.com/watch?v=abc", source_url="https://x", start_seconds=90,
        speakers="G", text="text", score=score,
    )


def test_expand_query_drops_filler_and_expands_jargon():
    expanded = expand_query("What's the best way to think about PMF and PLG?")
    assert "product market fit" in expanded and "product led growth" in expanded
    assert "best" not in expanded.split() and "think" not in expanded.split()


def test_expand_query_never_returns_empty():
    assert expand_query("think best way") == "think best way"


def test_followups_are_combined_with_previous_question():
    previous = ["How does Superhuman measure product-market fit?"]
    assert build_retrieval_query("What about for B2B?", previous).startswith("How does Superhuman")
    assert build_retrieval_query("Can you give an example of that?", previous).startswith("How does Superhuman")
    standalone = "What do guests recommend for structuring a growth team at a Series B startup?"
    assert build_retrieval_query(standalone, previous) == standalone
    assert build_retrieval_query("What about pricing?", []) == "What about pricing?"


def test_rrf_rewards_agreement_between_rankings():
    scores = rrf_fuse([[1, 2, 3], [3, 1, 4]])
    assert max(scores, key=scores.get) == 1
    assert scores[3] > scores[2]
    assert set(scores) == {1, 2, 3, 4}


def test_diversify_caps_chunks_per_episode():
    ranked = [chunk(1, "a"), chunk(2, "a"), chunk(3, "a"), chunk(4, "b"), chunk(5, "c")]
    picked = diversify(ranked, top_k=4, max_per_episode=2)
    assert [c.chunk_id for c in picked] == [1, 2, 4, 5]


def test_source_payload_has_deep_link_and_ids():
    payload = chunk(7, "rahul-vohra").to_source("S1")
    assert payload["id"] == "S1"
    assert payload["youtube_url"].endswith("&t=90s")
    assert payload["timestamp"] == "1:30"
    roundtrip = RetrievedChunk.from_source(payload)
    assert roundtrip.youtube_url == "https://www.youtube.com/watch?v=abc"
    assert roundtrip.chunk_id == 7


def test_deep_link_formats():
    assert deep_link(None, 10) is None
    assert deep_link("https://youtu.be/x", 0) == "https://youtu.be/x"
    assert deep_link("https://youtu.be/x", 75) == "https://youtu.be/x?t=75s"


def test_focus_excerpt_keeps_relevant_window_and_speaker_labels():
    text = (
        "Lenny: Let us start with your background and early career in consulting.\n"
        "Guest: I worked in consulting for years. It was fine. I learned a lot about clients.\n"
        "Lenny: How do you measure product market fit?\n"
        "Guest: We measure product market fit with a survey. Forty percent very disappointed is the bar. "
        "We track it every quarter.\n"
        "Lenny: What about hiring?\nGuest: Hiring is hard and slow and we hire carefully."
    )
    out = focus_excerpt(text, "how to measure product market fit", max_words=30)
    assert "survey" in out
    assert "Guest:" in out
    assert out.lstrip("… ").startswith("Lenny: How do you measure")  # no irrelevant lead-in
    assert len(out.split()) <= 30 + 6  # budget + speaker labels + ellipses


def test_focus_excerpt_returns_short_text_unchanged():
    assert focus_excerpt("short text", "anything", 50) == "short text"


def test_merge_continuation_drops_repeated_turn():
    first = "Guest: answer one.\nLenny: And then?"
    second = "Lenny: And then?\nGuest: the continuation."
    assert merge_continuation(first, second) == "Guest: answer one.\nLenny: And then?\nGuest: the continuation."


def test_query_bigrams():
    assert query_bigrams("build a growth team") == ["build a", "a growth", "growth team"]


def test_self_contained_question_after_topic_switch_is_not_merged():
    # Regression: an 8-word standalone question was merged with an unrelated
    # previous question ("chocolate cake"), which then dominated retrieval.
    previous = ["What's a good chocolate cake recipe?"]
    q = "What do guests recommend for improving onboarding activation?"
    assert build_retrieval_query(q, previous) == q
    assert build_retrieval_query("And for B2B?", previous).startswith("What's a good")
    assert build_retrieval_query("Why does that work?", previous).startswith("What's a good")
