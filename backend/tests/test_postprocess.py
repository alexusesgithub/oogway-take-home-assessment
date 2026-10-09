"""Grounding checks on model output: citations, attribution, artifacts, essays."""

from app.agent.postprocess import (
    attribute_blocks,
    check_essay,
    extract_artifact,
    normalise_citations,
    process_citations,
    scrub_echoes,
)

VALID = {"S1", "S2", "S3"}


def test_citation_groups_are_normalised():
    assert normalise_citations("claim [S1, S3] and (S2)") == "claim [S1][S3] and [S2]"
    assert normalise_citations("claim [S1; S2 and S3]") == "claim [S1][S2][S3]"


def test_hallucinated_citations_are_removed_and_reported():
    report = process_citations("A real claim [S1]. A made-up one [S9].", VALID)
    assert "[S9]" not in report.content
    assert report.invalid == ["S9"]
    assert report.cited == ["S1"]


def test_citation_coverage_counts_substantive_blocks():
    text = (
        "Superhuman asks users how disappointed they would be without the product [S1].\n\n"
        "This second paragraph makes a long claim without any citation at all here."
    )
    report = process_citations(text, VALID)
    assert report.coverage == 0.5
    assert report.as_quality()["answer_type"] == "grounded"


def test_not_covered_answers_are_detected():
    report = process_citations("The transcripts I have don't cover this topic.", VALID)
    assert report.not_covered is True
    assert report.as_quality()["answer_type"] == "not_covered"


def test_echoed_source_headers_are_scrubbed():
    text = '[Superhuman\'s secret to success | Rahul](2025-03-23, 00:54:07): The core idea [S1].\n<source id="S2">'
    assert scrub_echoes(text).strip() == "The core idea [S1]."


SOURCES = {
    "S1": "Rahul Vohra: We ask users how they would feel if they could no longer use Superhuman. "
    "If at least 40 percent of users say very disappointed, you have product-market fit.",
    "S2": "Lauryn Isford: Onboarding is really about activation, getting a new user to core value fast.",
}


def test_attribution_infers_missing_citation_for_paraphrase():
    text = "If at least 40 percent of users say they would be very disappointed without Superhuman, you have product-market fit."
    report = attribute_blocks(text, SOURCES)
    assert report.inferred == ["S1"]
    assert report.content.endswith("[S1]")
    assert report.unsupported_blocks == 0


def test_attribution_flags_unsupported_statements():
    text = "Quarterly OKR planning rituals at Google rely on moonshot thinking and stretch targets for engineers."
    report = attribute_blocks(text, SOURCES)
    assert report.inferred == []
    assert report.unsupported_blocks == 1


def test_attribution_verifies_quotes_verbatim():
    ok = attribute_blocks('He said "If at least 40 percent of users say very disappointed" [S1]', SOURCES)
    bad = attribute_blocks('He said "we never measured anything about our users at all" [S1]', SOURCES)
    assert ok.unverified_quotes == []
    assert len(bad.unverified_quotes) == 1


def test_extract_artifact_from_tag():
    out = '<artifact type="html" title="Onboarding one-pager"><!DOCTYPE html><html><body><h1>Hi</h1></body></html></artifact>\nDone.'
    art = extract_artifact(out, "html", "Artifact")
    assert art.kind == "html" and art.title == "Onboarding one-pager" and art.method == "tag"
    assert art.message == "Done."


def test_extract_artifact_falls_back_to_fenced_or_bare_html():
    fenced = "Here you go:\n```markdown\n# Checklist\n" + "- [ ] item\n" * 30 + "```"
    art = extract_artifact(fenced, "markdown", "Artifact")
    assert art.kind == "markdown" and art.title == "Checklist" and art.method == "fence"

    bare = "Sure!\n<!DOCTYPE html><html><head><title>PLG vs SLG</title></head><body><p>x</p></body></html>"
    art = extract_artifact(bare, "html", "Artifact")
    assert art.kind == "html" and art.title == "PLG vs SLG" and art.method == "bare_html"


def test_extract_artifact_returns_none_for_short_chat():
    assert extract_artifact("Sorry, I can't.", "html", "Artifact") is None


def _essay() -> str:
    # 4 sentences x 18 words = a 72-word paragraph (1/3/1-ish rhythm, <= 5 sentences).
    sentence = "Teams that measure activation every single week learn faster, waste less time and ship better onboarding."
    paragraph = " ".join([sentence] * 4)
    sections = "\n\n".join(
        f"## {i}. Lesson {i}\n\n" + "\n\n".join([paragraph] * 4) + f" [S{i}]\n\n"
        f"- **Key point {i}** first bullet [S{i}]\n- second bullet"
        for i in range(1, 5)
    )
    return (
        "# 4 Lessons From Lenny's Guests for PMs Fixing Onboarding\n\n"
        "Most onboarding is a tour. That's the problem.\n\n"
        f"{sections}\n\n## The takeaway\n\nDefine one activation metric this week [S1].\n\n"
        "**TL;DR**\n- one\n- two\n- three"
    )


def test_essay_checklist_passes_for_well_formed_essay():
    report = check_essay(_essay(), target_words=1250, cited_sources=4)
    failed = [c.id for c in report.checks if not c.passed]
    assert failed == [], failed
    assert 1000 <= report.word_count <= 1500


def test_essay_checklist_flags_short_unstructured_text():
    report = check_essay("Just one paragraph about growth.", cited_sources=0)
    failed = {c.id for c in report.checks if not c.passed}
    assert {"length", "headline", "structure", "takeaway", "grounded"} <= failed
