"""TypeSafe **JEV** — joint-energy decisions served over OpenRouter."""

from __future__ import annotations

import os
from typing import Any, Mapping, Sequence

import requests

from engine.backends.base import Decision, Question, normalise

DEFAULT_ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_MODEL = "typesafe/jev-1.13"


class JevBackend:
    """Calls the hosted JEV-1.x decision endpoint.

    JEV scores every candidate answer simultaneously against the accumulated
    state text and returns the lowest-energy (highest-probability) one.
    """

    name = "jev"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout: float = 240.0,
        session: Any = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        self.model = model
        self.endpoint = endpoint
        self.timeout = timeout
        self._session = session or requests.Session()
        self._headers = {
            "Authorization": f"Bearer {api_key or os.environ['OPENROUTER_API_KEY']}",
            "Content-Type": "application/json",
            **(extra_headers or {}),
        }

    def decide(self, state: str, questions: Sequence[Question]) -> Decision:
        payload = {
            "model": self.model,
            "state": state,
            "questions": {q.name: q.to_payload() for q in questions},
        }
        response = self._session.post(
            self.endpoint, headers=self._headers, json=payload, timeout=self.timeout
        )
        response.raise_for_status()
        return normalise(response.json(), questions)
