"""The command line runner: resolution, the rule listing, and no branches."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from engine.backends import JEV, LAYA
from engine.cli import _rule_row, list_rules, main, resolve_backend, resolve_flow
from engine.config import ROOT


def test_resolve_flow_accepts_a_bare_stem():
    resolved = resolve_flow("refund_triage")
    assert resolved.name == "refund_triage.rule"
    assert resolved.parent.name == "rules"
    assert resolved.exists()


def test_resolve_flow_accepts_an_explicit_path(tmp_path: Path):
    path = tmp_path / "custom.rule"
    path.write_text("@rule x\n@start end\n@node end\n@end done\n", encoding="utf-8")
    assert resolve_flow(str(path)) == path


def test_resolve_flow_passes_an_unknown_name_through_untouched():
    """The guard has to report a missing flow, so resolution must not raise."""
    assert resolve_flow("no_such_mesh_anywhere") == Path("no_such_mesh_anywhere")


class _Rule:
    """Only the one attribute ``resolve_backend`` is allowed to look at."""

    def __init__(self, suggested: str) -> None:
        self.suggested_backend = suggested


def test_resolve_backend_uses_an_explicit_request_first():
    assert resolve_backend(LAYA, _Rule(JEV)) == LAYA
    assert resolve_backend(JEV, _Rule(LAYA)) == JEV


def test_resolve_backend_maps_aliases():
    assert resolve_backend("openrouter", _Rule(LAYA)) == JEV
    assert resolve_backend("local", _Rule(JEV)) == LAYA


def test_resolve_backend_falls_back_to_the_rules_suggestion():
    assert resolve_backend("", _Rule(LAYA)) == LAYA
    assert resolve_backend("", _Rule(JEV)) == JEV


def test_resolve_backend_defaults_to_jev_when_nothing_is_said():
    assert resolve_backend("", _Rule("")) == JEV


def test_rule_row_is_one_line_and_names_the_backend():
    row = _rule_row(ROOT / "rules" / "refund_triage.rule")
    assert "\n" not in row
    assert "refund_triage" in row
    assert "jev" in row


def test_list_rules_prints_every_rule_file():
    out = io.StringIO()
    assert list_rules(out) == 0
    text = out.getvalue()
    for path in (ROOT / "rules").glob("*.rule"):
        assert path.stem in text


def test_the_listing_has_metadata_on_each_row():
    """Regression: rows used to be bare stems, or wrapped past the terminal."""
    out = io.StringIO()
    main(["--list"], out=out)
    rows = [line for line in out.getvalue().splitlines() if "nodes" in line and "endings" in line]
    assert rows, "the listing printed no rule rows"
    assert all(len(row) <= 78 for row in rows)


def test_a_long_title_is_clipped_rather_than_wrapped():
    from engine.cli import _clip

    assert _clip("short", 20) == "short"
    assert _clip("a" * 40, 20) == "a" * 17 + "..."
    assert len(_clip("a" * 40, 20)) == 20


def test_an_unknown_flow_exits_with_the_missing_path(tmp_path: Path):
    """Resolution failure must be a clean message, not a traceback or a hang."""
    out = io.StringIO()
    with pytest.raises(SystemExit, match="absent"):
        main(["--flow", str(tmp_path / "absent.rule")], out=out)


def test_a_bad_backend_on_the_command_line_is_caught_by_argparse(tmp_path: Path):
    out = io.StringIO()
    with pytest.raises(SystemExit):
        main(["--flow", "refund_triage", "--backend", "gpt5"], out=out)


def test_a_bad_suggest_in_the_rule_file_is_caught_by_the_guard(tmp_path: Path):
    """--backend is a closed set, so the guard really covers @suggest typos."""
    rule = tmp_path / "tiny.rule"
    rule.write_text(
        "@rule tiny\n@suggest gpt5\n@start end\n@node end\n@end finished\n", encoding="utf-8"
    )
    out = io.StringIO()
    with pytest.raises(SystemExit, match="gpt5"):
        main(["--flow", str(rule)], out=out)


def test_the_summary_table_dispatches_on_the_json_flag():
    from engine.cli import _SUMMARY, _json_summary, _no_summary

    assert _SUMMARY == {True: _json_summary, False: _no_summary}


def test_json_mode_writes_exactly_one_object_per_line():
    """Regression: the plugins banner used to print raw text into the stream."""
    import json

    from engine.cli import _emit

    events = [
        {"kind": "plugins", "detail": "loaded plugins.py (8 actions registered)"},
        {"kind": "start", "flow": "x", "backend": "jev", "run": "r", "seed": "s"},
        {"kind": "error", "message": "boom"},
    ]
    out = io.StringIO()
    for event in events:
        _emit(event, out, as_json=True)
    lines = out.getvalue().splitlines()
    assert len(lines) == len(events)
    assert [json.loads(line)["kind"] for line in lines] == ["plugins", "start", "error"]


def test_the_plugins_banner_still_renders_in_text_mode():
    from engine.cli import render

    lines = render({"kind": "plugins", "detail": "loaded plugins.py (8 actions registered)"})
    assert lines
    assert "plugins" in lines[0]


def test_the_json_summary_is_a_single_parseable_line(rule, stub):
    """The last line has to be one object, or the stream is not JSON lines."""
    import json

    from engine.cli import _emit, _json_summary
    from engine.core import Engine

    trace = Engine(ruleset=rule, backend=stub, on_event=lambda event: None).run("seed")
    out = io.StringIO()
    _json_summary(trace, out)
    lines = out.getvalue().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == trace.to_json()

    # And the whole stream, events plus summary, parses line by line.
    stream = io.StringIO()
    trace = Engine(
        ruleset=rule, backend=stub, on_event=lambda event: _emit(event, stream, True)
    ).run("seed")
    _json_summary(trace, stream)
    parsed = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert parsed[-1]["status"] == trace.status

