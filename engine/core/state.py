"""The accumulated state.

There is no structured state object the engine branches on — the state *is*
free text. Each decision appends a sentence, so later layers can feel the
earlier ones the way an energy model feels any other piece of evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.backends.base import Answer, CHOICE, Decision, NOUL, SCORE, Question
from engine.rules.model import Node, Option

#: A confidence suffix, chosen by a table instead of a ternary.
_CONFIDENCE = {True: " (confidence {0:.2f})", False: ""}


def _legend_for(question: Question, answer: Answer) -> str:
    """Map a continuous score back onto its band label.

    The ``[index:index + 1]`` slice is the branch-free spelling of
    ``criteria[index] if criteria else ""`` — a one-element slice joins to the
    label, an empty slice joins to the empty string.
    """
    criteria = list(question.criteria or [])
    index = max(0, min(len(criteria) - 1, int(round(float(answer.value or 0)))))
    return "".join(criteria[index : index + 1])


def _score_line(question: Question, answer: Answer) -> str:
    span = len(question.criteria or []) - 1
    band = _legend_for(question, answer)
    return (
        f"Signal '{question.name}' (score 0-{span}) = {float(answer.value or 0):.2f}"
        f" -> {band}"
    )


def _noul_line(question: Question, answer: Answer) -> str:
    verdict = ("yes", "no")[float(answer.value or 0) < 0.5]
    return (
        f"Signal '{question.name}' (yes/no) = {float(answer.value or 0):.3f}"
        f" -> leans {verdict}"
    )


def _choice_line(question: Question, answer: Answer) -> str:
    criteria = dict(question.criteria or {})
    return (
        f"Signal '{question.name}' = {answer.value}"
        f" ({criteria.get(answer.value, '')})"
    )


_SIGNAL_LINES = {
    SCORE: _score_line,
    NOUL: _noul_line,
    CHOICE: _choice_line,
}


@dataclass
class StateLedger:
    """A growing block of text handed to the model at every layer."""

    seed: str
    facts: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join([self.seed, *self.facts])

    def note(self, fact: str) -> None:
        self.facts.append(fact)

    def absorb_choice(self, node: Node, option: Option, answer: Answer) -> None:
        suffix = _CONFIDENCE[bool(answer.confidence)].format(answer.confidence)
        self.note(f"Layer '{node.key}' -> chose '{option.key}': {option.semantics}{suffix}")

    def absorb_signals(self, node: Node, decision: Decision) -> None:
        for question in node.signals:
            answer = decision.answer(question.name)
            self.note(_SIGNAL_LINES[question.qtype](question, answer))

    def absorb(self, node: Node, option: Option, decision: Decision) -> None:
        self.absorb_choice(node, option, decision.answer(node.key))
        self.absorb_signals(node, decision)
