"""Runtime configuration.

Deliberately branch-free: the workspace ``.env.local`` file is parsed with a
single regular expression and merged underneath the real process environment,
so an explicitly exported variable always wins.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_LOCAL = ROOT / ".env.local"
RUNS_DIR = ROOT / "runs"

_PAIR = re.compile(r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def load_env(path: Path | str = ENV_LOCAL) -> dict[str, str]:
    """Merge ``path`` into ``os.environ`` without overriding real variables."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        text = ""
    pairs = {key: value.strip("\"'") for key, value in _PAIR.findall(text)}
    for key, value in pairs.items():
        os.environ[key] = os.environ.get(key, value)
    return pairs
