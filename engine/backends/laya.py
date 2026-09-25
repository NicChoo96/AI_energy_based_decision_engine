"""**LAYA** — the same decision vocabulary, running locally on your machine."""

from __future__ import annotations

import sys
from typing import Any, Sequence

from engine.backends.base import Decision, Question, normalise

DEFAULT_MODEL = "english"
MODELS = ("english", "multilingual", "typed-decisions")

#: ``laya`` is imported lazily, on the first decision. That is deliberate — JEV
#: users should never pay for the import — but it means a missing package only
#: surfaces once a run is already under way, and it surfaces in the browser as a
#: bare traceback. FastAPI being installed is enough to start the console, so the
#: real cause (the wrong interpreter) is invisible. Say so explicitly.
_MISSING_HINT = """LAYA is not installed in the interpreter running this process:
  {python}

The console still started because its other dependencies are present, so this
only shows up when the toggle is switched to LAYA.

Run the console through uv, which uses the project's own .venv:
  uv run python -m webui

Or install the dependencies into that interpreter:
  "{python}" -m pip install -r requirements.txt"""


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
        try:
            from laya import Router
        except ImportError as exc:
            raise RuntimeError(_MISSING_HINT.format(python=sys.executable)) from exc

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
