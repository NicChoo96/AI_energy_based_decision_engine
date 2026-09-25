"""The traversal driver.

Read this file as the *absence* of logic. There is no rule here about billing
or incidents or loans — not one comparison of business values. The engine can
do exactly three things:

1. ask the model a question and look the answer up in a table,
2. follow the table entry it landed on,
3. run whatever action the table entry names.

Every branch in every flow is chosen by JEV or LAYA. ``tests/test_purity.py``
parses this package with :mod:`ast` and fails if an ``if`` or a ternary ever
appears outside :mod:`engine.backends.factory`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from engine.core import actions
from engine.core.actions import ActionContext
from engine.core.state import StateLedger
from engine.rules.model import (
    LAND_ACTION,
    LAND_GOTO,
    NODE_ROUTE,
    NODE_TERMINAL,
    Effect,
    Node,
    Option,
    RuleSet,
    Trace,
    summarise_signals,
)

EventSink = Callable[[dict], None]


class FlowError(RuntimeError):
    """Base class for anything that goes wrong while walking a rule mesh."""


class FlowComplete(Exception):
    """Raised to unwind the traversal loop once a leaf has been reached."""


class UnknownLayer(FlowError):
    """A ``->`` pointed at a layer that does not exist."""


class UnknownOption(FlowError):
    """The model returned an option key that is not on the menu."""


def _ok_option(node: Node, decision) -> None:
    return None


def _raise_unknown_option(node: Node, decision) -> None:
    raise UnknownOption(
        f"layer '{node.key}' returned '{decision.pick(node.key)}', which is not one of "
        f"{sorted(node.option_index)}"
    )


_OPTION_GUARD: dict[bool, Callable[[Node, Any], None]] = {
    False: _ok_option,
    True: _raise_unknown_option,
}


@dataclass
class Engine:
    """Walks a :class:`RuleSet` using one energy-based decision backend."""

    ruleset: RuleSet
    backend: Any
    root: Path = field(default_factory=Path.cwd)
    live: bool = False
    max_layers: int = 80
    on_event: EventSink | None = None

    def __post_init__(self) -> None:
        self._steps: dict[str, Callable] = {
            NODE_ROUTE: self._step_route,
            NODE_TERMINAL: self._step_terminal,
        }
        self._landings: dict[str, Callable] = {
            LAND_GOTO: self._land_goto,
            LAND_ACTION: self._land_action,
        }

    # ----------------------------------------------------------------- run --
    def run(self, seed: str) -> Trace:
        trace = Trace(
            flow=self.ruleset.title,
            backend=getattr(self.backend, "name", "unknown"),
            seed=seed,
            source=self.ruleset.source,
        )
        ledger = StateLedger(seed=seed)
        run_id = uuid4().hex[:12]
        budget = iter(range(self.max_layers))
        self._emit(
            trace,
            {
                "kind": "start",
                "flow": trace.flow,
                "backend": trace.backend,
                "seed": seed,
                "run": run_id,
                "source": trace.source,
            },
        )
        try:
            cursor = self.ruleset.start
            while True:
                next(budget)
                cursor = self._visit(cursor, ledger, trace, run_id)
        except FlowComplete:
            self._settle(trace, ledger, "completed")
        except StopIteration:
            self._settle(trace, ledger, "budget-exceeded")
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI as an event
            self._emit(trace, {"kind": "error", "message": f"{type(exc).__name__}: {exc}"})
            self._settle(trace, ledger, "failed")
        return trace

    # ------------------------------------------------------------- walking --
    def _visit(self, key: str, ledger: StateLedger, trace: Trace, run_id: str) -> str:
        try:
            node = self.ruleset.nodes[key]
        except KeyError:
            raise UnknownLayer(f"no layer named '{key}'") from None
        return self._steps[node.kind](node, ledger, trace, run_id)

    def _step_route(self, node: Node, ledger: StateLedger, trace: Trace, run_id: str) -> str:
        self._fire_effects(node, ledger, trace, run_id)
        trace.layers += 1
        self._emit(trace, self._layer_event(node, trace.layers))
        decision = self.backend.decide(ledger.text, node.questions)
        self._absorb_usage(trace, decision)
        option = _resolve_option(node, decision)
        ledger.absorb(node, option, decision)
        trace.steps.append(self._decision_event(node, option, decision, trace.layers))
        self._emit(trace, trace.steps[-1])
        return self._landings[option.kind](option, ledger, trace, run_id)

    def _step_terminal(self, node: Node, ledger: StateLedger, trace: Trace, run_id: str) -> str:
        self._fire_effects(node, ledger, trace, run_id)
        self._emit(
            trace,
            {"kind": "end-layer", "layer": trace.layers, "node": node.key, "note": node.note},
        )
        raise FlowComplete()

    def _land_goto(self, option: Option, ledger: StateLedger, trace: Trace, run_id: str) -> str:
        ledger.note(f"Continue to layer '{option.target}'.")
        return option.target

    def _land_action(self, option: Option, ledger: StateLedger, trace: Trace, run_id: str) -> str:
        self._fire(option.target, option.args, ledger, trace, run_id, kind="final")
        raise FlowComplete()

    # ------------------------------------------------------------- effects --
    def _fire_effects(self, node: Node, ledger: StateLedger, trace: Trace, run_id: str) -> None:
        for effect in node.effects:
            self._fire(effect.action, effect.args, ledger, trace, run_id, kind="effect")

    def _fire(
        self,
        name: str,
        args: tuple[str, ...],
        ledger: StateLedger,
        trace: Trace,
        run_id: str,
        kind: str,
    ) -> None:
        context = ActionContext(
            flow=self.ruleset.title,
            run_id=run_id,
            root=Path(self.root),
            ledger=ledger,
            live=self.live,
        )
        result = actions.perform(name, context, tuple(args))
        payload = {"kind": "action", "at": kind, "layer": trace.layers, **result.to_json()}
        trace.actions.append(payload)
        self._emit(trace, payload)

    # -------------------------------------------------------------- events --
    def _emit(self, trace: Trace, event: dict) -> None:
        trace.events.append(event)
        self.on_event and self.on_event(event)

    def _settle(self, trace: Trace, ledger: StateLedger, status: str) -> None:
        trace.status = status
        trace.state = ledger.text
        self._emit(
            trace,
            {
                "kind": "end",
                "status": status,
                "layers": trace.layers,
                "model": trace.model,
                "usage": trace.usage,
                "actions": trace.actions,
                "state": ledger.text,
            },
        )

    def _absorb_usage(self, trace: Trace, decision) -> None:
        trace.model = decision.model or trace.model
        for key, value in dict(decision.usage).items():
            try:
                trace.usage[key] = trace.usage.get(key, 0) + value
            except TypeError:
                trace.usage[key] = value

    def _layer_event(self, node: Node, index: int) -> dict:
        return {
            "kind": "layer",
            "layer": index,
            "node": node.key,
            "ask": node.ask,
            "note": node.note,
            "terminal": node.kind == NODE_TERMINAL,
            "effects": [{"action": e.action, "args": list(e.args)} for e in node.effects],
            "signals": [
                {
                    "name": signal.name,
                    "type": signal.qtype,
                    "instructions": signal.instructions,
                    "criteria": signal.criteria,
                }
                for signal in node.signals
            ],
            "options": [option.to_json() for option in node.options],
        }

    def _decision_event(self, node: Node, option: Option, decision, index: int) -> dict:
        answer = decision.answer(node.key)
        return {
            "kind": "decision",
            "layer": index,
            "node": node.key,
            "ask": node.ask,
            "chosen": option.key,
            "semantics": option.semantics,
            "confidence": answer.confidence,
            "probabilities": dict(answer.probabilities),
            "candidates": [option.to_json() for option in node.options],
            "routing": option.kind,
            "target": option.target,
            "args": list(option.args),
            "model": decision.model,
            "signals": summarise_signals(node, decision),
        }


def _resolve_option(node: Node, decision) -> Option:
    option = node.option_index.get(decision.pick(node.key))
    _OPTION_GUARD[option is None](node, decision)
    return option


def describe_effects(effects: tuple[Effect, ...]) -> str:
    """Small helper used by the CLI banner."""
    return ", ".join(f"{effect.action}({' '.join(effect.args)})" for effect in effects)
