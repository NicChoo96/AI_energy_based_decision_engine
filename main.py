"""Convenience shim so ``python main.py`` works as well as ``python -m engine``.

This file lives outside ``engine/`` on purpose. The one ``if`` a Python program
always needs — the ``__main__`` guard — is not decision logic, but keeping it
out here means the purity scan over ``engine/`` can still demand zero branches.
"""

from __future__ import annotations

from engine.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
