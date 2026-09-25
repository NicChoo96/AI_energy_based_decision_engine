"""Shared fixtures. See ``tests/stub_backend.py`` for the scripted backend, and
``tests/test_purity.py`` for the assertion that the engine cannot branch."""

from __future__ import annotations

import pytest
from tests.stub_backend import StubBackend


@pytest.fixture
def stub() -> StubBackend:
    """A backend that returns the first option on every menu."""
    return StubBackend()


@pytest.fixture
def rule():
    from engine.rules.parser import parse
    from tests.stub_backend import SAMPLE_RULE

    return parse(SAMPLE_RULE, source="<stub>")
