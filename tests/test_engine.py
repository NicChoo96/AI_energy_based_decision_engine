"""The traversal loop, driven entirely by a scripted backend.

Nothing in this file touches a network or a model. If any of these tests can
influence which branch is taken without going through ``StubBackend.decide``,
then the engine is branching on its own — which is the one thing it must not do.
"""

from __future__ import annotations

import json

import pytest

from engine.core import Engine, StateLedger, FlowError, UnknownLayer, UnknownOption
from engine.core.actions import REGISTRY, ActionContext, ActionResult, action, load_plugins, perform
from engine.core.engine import FlowComplete
from engine.rules.model import LAND_GOTO, LAND_ACTION, NODE_ROUTE, Node, Option, RuleSet, Trace
from tests.stub_backend import StubBackend


def _engine(rule, backend, tmp_path, **kwargs) -> Engine:
    return Engine(ruleset=rule, backend=backend, root=tmp_path, **kwargs)


# --------------------------------------------------------------------------- #
# the happy path
# --------------------------------------------------------------------------- #
def test_first_option_of_each_layer_is_walked_and_an_action_ends_the_flow(rule, stub, tmp_path):
    trace = Engine(ruleset=rule, backend=stub, root=tmp_path).run("a plain seed")

    assert trace.status == "completed"
    assert trace.layers == 2, "classify -> alpha_play, then alpha_play lands on the refund action"
    assert [step["chosen"] for step in trace.steps] == ["alpha", "refund"]
    assert [step["node"] for step in trace.steps] == ["classify", "alpha_play"]
    assert trace.model == "stub-0"


def test_the_final_action_is_recorded_with_its_arguments(rule, stub, tmp_path):
    trace = Engine(ruleset=rule, backend=stub, root=tmp_path).run("seed")
    final = [entry for entry in trace.actions if entry["at"] == "final"]
    assert len(final) == 1
    assert final[0]["action"] == "trigger"
    assert final[0]["args"] == ["payments", "refunded", "Refund issued"]
    assert final[0]["payload"]["channel"] == "payments"


def test_effects_fire_before_the_layer_asks_its_question(rule, stub, tmp_path):
    trace = Engine(ruleset=rule, backend=stub, root=tmp_path).run("seed")
    effect = [entry for entry in trace.actions if entry["at"] == "effect"]
    assert [entry["action"] for entry in effect] == ["annotate"]
    assert effect[0]["layer"] == 0, "the effect belongs to classify, before its question"

    # annotate wrote into the ledger, so the very first decision already saw it.
    first_state = stub.calls[0][0]
    assert "triage started" in first_state
    assert first_state.startswith("seed")


def test_a_deep_path_reaches_a_plain_end_layer(rule, tmp_path):
    stub = StubBackend(picks={"classify": "alpha", "alpha_play": "deep", "deep_dive": "endless"})
    trace = Engine(ruleset=rule, backend=stub, root=tmp_path).run("seed")

    assert trace.status == "completed"
    assert trace.layers == 3
    assert [step["node"] for step in trace.steps] == ["classify", "alpha_play", "deep_dive"]
    assert [entry["at"] for entry in trace.actions] == ["effect", "effect"]
    assert trace.actions[-1]["action"] == "record", "the @end layer's effect is the last thing to run"


def test_the_alternate_first_branch_terminates_immediately(rule, tmp_path):
    stub = StubBackend(picks={"classify": "beta"})
    trace = Engine(ruleset=rule, backend=stub, root=tmp_path).run("seed")
    assert trace.layers == 1
    final = [entry for entry in trace.actions if entry["at"] == "final"]
    assert final[0]["payload"]["target"] == "beta-handled"


def test_branching_is_entirely_the_backends_decision(rule, tmp_path):
    """Same rule, same seed, two scripts, two completely different flows."""
    left = Engine(
        ruleset=rule, backend=StubBackend(picks={"classify": "alpha"}), root=tmp_path
    ).run("identical input")
    right = Engine(
        ruleset=rule, backend=StubBackend(picks={"classify": "beta"}), root=tmp_path
    ).run("identical input")

    assert left.layers != right.layers
    assert [s["target"] for s in left.steps] != [s["target"] for s in right.steps]


# --------------------------------------------------------------------------- #
# state accumulation
# --------------------------------------------------------------------------- #
def test_state_grows_with_every_decision_and_signal(rule, stub, tmp_path):
    trace = Engine(ruleset=rule, backend=stub, root=tmp_path).run("the original complaint")

    assert trace.state.startswith("the original complaint")
    assert "Effect 'annotate': triage started" in trace.state
    assert "Continue to layer 'alpha_play'" in trace.state
    assert "urgency" in trace.state and "churn" in trace.state and "lane" in trace.state


def test_each_layer_sees_a_strictly_longer_state_than_the_last(rule, stub, tmp_path):
    Engine(ruleset=rule, backend=stub, root=tmp_path).run("seed")
    lengths = [len(state) for state, _ in stub.calls]
    assert lengths == sorted(lengths)
    assert lengths[0] < lengths[-1], "the second call must see what the first one learned"


