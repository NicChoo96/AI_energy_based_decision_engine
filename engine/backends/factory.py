"""Backend selection.

This module contains the **only** ``if``/``else`` in the entire engine, and it
exists for exactly one reason: the brief permits a single conditional, choosing
between JEV and LAYA. Every business branch lives in a ``.rule`` file and is
resolved by the energy model.

``tests/test_purity.py`` parses the whole package with :mod:`ast` and fails if
any other module grows a conditional, so this guarantee is enforced, not just
documented.
"""

from __future__ import annotations

from engine.backends.base import DecisionBackend
from engine.backends.jev import DEFAULT_MODEL as JEV_DEFAULT_MODEL
from engine.backends.jev import JevBackend
from engine.backends.laya import DEFAULT_MODEL as LAYA_DEFAULT_MODEL
from engine.backends.laya import LayaBackend

JEV = "jev"
LAYA = "laya"
KINDS = (JEV, LAYA)


def create_backend(
    kind: str = JEV,
    *,
    jev_model: str | None = None,
    laya_model: str | None = None,
    api_key: str | None = None,
    api_endpoint: str | None = None,
    device: str | None = None,
    preload: bool = False,
    timeout: float = 240.0,
) -> DecisionBackend:
    """Return the requested energy-based decision backend."""
    if kind == LAYA:
        return LayaBackend(model=laya_model or LAYA_DEFAULT_MODEL, device=device, preload=preload)
    else:
        return JevBackend(
            api_key=api_key,
            model=jev_model or JEV_DEFAULT_MODEL,
            endpoint=api_endpoint or "https://openrouter.ai/api/alpha/decisions",
            timeout=timeout,
        )
