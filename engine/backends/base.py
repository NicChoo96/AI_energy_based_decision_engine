"""Wire vocabulary shared by every energy-based decision backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

CHOICE = "choice"
SCORE = "score"
NOUL = "noul"

QTYPES = (CHOICE, SCORE, NOUL)

#: Which key of an answer object carries the payload, per question type.
VALUE_KEY = {CHOICE: "choice", SCORE: "score", NOUL: "noul"}

#: A ``noul`` question accepts exactly these two criteria keys, no more and no
#: fewer. LAYA validates this and rejects anything else, so the rule files may
#: write the friendlier ``yes``/``no`` and we canonicalise on the way out.
NOUL_KEYS = ("false", "true")

_DEFAULT_NOUL_WORDING = {
    "false": "no, the statement does not hold",
    "true": "yes, the statement holds",
}

_NOUL_SPELLING = {
    "yes": "true",
    "no": "false",
    "y": "true",
    "n": "false",
    "true": "true",
    "false": "false",
}


def normalise_noul_key(key: str) -> str:
    """Map every friendly spelling onto the two keys the wire format accepts."""
    return _NOUL_SPELLING.get(str(key).strip().lower(), str(key).strip().lower())


def _keep_words(wording: dict) -> dict:
    return dict(wording)


def _default_words(wording: dict) -> dict:
    return dict(_DEFAULT_NOUL_WORDING)


#: LAYA requires the two labels to be distinct non-empty strings. If a rule file
#: happens to give the same text for both, fall back rather than fail the run.
_DISTINCT_WORDS = {True: _keep_words, False: _default_words}


def _noul_payload(criteria: Any) -> dict:
    """Build the ``criteria``/``labels`` pair a ``noul`` question needs.

    ``criteria`` carries which polarities the rule file named (either or both is
    legal), ``labels`` carries the wording the model actually reads.
    """
    given = {
        normalise_noul_key(key): value for key, value in dict(criteria or {}).items()
    }
    wording = {**_DEFAULT_NOUL_WORDING, **given}
    return {
        "criteria": given,
        "labels": _DISTINCT_WORDS[wording["false"] != wording["true"]](wording),
    }


_CRITERIA_BUILDERS = {
    CHOICE: lambda criteria: {"criteria": dict(criteria or {})},
    SCORE: lambda criteria: {"criteria": list(criteria or [])},
    NOUL: _noul_payload,
}


@dataclass(frozen=True)
class Question:
    """One classifier question handed to the model.

    ``choice`` questions pick a key from ``criteria`` (a mapping), ``score``
    questions place the state on an ordered scale (a sequence of band labels)
    and ``noul`` questions return the probability that the statement holds.
    """

    name: str
    qtype: str
    instructions: str
    criteria: Any = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "type": self.qtype,
            "instructions": self.instructions,
            **_CRITERIA_BUILDERS[self.qtype](self.criteria),
        }


@dataclass(frozen=True)
class Answer:
    """The model's response to a single :class:`Question`."""

    name: str
    qtype: str
    value: Any
    confidence: float = 0.0
    probabilities: Mapping[str, float] = field(default_factory=dict)
    legend: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    """Every answer produced by one model call, plus provenance."""

    model: str
    answers: Mapping[str, Answer]
    usage: Mapping[str, Any] = field(default_factory=dict)
    raw: Mapping[str, Any] = field(default_factory=dict)

    def pick(self, name: str) -> Any:
        return self.answers[name].value

    def answer(self, name: str) -> Answer:
        return self.answers[name]


@runtime_checkable
class DecisionBackend(Protocol):
    """Anything that can turn (state, questions) into a :class:`Decision`."""

    name: str

    def decide(self, state: str, questions: Sequence[Question]) -> Decision: ...


def _to_answer(question: Question, raw: Mapping[str, Any]) -> Answer:
    qtype = raw.get("type") or question.qtype
    return Answer(
        name=question.name,
        qtype=qtype,
        value=raw.get(VALUE_KEY.get(qtype, VALUE_KEY[question.qtype])),
        confidence=float(raw.get("confidence") or 0.0),
        probabilities=dict(raw.get("probabilities") or {}),
        legend=dict(raw.get("legend") or {}),
    )


def normalise(raw: Mapping[str, Any], questions: Sequence[Question]) -> Decision:
    """Fold a JEV/LAYA response body into a backend-neutral :class:`Decision`."""
    answers = dict(raw.get("answers") or {})
    return Decision(
        model=str(raw.get("model") or ""),
        answers={q.name: _to_answer(q, answers.get(q.name) or {}) for q in questions},
        usage=dict(raw.get("usage") or {}),
        raw=dict(raw),
    )
