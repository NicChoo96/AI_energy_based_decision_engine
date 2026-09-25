"""The rule language: what it accepts, what it refuses, and what it produces."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.backends.base import CHOICE, NOUL, SCORE
from engine.config import ROOT
from engine.rules import RuleSyntaxError, load, parse, unresolved_links
from engine.rules.model import LAND_ACTION, LAND_GOTO, NODE_ROUTE, NODE_TERMINAL

RULE_FILES = sorted((ROOT / "rules").glob("*.rule"))


# --------------------------------------------------------------------------- #
# the shipped rule files
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", RULE_FILES, ids=lambda p: p.stem)
def test_shipped_rule_files_parse(path: Path) -> None:
    ruleset = load(path)
    assert ruleset.nodes, f"{path.name} defined no layers"
    assert ruleset.start in ruleset.nodes
    assert ruleset.title
    assert not unresolved_links(ruleset)


@pytest.mark.parametrize("path", RULE_FILES, ids=lambda p: p.stem)
def test_shipped_rule_files_suggest_a_real_backend(path: Path) -> None:
    from engine.backends.factory import KINDS

    ruleset = load(path)
    assert ruleset.suggested_backend in KINDS, (
        f"{path.name} suggests '{ruleset.suggested_backend}', which is not one of {KINDS}"
    )


@pytest.mark.parametrize("path", RULE_FILES, ids=lambda p: p.stem)
def test_shipped_rule_files_race_the_model_to_an_ending(path: Path) -> None:
    """Every layer must either branch somewhere or call an action, and the mesh
    must actually reach at least one action from the start."""
    ruleset = load(path)
    for key, node in ruleset.nodes.items():
        landed = bool(node.actions) or bool(node.links) or node.kind == NODE_TERMINAL
        assert landed, f"{path.name}: layer '{key}' neither branches nor does anything"

    reachable = {ruleset.start}
    frontier = [ruleset.start]
    while frontier:
        node = ruleset.nodes[frontier.pop()]
        for target in node.links:
            if target not in reachable:
                reachable.add(target)
                frontier.append(target)

    endings = [o for key in reachable for o in ruleset.nodes[key].actions]
    pure_ends = [k for k in reachable if ruleset.nodes[k].kind == NODE_TERMINAL]
    assert endings or pure_ends, f"{path.name} can never terminate"


@pytest.mark.parametrize("path", RULE_FILES, ids=lambda p: p.stem)
def test_shipped_rule_files_have_examples(path: Path) -> None:
    assert load(path).examples, f"{path.name} ships no @example seeds for the UI"


# --------------------------------------------------------------------------- #
# the language itself
# --------------------------------------------------------------------------- #
def test_parses_all_three_signal_types() -> None:
    ruleset = parse(
        "@rule T\n@start n\n"
        "@node n\n"
        "@signal a | choice | pick one | x = first | y = second\n"
        "@signal b | score  | how much | low | mid | high\n"
        "@signal c | noul   | is it true | yes = it is | no = it is not\n"
        "@ask done?\n"
        "@option go | yes | ! nothing | arg\n"
    )
    kinds = [signal.qtype for signal in ruleset.entry.signals]
    assert kinds == [CHOICE, SCORE, NOUL]

    choice, score, noul = ruleset.entry.signals
    assert choice.criteria == {"x": "first", "y": "second"}
    assert score.criteria == ["low", "mid", "high"]
    # a noul signal is a yes/no test, so its criteria are canonicalised onto
    # exactly the keys 'true'/'false' no matter how the author spelled them
    assert noul.criteria == {"true": "it is", "false": "it is not"}


def test_noul_key_spellings_are_canonicalised_or_rejected() -> None:
    spelled = parse(
        "@start n\n@node n\n"
        "@signal c | noul | is it true | no = nope | y = yep\n"
        "@ask done?\n@option go | yes | ! nothing | arg\n"
    )
    assert spelled.entry.signals[0].criteria == {"false": "nope", "true": "yep"}

    with pytest.raises(RuleSyntaxError, match="true.*false"):
        parse(
            "@start n\n@node n\n"
            "@signal c | noul | is it true | maybe = unsure | true = yep\n"
            "@ask done?\n@option go | yes | ! nothing | arg\n"
        )


def test_signals_only_ask_about_one_thing() -> None:
    """Criteria are keyed by the option key, not by the model's free text."""
    ruleset = parse(
        "@start n\n@node n\n@ask choose\n"
        "@option alpha | first  | ! act\n"
        "@option beta  | second | ! act\n"
    )
    payload = ruleset.entry.question.to_payload()
    assert payload["type"] == CHOICE
    assert payload["criteria"] == {"alpha": "first", "beta": "second"}
    assert payload["instructions"] == "choose"