def test_score_signal_is_rendered_with_its_band(rule, tmp_path):
    stub = StubBackend(scores={"urgency": 2.0})
    trace = Engine(ruleset=rule, backend=stub, root=tmp_path).run("seed")
    assert "score" in trace.state
    assert "blocking" in trace.state


def test_noul_signal_reads_as_yes_or_no(rule, tmp_path):
    yes = Engine(
        ruleset=rule, backend=StubBackend(noul={"churn": 0.9}), root=tmp_path
    ).run("seed")
    no = Engine(ruleset=rule, backend=StubBackend(noul={"churn": 0.1}), root=tmp_path).run("seed")

    assert "leans yes" in yes.state
    assert "leans no" in no.state


def test_choice_signal_is_rendered_with_its_criteria_text(rule, tmp_path):
    stub = StubBackend(picks={"lane": "slow"})
    trace = Engine(ruleset=rule, backend=stub, root=tmp_path).run("seed")
    assert "slow" in trace.state


def test_ledger_is_plain_text_and_not_structured() -> None:
    ledger = StateLedger(seed="start here")
    ledger.note("a fact")
    ledger.note("another fact")
    assert ledger.text == "start here\na fact\nanother fact"


# --------------------------------------------------------------------------- #
# events, for the UI
# --------------------------------------------------------------------------- #
def test_events_arrive_in_the_order_the_ui_needs(rule, stub, tmp_path):
    seen: list[dict] = []
    Engine(ruleset=rule, backend=stub, root=tmp_path, on_event=seen.append).run("seed")

    kinds = [event["kind"] for event in seen]
    assert kinds[0] == "start"
    assert kinds[-1] == "end"
    assert kinds.index("layer") < kinds.index("decision")
    assert "action" in kinds
    assert seen[-1]["status"] == "completed"


def test_decision_events_carry_the_full_menu_and_the_confidence(rule, stub, tmp_path):
    seen: list[dict] = []
    Engine(ruleset=rule, backend=stub, root=tmp_path, on_event=seen.append).run("seed")

    decision = next(
        event for event in seen if event["kind"] == "decision" and event["node"] == "alpha_play"
    )
    assert decision["chosen"] == "refund"
    assert decision["confidence"] == pytest.approx(0.75)
    assert decision["probabilities"]["refund"] == pytest.approx(0.75)
    assert {candidate["key"] for candidate in decision["candidates"]} == {"refund", "deep"}
    assert decision["target"] == "trigger", "an action option's target is the action to call"
    assert decision["args"] == ["payments", "refunded", "Refund issued"]
    assert decision["routing"] == LAND_ACTION
    assert [s["name"] for s in decision["signals"]] == ["urgency", "churn", "lane"]


def test_layer_events_list_the_signals_that_will_be_asked(rule, stub, tmp_path):
    seen: list[dict] = []
    Engine(ruleset=rule, backend=stub, root=tmp_path, on_event=seen.append).run("seed")

    layers = [event for event in seen if event["kind"] == "layer"]
    assert [layer["node"] for layer in layers] == ["classify", "alpha_play"]
    assert [s["type"] for s in layers[1]["signals"]] == ["score", "noul", "choice"]
    assert layers[0]["effects"] == [{"action": "annotate", "args": ["triage started"]}]
    assert layers[1]["effects"] == []


# --------------------------------------------------------------------------- #
# failure modes
# --------------------------------------------------------------------------- #
def test_a_goto_pointing_at_a_missing_layer_stops_the_run(rule, tmp_path):
    broken = RuleSet(
        title="broken",
        start="a",
        nodes={
            "a": Node(
                key="a",
                ask="where?",
                kind=NODE_ROUTE,
                options=(Option(key="go", semantics="off we go", kind=LAND_GOTO, target="ghost"),),
            )
        },
    )
    trace = Engine(ruleset=broken, backend=StubBackend(), root=tmp_path).run("seed")
    assert trace.status == "failed"
    assert any("UnknownLayer" in event.get("message", "") for event in trace.events)


def test_unknown_layer_is_a_flow_error_subclass() -> None:
    assert issubclass(UnknownLayer, FlowError)
    assert issubclass(UnknownOption, FlowError)


def test_a_layer_that_is_never_reached_is_never_asked(rule, tmp_path):
    """never_return is only reached via deep_dive -> endless."""
    stub = StubBackend(picks={"classify": "beta"})
    trace = Engine(ruleset=rule, backend=stub, root=tmp_path).run("seed")
    assert "never_return" not in [step["node"] for step in trace.steps]
    assert {name for _, names in stub.calls for name in names}.isdisjoint({"deep_dive"})


def test_an_option_the_model_invented_stops_the_run(rule, tmp_path):
    stub = StubBackend(picks={"classify": "gamma"})
    trace = Engine(ruleset=rule, backend=stub, root=tmp_path).run("seed")
    assert trace.status == "failed"
    assert any("UnknownOption" in event.get("message", "") for event in trace.events)


