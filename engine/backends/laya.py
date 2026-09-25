"""**LAYA** — the same decision vocabulary, running locally on your machine."""

from __future__ import annotations

from typing import Any, Sequence

from engine.backends.base import Decision, Question, normalise

DEFAULT_MODEL = "english"
MODELS = ("english", "multilingual", "typed-decisions")


class LayaBackend:
    """Wraps ``laya.Router``.

    ``Router`` downloads its checkpoint on first use; pass ``preload=True`` to
    pull every head into memory up front (slower start, faster decisions).
    """

    name = "laya"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        device: str | None = None,
        preload: bool = False,
        token: str | None = None,
        max_loaded: int = 2,
    ) -> None:
        from laya import Router

        self.model = model
        self._router = Router(
            device=device,
            token=token,
            max_loaded=max_loaded,
            default=model,
            preload=preload,
        )

    def decide(self, state: str, questions: Sequence[Question]) -> Decision:
        raw: dict[str, Any] = self._router.predict(
            state, {q.name: q.to_payload() for q in questions}
        )
        return normalise(raw, questions)