def test_node_question_is_first_and_signals_follow() -> None:
    ruleset = parse(
        "@start n\n@node n\n@signal s | score | how | a | b\n@ask pick\n@option k | x | ! act\n"
    )
    names = [q.name for q in ruleset.entry.questions]
    assert names == ["n", "s"]


def test_goto_and_action_landings_are_distinguished() -> None:
    ruleset = parse(
        "@start a\n@node a\n@ask go\n@option to_b | branch | -> b\n@option stop | end | ! halting | why\n"
        "@node b\n@end\n"
    )
    branch, stop = ruleset.nodes["a"].options
    assert branch.kind == LAND_GOTO and branch.target == "b"
    assert stop.kind == LAND_ACTION and stop.target == "halting" and stop.args == ("why",)
    assert ruleset.nodes["a"].kind == NODE_ROUTE
    assert ruleset.nodes["b"].kind == NODE_TERMINAL


def test_effects_and_examples_are_captured() -> None:
    ruleset = parse(
        "@rule Named\n@about What it does\n@suggest laya\n@example first\n@example second\n"
        "@start n\n@node n\n@effect annotate | hello world\n@effect emit | done\n@end\n"
    )
    assert ruleset.title == "Named"
    assert ruleset.about == "What it does"
    assert ruleset.suggested_backend == "laya"
    assert ruleset.examples == ("first", "second")
    effects = ruleset.entry.effects
    assert [(e.action, e.args) for e in effects] == [
        ("annotate", ("hello world",)),
        ("emit", ("done",)),
    ]


def test_comments_and_blank_lines_are_ignored() -> None:
    ruleset = parse(
        "# a comment\n\n@rule T\n   \n@start n\n\n@node n\n# nested comment\n@end\n"
    )
    assert ruleset.title == "T"
    assert list(ruleset.nodes) == ["n"]


def test_unknown_directives_are_ignored_not_fatal() -> None:
    ruleset = parse("@rule T\n@start n\n@wat is this\n@node n\n@end\n")
    assert list(ruleset.nodes) == ["n"]


def test_first_node_is_the_start_when_none_declared() -> None:
    ruleset = parse("@node only\n@end\n")
    assert ruleset.start == "only"


def test_node_description_after_the_pipe_becomes_the_note() -> None:
    ruleset = parse("@start n\n@node n | a short description\n@end\n")
    assert ruleset.entry.note == "a short description"


# --------------------------------------------------------------------------- #
# refusals
# --------------------------------------------------------------------------- #
def test_dangling_goto_is_rejected() -> None:
    with pytest.raises(RuleSyntaxError, match="referenced but never defined"):
        parse("@start a\n@node a\n@ask go\n@option b | to nowhere | -> nowhere\n")


def test_dangling_start_is_rejected() -> None:
    with pytest.raises(RuleSyntaxError, match="referenced but never defined"):
        parse("@start missing\n@node a\n@end\n")


def test_option_without_a_destination_is_rejected() -> None:
    with pytest.raises(RuleSyntaxError, match="needs a destination"):
        parse("@start a\n@node a\n@ask go\n@option b | nowhere to go\n")


def test_option_with_an_unknown_marker_is_rejected() -> None:
    with pytest.raises(RuleSyntaxError, match="needs a destination"):
        parse("@start a\n@node a\n@ask go\n@option b | bad marker | ==> c\n")


def test_unknown_signal_type_is_rejected() -> None:
    with pytest.raises(RuleSyntaxError, match="unknown @signal type"):
        parse("@start a\n@node a\n@signal s | telepathy | guess | one | two\n@end\n")


def test_directive_before_any_node_is_rejected() -> None:
    with pytest.raises(RuleSyntaxError, match="before any @node"):
        parse("@rule T\n@ask orphaned\n@node a\n@end\n")


def test_unresolved_links_reports_every_missing_target() -> None:
    ruleset = parse("@start a\n@node a\n@ask go\n@option x | p | ! act\n")
    assert unresolved_links(ruleset) == []
