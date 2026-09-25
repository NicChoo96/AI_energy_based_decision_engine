"""A backend that answers from a script instead of a model.

Kept out of ``conftest.py`` so tests can import it directly.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from engine.backends.base import CHOICE, NOUL, SCORE, Answer, Decision, Question

SAMPLE_RULE = """\
@rule    Stub flow
@about   Exercises every feature of the language in four layers.
@suggest jev
@example A seed example
@start   classify

@node    classify | first layer
@effect  annotate | triage started
@ask     Which family is this?
@option  alpha | first family  | -> alpha_play
@option  beta  | second family | ! trigger | mailbox | beta-handled | Beta was handled by trigger

@node    alpha_play
@signal  urgency | score  | How urgent is it? | can wait | soon | blocking now
@signal  churn   | noul   | Might they leave?  | yes = a threat to leave | no = no threat
@signal  lane    | choice | Which lane?        | fast = the quick lane | slow = the slow lane
@ask     What should be done?
@option  refund | give the money back | ! trigger | payments | refunded | Refund issued
@option  deep   | investigate first   | -> deep_dive

@node    deep_dive
@ask     How deep?
@option  shallow | a quick look       | ! annotate | looked briefly
@option  endless | never-ending drill | -> never_return

@node    never_return
@end
@note    A pure effect layer: no question, just work and then stop.
@effect  record | dead-end
"""


class StubBackend:
    """A backend that answers from a script.

    ``picks`` maps a layer key to the option key that layer should choose,
    ``scores`` maps a signal name to a number and ``noul`` maps a signal name to
    a probability. Anything unscripted falls back to the first option on the
    menu and to the middle of a score scale, so a test only states what it cares
    about.
    """

    name = "stub"

    def __init__(
        self,
        picks: Mapping[str, str] | None = None,
        scores: Mapping[str, float] | None = None,
        noul: Mapping[str, float] | None = None,
    ) -> None:
        self.picks = dict(picks or {})
        self.scores = dict(scores or {})
        self.noul = dict(noul or {})
        #: every (state, question-names) pair the engine presented, in order
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def decide(self, state: str, questions: Sequence[Question]) -> Decision:
        self.calls.append((state, tuple(question.name for question in questions)))
        return Decision(
            model="stub-0",
            answers={question.name: self._answer(question) for question in questions},
            usage={"calls": 1},
        )

    def _answer(self, question: Question) -> Answer:
        builders = {CHOICE: self._choice, SCORE: self._score, NOUL: self._noul}
        return builders[question.qtype](question)

    def _choice(self, question: Question) -> Answer:
        keys = list(question.criteria or {})
        chosen = self.picks.get(question.name, keys[0])
        return Answer(
            name=question.name,
            qtype=CHOICE,
            value=chosen,
            confidence=0.75,
            probabilities={key: 0.05 for key in keys} | {chosen: 0.75},
        )

    def _score(self, question: Question) -> Answer:
        bands = list(question.criteria or [])
        return Answer(
            name=question.name,
            qtype=SCORE,
            value=self.scores.get(question.name, len(bands) / 2),
            confidence=0.6,
            probabilities={str(index): 0.5 for index, _ in enumerate(bands)},
            legend={str(index): band for index, band in enumerate(bands)},
        )

    def _noul(self, question: Question) -> Answer:
        value = self.noul.get(question.name, 0.25)
        return Answer(name=question.name, qtype=NOUL, value=value, confidence=value)
