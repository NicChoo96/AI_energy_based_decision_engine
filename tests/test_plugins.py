"""The sample ``plugins.py`` — the actions a rule file can reach by name."""

from __future__ import annotations

from engine.config import ROOT
from engine.core.actions import REGISTRY, ActionContext, load_plugins

BUILT_INS = ("annotate", "emit", "http", "record", "trigger")
SAMPLE = ("freeze_account", "page", "slack_ping")


def test_the_sample_plugin_loads_without_error():
    """A broken plugin is reported rather than raised, so assert on the text."""
    report = load_plugins(ROOT)
    assert "did not import" not in report, report
    assert "plugins.py" in report


def test_the_sample_plugin_adds_exactly_its_three_actions():
    load_plugins(ROOT)
    for name in (*BUILT_INS, *SAMPLE):
        assert name in REGISTRY, f"{name} is not registered"


def test_a_registered_action_is_callable_directly():
    load_plugins(ROOT)
    ctx = ActionContext(flow="test", run_id="r1", root=ROOT, ledger=None)
    result = REGISTRY["slack_ping"](ctx, "slack_ping", ("alerts", "checkout is down"))
    assert result.status == "fired"
    assert result.payload["simulated"] is True
    assert "#alerts" in result.detail


def test_the_page_action_escalates_urgent_severities():
    load_plugins(ROOT)
    ctx = ActionContext(flow="test", run_id="r1", root=ROOT, ledger=None)
    loud = REGISTRY["page"](ctx, "page", ("billing-lead", "sev1"))
    quiet = REGISTRY["page"](ctx, "page", ("billing-lead", "low"))
    assert loud.payload["escalation"] == "P1"
    assert quiet.payload["escalation"] == "P3"


def test_missing_arguments_do_not_crash_a_plugin_action():
    """A half-written rule line should degrade, not raise mid-run."""
    load_plugins(ROOT)
    ctx = ActionContext(flow="test", run_id="r1", root=ROOT, ledger=None)
    bare = REGISTRY["page"](ctx, "page", ())
    assert bare.status == "fired"
    assert "oncall" in bare.detail


def test_the_destructive_sample_action_is_always_a_dry_run():
    """The one action that would touch real money must never execute."""
    load_plugins(ROOT)
    ctx = ActionContext(flow="test", run_id="r1", root=ROOT, ledger=None)
    result = REGISTRY["freeze_account"](ctx, "freeze_account", ("acct-1", "abuse"))
    assert result.status == "dry-run"
    assert result.payload["executed"] is False


def test_a_rule_file_can_name_a_plugin_action():
    """refund_triage points its escalate branch at the plugin's ``page``."""
    from engine.rules import load

    ruleset = load(ROOT / "rules" / "refund_triage.rule")
    called = {option.target for option in ruleset.nodes["billing_play"].actions}
    assert called == {"page", "trigger"}
    escalate = next(o for o in ruleset.nodes["billing_play"].actions if o.target == "page")
    assert escalate.args == ("billing-lead", "high")
