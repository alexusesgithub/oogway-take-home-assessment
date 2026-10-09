"""Routing: which skill handles a message, and why."""

import pytest

from app.agent.router import Route, route_message


@pytest.mark.parametrize(
    ("message", "route", "kind"),
    [
        ("How does Superhuman measure product-market fit?", Route.ANSWER, None),
        ("What did guests say about pricing?", Route.ANSWER, None),
        ("tell me about onboarding", Route.ANSWER, None),
        ("Turn this into a Ship 30 essay", Route.ESSAY, "markdown"),
        ("Write an essay on product-led growth", Route.ESSAY, "markdown"),
        ("write a blog post about retention", Route.ESSAY, "markdown"),
        ("Make an HTML one-pager of the onboarding tactics", Route.ARTIFACT, "html"),
        ("Create a markdown checklist for running a PMF survey", Route.ARTIFACT, "markdown"),
        ("Can you make a cheat sheet of these ideas?", Route.ARTIFACT, "markdown"),
        ("Design a landing page for the growth playbook", Route.ARTIFACT, "html"),
        ("hi", Route.CHITCHAT, None),
        ("Thanks!", Route.CHITCHAT, None),
        ("what can you do?", Route.CHITCHAT, None),
    ],
)
def test_auto_routing(message, route, kind):
    decision = route_message(message)
    assert decision.route is route
    assert decision.artifact_kind == kind
    assert decision.reason


@pytest.mark.parametrize(
    "message",
    ["How do I build a landing page that converts?", "What makes a good essay hook according to guests?"],
)
def test_questions_about_artifacts_are_answered_not_generated(message):
    assert route_message(message).route is Route.ANSWER


def test_explicit_mode_overrides_rules():
    assert route_message("hi", mode="essay").route is Route.ESSAY
    assert route_message("Write an essay", mode="answer").route is Route.ANSWER
    decision = route_message("summarise onboarding as html", mode="artifact")
    assert decision.route is Route.ARTIFACT and decision.artifact_kind == "html"


def test_short_edit_continues_current_artifact():
    decision = route_message("make it darker", has_artifact=True)
    assert decision.route is Route.ARTIFACT
    assert decision.revises_artifact is True
    # Without an artifact in the session it is just a (weird) question.
    assert route_message("make it darker", has_artifact=False).route is Route.ANSWER