def test_unknown_option_message_names_the_menu(rule, tmp_path):
    """The error must be actionable — it lists what the model was allowed to say."""
    seen: list[dict] = []
    Engine(
        ruleset=rule,
        backend=StubBackend(picks={"classify": "nonsense"}),
        root=tmp_path,
        on_event=seen.append,
    ).run("seed")
    error = next(event for event in seen if event["kind"] == "error")
    assert "alpha" in error["message"] and "beta" in error["message"]


def test_the_layer_budget_stops_a_cycle(tmp_path):
    looping = RuleSet(
        title="loop",
        start="a",
        nodes={
            "a": Node(
                key="a",
                ask="again?",
                kind=NODE_ROUTE,
                options=(Option(key="more", semantics="go round", kind=LAND_GOTO, target="b"),),
            ),
            "b": Node(
                key="b",
                ask="again?",
                kind=NODE_ROUTE,
                options=(Option(key="more", semantics="go round", kind=LAND_GOTO, target="a"),),
            ),
        },
    )
    trace = Engine(ruleset=looping, backend=StubBackend(), root=tmp_path, max_layers=7).run("seed")
    assert trace.status == "budget-exceeded"
    assert trace.layers == 7


def test_flow_complete_is_an_exception_used_only_for_unwinding(rule, stub, tmp_path):
    assert issubclass(FlowComplete, Exception)
    assert not issubclass(FlowComplete, UnknownLayer)


# --------------------------------------------------------------------------- #
# actions
# --------------------------------------------------------------------------- #
def test_a_mistyped_action_becomes_a_visible_failure_not_an_exception(tmp_path):
    """A typo in a rule file should show up in the UI, not blow up the run."""
    typo = RuleSet(
        title="typo flow",
        start="a",
        nodes={
            "a": Node(
                key="a",
                ask="go?",
                kind=NODE_ROUTE,
                options=(
                    Option(key="go", semantics="away", kind=LAND_ACTION, target="triggr"),
                ),
            )
        },
    )
    trace = Engine(ruleset=typo, backend=StubBackend(), root=tmp_path).run("seed")
    assert trace.status == "completed"
    assert trace.actions[-1]["status"] == "unknown-action"
    assert "no action named 'triggr'" in trace.actions[-1]["detail"]


def test_the_trace_serialises_to_json(rule, stub, tmp_path):
    trace: Trace = Engine(ruleset=rule, backend=stub, root=tmp_path).run("seed")
    body = json.loads(json.dumps(trace.to_json()))
    assert body["flow"] == "Stub flow"
    assert body["backend"] == "stub"
    assert body["status"] == "completed"
    assert body["state"].startswith("seed")
    assert [step["chosen"] for step in body["steps"]] == ["alpha", "refund"]


def test_an_unknown_action_reports_a_typo_instead_of_crashing() -> None:
    ctx = ActionContext(flow="f", run_id="r", root=None, ledger=StateLedger(seed="s"))
    assert perform("nope", ctx, ()).status == "unknown-action"


def test_record_writes_an_audit_row_per_run(rule, tmp_path):
    stub = StubBackend(picks={"classify": "alpha", "alpha_play": "deep", "deep_dive": "endless"})
    Engine(ruleset=rule, backend=stub, root=tmp_path).run("seed")

    ledger_file = tmp_path / "runs" / "stub-flow.jsonl"
    assert ledger_file.exists()
    rows = [json.loads(line) for line in ledger_file.read_text().splitlines()]
    assert rows and rows[0]["action"] == "record"
    assert rows[0]["flow"] == "Stub flow"


def test_trigger_marks_itself_simulated_until_live_mode_is_on(rule, tmp_path):
    simulated = Engine(
        ruleset=rule, backend=StubBackend(picks={"classify": "beta"}), root=tmp_path
    ).run("seed")
    live = Engine(
        ruleset=rule,
        backend=StubBackend(picks={"classify": "beta"}),
        root=tmp_path,
        live=True,
    ).run("seed")

    assert simulated.actions[-1]["payload"]["simulated"] is True
    assert live.actions[-1]["payload"]["simulated"] is False


def test_custom_actions_can_be_registered_and_are_then_callable() -> None:
    @action
    def _test_only_probe(ctx, name, args):
        return ActionResult(action=name, args=args, detail="probed")

    assert "_test_only_probe" in REGISTRY
    ctx = ActionContext(flow="f", run_id="r", root=None, ledger=StateLedger(seed="s"))
    assert perform("_test_only_probe", ctx, ("x",)).detail == "probed"


def test_load_plugins_registers_actions_from_a_project_file(tmp_path) -> None:
    (tmp_path / "plugins.py").write_text(
        "from engine.core.actions import ActionResult, action\n"
        "@action\n"
        "def slack_ping(ctx, name, args):\n"
        "    return ActionResult(action=name, args=args, detail='pinged slack')\n",
        encoding="utf-8",
    )
    load_plugins(tmp_path)
    assert "slack_ping" in REGISTRY
