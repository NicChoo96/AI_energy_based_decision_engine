"""The rule data model: layers, options, signals and effects."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import chain
from typing import Callable, Mapping

from engine.backends.base import CHOICE, Question

# Where an option sends the flow.
LAND_GOTO = "goto"
LAND_ACTION = "action"

# What a layer is.
NODE_ROUTE = "route"
NODE_TERMINAL = "terminal"

#: Which node keys an option lands on, per landing kind. A goto contributes its
#: target; an action contributes nothing because it terminates the flow.
#: Written as a table so :attr:`Node.links` needs no filter comprehension.
_LINKS_OF: dict[str, Callable[["Option"], tuple[str, ...]]] = {
    LAND_GOTO: lambda option: (option.target,),
    LAND_ACTION: lambda option: (),
}

#: Which options terminate the flow, per landing kind.
_ACTIONS_OF: dict[str, Callable[["Option"], tuple["Option", ...]]] = {
    LAND_GOTO: lambda option: (),
    LAND_ACTION: lambda option: (option,),
}


@dataclass(frozen=True)
class Effect:
    """A side effect fired *before* a layer asks its question (or at ``@end``)."""

    action: str
    args: tuple[str, ...] = ()


@dataclass(frozen=True)
class Option:
    """One candidate answer to a layer's routing question.

    ``kind`` is either :data:`LAND_GOTO` (``target`` is the next layer key) or
    :data:`LAND_ACTION` (``target`` is an action name — the flow ends there).
    """

    key: str
    semantics: str
    kind: str
    target: str
    args: tuple[str, ...] = ()

    def to_json(self) -> dict:
        return {
            "key": self.key,
            "semantics": self.semantics,
            "kind": self.kind,
            "target": self.target,
            "args": list(self.args),
        }


@dataclass(frozen=True)
class Node:
    """A single layer of the rule mesh."""

    key: str
    ask: str
    kind: str
    signals: tuple[Question, ...] = ()
    options: tuple[Option, ...] = ()
    effects: tuple[Effect, ...] = ()
    note: str = ""

    @property
    def question(self) -> Question:
        """The ``choice`` question the model answers to pick the next branch."""
        return Question(
            name=self.key,
            qtype=CHOICE,
            instructions=self.ask,
            criteria={option.key: option.semantics for option in self.options},
        )

    @property
    def questions(self) -> tuple[Question, ...]:
        """Routing question first, then every non-branching signal."""
        return (self.question, *self.signals)

    @property
    def option_index(self) -> Mapping[str, Option]:
        return {option.key: option for option in self.options}

    @property
    def links(self) -> tuple[str, ...]:
        """Node keys this layer can route to. Action options terminate instead."""
        return tuple(
            chain.from_iterable(_LINKS_OF[option.kind](option) for option in self.options)
        )

    @property
    def actions(self) -> tuple[Option, ...]:
        """Options that end the flow by calling an action."""
        return tuple(
            chain.from_iterable(_ACTIONS_OF[option.kind](option) for option in self.options)
        )

    @property
    def question_texts(self) -> tuple[str, ...]:
        """The prompts this layer will send, in order, for display purposes."""
        return tuple(question.name for question in self.questions)
@dataclass(frozen=True)
class RuleSet:
    """A parsed ``.rule`` file, ready to execute."""

    title: str
    start: str
    nodes: Mapping[str, Node]
    source: str = ""
    about: str = ""
    examples: tuple[str, ...] = ()
    suggested_backend: str = ""

    @property
    def entry(self) -> Node:
        return self.nodes[self.start]

    def to_json(self) -> dict:
        return {
            "title": self.title,
            "about": self.about,
            "start": self.start,
            "examples": list(self.examples),
            "source": self.source,
            "suggestedBackend": self.suggested_backend,
            "nodes": [
                {
                    "key": node.key,
                    "kind": node.kind,
                    "ask": node.ask,
                    "note": node.note,
                    "signals": [
                        {
                            "name": signal.name,
                            "type": signal.qtype,
                            "instructions": signal.instructions,
                            "criteria": signal.criteria,
                        }
                        for signal in node.signals
                    ],
                    "effects": [
                        {"action": effect.action, "args": list(effect.args)}
                        for effect in node.effects
                    ],
                    "options": [option.to_json() for option in node.options],
                }
                for node in self.nodes.values()
            ],
        }


@dataclass
class Trace:
    """Everything that happened during one execution of a rule set."""

    flow: str
    backend: str
    seed: str
    source: str = ""
    model: str = ""
    status: str = "running"
    layers: int = 0
    events: list[dict] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    state: str = ""

    def to_json(self) -> dict:
        return {
            "flow": self.flow,
            "backend": self.backend,
            "model": self.model,
            "source": self.source,
            "seed": self.seed,
            "status": self.status,
            "layers": self.layers,
            "steps": self.steps,
            "actions": self.actions,
            "events": self.events,
            "usage": self.usage,
            "state": self.state,
        }


def summarise_signals(node: Node, decision) -> list[dict]:
    """Human-readable rendering of a layer's non-branching answers."""
    return [
        {
            "name": signal.name,
            "type": signal.qtype,
            "instructions": signal.instructions,
            "value": decision.answer(signal.name).value,
            "confidence": decision.answer(signal.name).confidence,
            "probabilities": dict(decision.answer(signal.name).probabilities),
            "legend": dict(decision.answer(signal.name).legend),
            "criteria": signal.criteria,
        }
        for signal in node.signals
    ]
