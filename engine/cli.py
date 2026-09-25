"""The command line runner for a rule mesh.

Branch-free, like the rest of ``engine/``. Every decision is a dict lookup, and
``tests/test_purity.py`` scans this file too — so the claim "no if/else anywhere
except choosing JEV or LAYA" covers the runner as well.

    python -m engine --flow refund_triage --seed "charged twice for one order"
    python -m engine --flow loan_underwriting --backend laya
    python -m engine --list

``--flow`` accepts a literal path or a bare name, resolved against ``rules/``.
``--backend`` defaults to whatever the rule file suggests with ``@suggest``.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path
from typing import Callable, Sequence, TextIO

from engine.backends import JEV, KINDS, LAYA, create_backend
from engine.config import ROOT, load_env
from engine.core import Engine
from engine.core.actions import load_plugins
from engine.rules import RuleSet
from engine.rules import load as load_rule

PANEL = 78


# --------------------------------------------------------------------------- #
# text helpers — branch-free by construction
# --------------------------------------------------------------------------- #
def _line(char: str = "-") -> str:
    return char * PANEL


def _prefix(first: str, rest: str, index: int) -> str:
    """The first line of a block gets a label, later lines get padding."""
    return {False: first, True: rest}[bool(index)]


def _block(label: str, text: str, indent: int = 4, width: int = PANEL) -> list[str]:
    """A labelled, wrapped block. Empty text yields no lines at all."""
    lead = " " * indent + f"{label:<10} "
    cont = " " * len(lead)
    lines = textwrap.wrap(text, width=max(24, width - len(lead)))
    return [_prefix(lead, cont, index) + line for index, line in enumerate(lines)]


def _bar(probability: float, width: int = 16) -> str:
    filled = int(round(max(0.0, min(1.0, probability)) * width))
    return "#" * filled + "." * (width - filled)


def _spread(probabilities: dict, chosen: str) -> list[str]:
    ranked = sorted(probabilities.items(), key=lambda item: -item[1])
    return [
        f"    {_MARK[bool(key == chosen)]} {key:<14} {value:6.3f}  {_bar(float(value))}"
        for key, value in ranked
    ]


#: Marks the winning option in a probability listing.
_MARK = {True: ">", False: " "}


def _signal_lines(signals: list[dict]) -> list[str]:
    """The non-branching answers the model returned, if the layer asked for any."""
    return _block(
        "read",
        "; ".join(f"{s['name']}={s['value']} ({s['type']})" for s in signals),
    )


# --------------------------------------------------------------------------- #
# event renderers — one per event kind, selected by a table
# --------------------------------------------------------------------------- #
def _r_start(event: dict) -> list[str]:
    return [
        "",
        _line("="),
        f"  {event['flow']}",
        _line("="),
        f"  backend    {event['backend']}",
        f"  source     {event.get('source') or '<in memory>'}",
        f"  run        {event['run']}",
        *_block("seed", event["seed"], indent=2),
    ]


def _r_layer(event: dict) -> list[str]:
    heading = f"L{event['layer']}  {event['node']}"
    return [
        "",
        _line(),
        heading + "  [terminal]" * event["terminal"],
        *_block("ask", event["ask"]),
        *_block("note", event["note"]),
        *_block(
            "effects",
            ", ".join(f"{e['action']}({' '.join(e['args'])})" for e in event["effects"]),
        ),
        *_block(
            "signals",
            "; ".join(f"{s['name']} ({s['type']})" for s in event["signals"]),
        ),
    ]


def _r_decision(event: dict) -> list[str]:
    return [
        *_block("chose", f"{event['chosen']}  —  {event['semantics']}"),
        *_block("confidence", f"{event['confidence']:.3f}"),
        "",
        *_spread(event["probabilities"], event["chosen"]),
        "",
        *_signal_lines(event["signals"]),
        *_block("routes to", f"{event['routing']}  ->  {event['target']}"),
        *_block("calls", " ".join(event["args"])),
    ]


def _r_action(event: dict) -> list[str]:
    return _block(
        f"! {event['at']}",
        f"{event['action']} {' '.join(event['args'])}  [{event['status']}] {event['detail']}",
    )


def _r_end_layer(event: dict) -> list[str]:
    return ["", _line(), f"END  {event['node']}", *_block("note", event["note"], indent=4)]


def _r_error(event: dict) -> list[str]:
    return ["", _line("!"), f"  ERROR  {event['message']}", _line("!")]


def _r_plugins(event: dict) -> list[str]:
    return _block("plugins", event["detail"], indent=2)


def _r_end(event: dict) -> list[str]:
    outcomes = [f"    {a['at']:<7} {a['action']:<12} {a['status']:<15} {a['detail']}" for a in event["actions"]]
    return [
        "",
        _line("="),
        f"  {event['status'].upper()}  after {event['layers']} layer(s)  model {event['model'] or 'n/a'}",
        f"  usage    {json.dumps(event['usage'])}",
        _line("="),
        "  actions taken",
        *outcomes,
        "",
        "  final state",
        *_block("state", event["state"], indent=4),
    ]


#: Event kind -> renderer. An unknown kind renders as nothing.
_RENDERERS: dict[str, Callable[[dict], list[str]]] = {
    "start": _r_start,
    "layer": _r_layer,
    "decision": _r_decision,
    "action": _r_action,
    "end-layer": _r_end_layer,
    "error": _r_error,
    "end": _r_end,
    "plugins": _r_plugins,
}


def render(event: dict) -> list[str]:
    return _RENDERERS.get(event["kind"], _skip)(event)


def _skip(event: dict) -> list[str]:
    return []


# --------------------------------------------------------------------------- #
# flow + backend resolution
# --------------------------------------------------------------------------- #
_FLOW_CANDIDATES: tuple[Callable[[str], Path], ...] = (
    lambda value: Path(value),
    lambda value: ROOT / "rules" / f"{value}.rule",
    lambda value: ROOT / "rules" / value,
    lambda value: ROOT / value,
)

_BACKEND_ALIASES: dict[str, str] = {
    "": "",
    "jev": JEV,
    "openrouter": JEV,
    "laya": LAYA,
    "local": LAYA,
}


def resolve_flow(value: str) -> Path:
    """Accept a path, a bare stem, or a name under ``rules/``."""
    candidates = map(lambda build: build(value), _FLOW_CANDIDATES)
    matches = list(filter(Path.exists, candidates))
    return next(iter(matches), Path(value))


def resolve_backend(requested: str, ruleset: RuleSet) -> str:
    wanted = (requested or ruleset.suggested_backend or JEV).strip().lower()
    return _BACKEND_ALIASES.get(wanted, wanted)


# --------------------------------------------------------------------------- #
# guards — the tables that stand in for if/else
# --------------------------------------------------------------------------- #
def _missing_flow(path: Path) -> None:
    raise SystemExit(
        f"no such rule file: {path}\n"
        f"  try one of: {', '.join(sorted(p.stem for p in (ROOT / 'rules').glob('*.rule')))}"
    )


def _found_flow(path: Path) -> None:
    return None


_FLOW_GUARD: dict[bool, Callable[[Path], None]] = {True: _found_flow, False: _missing_flow}


def _bad_backend(kind: str) -> None:
    raise SystemExit(f"unknown backend '{kind}' — choose one of: {', '.join(KINDS)}")


def _good_backend(kind: str) -> None:
    return None


_BACKEND_GUARD: dict[bool, Callable[[str], None]] = {True: _good_backend, False: _bad_backend}


# --------------------------------------------------------------------------- #
# subcommands
# --------------------------------------------------------------------------- #
_CLIP = {
    True: lambda text, width: text,
    False: lambda text, width: text[: width - 3] + "...",
}


def _clip(text: str, width: int) -> str:
    """Shorten text to width, marking the cut so it does not read as a typo."""
    return _CLIP[len(text) <= width](text, width)


def _rule_row(path: Path) -> str:
    ruleset = load_rule(path)
    endings = sum(len(node.actions) for node in ruleset.nodes.values())
    head = (
        f"  {path.stem:<22} {ruleset.suggested_backend or '-':<6} "
        f"{len(ruleset.nodes):>2} nodes {endings:>2} endings  "
    )
    return head + _clip(ruleset.title, PANEL - len(head))


def list_rules(out: TextIO = sys.stdout) -> int:
    """Print every rule file with the backend it suggests and its size."""
    rows = [_rule_row(path) for path in sorted((ROOT / "rules").glob("*.rule"))]
    header = ["", "  rule files", f"  {_line('=')}"]
    out.writelines(f"{line}\n" for line in [*header, *rows, ""])
    return 0


def run_flow(args: argparse.Namespace, out: TextIO = sys.stdout) -> int:
    load_env()
    plugin = load_plugins(ROOT)
    path = resolve_flow(args.flow)
    _FLOW_GUARD[path.exists()](path)
    ruleset = load_rule(path)
    kind = resolve_backend(args.backend, ruleset)
    _BACKEND_GUARD[kind in KINDS](kind)
    backend = create_backend(kind, device=args.device, preload=args.preload)
    engine = Engine(
        ruleset=ruleset,
        backend=backend,
        root=ROOT,
        live=args.live,
        max_layers=args.max_layers,
        on_event=lambda event: _emit(event, out, args.json),
    )
    # Emitted as a real event so --json stays one JSON object per line.
    _emit({"kind": "plugins", "detail": plugin}, out, args.json)
    trace = engine.run(args.seed)
    _SUMMARY[args.json](trace, out)
    return {True: 0, False: 1}[trace.status == "completed"]


def _no_summary(trace, out: TextIO) -> None:
    return None


def _emit(event: dict, out: TextIO, as_json: bool) -> None:
    payload = {True: [json.dumps(event)], False: render(event)}
    out.writelines(f"{line}\n" for line in payload[as_json])


def _json_summary(trace, out: TextIO) -> None:
    """The final trace, as one line, so the whole stream parses as JSON lines."""
    out.writelines([json.dumps(trace.to_json(), separators=(",", ":")), "\n"])


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m engine",
        description="Run a .rule mesh through a JEV or LAYA energy model.",
    )
    parser.add_argument("--flow", "-f", default="refund_triage", help="rule file path or bare name")
    parser.add_argument(
        "--backend",
        "-b",
        default="",
        choices=("", *KINDS, "openrouter", "local"),
        help="jev (remote energy model) or laya (local). Defaults to the rule's @suggest.",
    )
    parser.add_argument("--seed", "-s", default="", help="the free-text situation to start from")
    parser.add_argument("--live", action="store_true", help="really send http actions instead of simulating")
    parser.add_argument("--json", action="store_true", help="stream events as JSON lines")
    parser.add_argument("--max-layers", type=int, default=80)
    parser.add_argument("--device", default=None, help="torch device for LAYA, e.g. cpu")
    parser.add_argument("--preload", action="store_true", help="warm the LAYA checkpoint before running")
    parser.add_argument("--list", action="store_true", help="show the available rule files and exit")
    return parser


#: Whether to dump the whole trace as JSON once the run finishes.
_SUMMARY: dict[bool, Callable] = {True: _json_summary, False: _no_summary}


#: Where to read argv from, keyed on whether the caller passed one.
_ARGV_SOURCE: dict[bool, Callable[[Sequence[str] | None], Sequence[str]]] = {
    True: lambda argv: sys.argv[1:],
    False: lambda argv: argv or (),
}


def main(argv: Sequence[str] | None = None, out: TextIO = sys.stdout) -> int:
    args = build_parser().parse_args(list(_ARGV_SOURCE[argv is None](argv)))
    return {True: lambda: list_rules(out), False: lambda: run_flow(args, out)}[args.list]()
