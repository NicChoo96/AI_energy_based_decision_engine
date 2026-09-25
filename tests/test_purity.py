"""The enforcement of the project's one hard rule.

The requirement, stated by the author of this project:

    I want all the if else and switch branches to SOLEly only depend on our
    JEV/LAYA classifier AI model to pick, I do not want a single IF ELSE. The
    only exception im giving is only choosing between JEV OR LAYA.

Documenting that is worthless. This file proves it, by reading the engine's own
source as a syntax tree and failing the moment a branch appears.

WHAT IS BANNED inside ``engine/``
    ast.If        ``if ...:`` / ``elif ...:`` / ``else:``
    ast.IfExp     ``a if cond else b``
    comprehension with ``ifs``   ``[x for x in y if cond]``
    ast.Match     ``match ... case ...``

WHAT IS ALLOWED, and why
    try / except      error routing, not business routing. Nothing is decided.
    while / for       iteration over a budget or a list. No branch is taken.
    dict lookup       the replacement for every branch the engine used to need.
    exactly ONE ast.If, in ``engine/backends/factory.py``, choosing JEV or LAYA.

The rule files themselves are checked too, for conditional *operators*. There
are none: a ``.rule`` file cannot express a comparison even if you wanted it to.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterator

import pytest

from engine.config import ROOT

ENGINE = ROOT / "engine"
RULES_DIR = ROOT / "rules"

#: The single place in the engine permitted to branch, and only on backend kind.
ALLOWED_IF_FILE = Path("backends") / "factory.py"
ALLOWED_IF_COUNT = 1

BANNED = (ast.If, ast.IfExp, ast.Match)

#: Operators that would let a rule file express a condition.
CONDITIONAL_OPERATORS = ("==", "!=", "<=", ">=", "&&", "||")

#: Comprehensions whose ``ifs`` make them a branch in disguise.
COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _source_files() -> list[Path]:
    return sorted(path for path in ENGINE.rglob("*.py") if "__pycache__" not in path.parts)


def _branch_nodes(tree: ast.AST) -> list[ast.AST]:
    nodes: list[ast.AST] = [n for n in ast.walk(tree) if isinstance(n, BANNED)]
    nodes += [
        node
        for node in ast.walk(tree)
        if isinstance(node, COMPREHENSIONS) and any(gen.ifs for gen in node.generators)
    ]
    return nodes


def _describe(node: ast.AST, path: Path) -> str:
    name = type(node).__name__
    line = getattr(node, "lineno", "?")
    if isinstance(node, COMPREHENSIONS):
        name = "comprehension-filter"
    return f"{path.relative_to(ROOT)}:{line}  {name}"


def _code_lines(text: str) -> Iterator[tuple[int, str]]:
    """Yield ``(lineno, line)`` for lines that are not comments.

    A rule file's banner comments are made of ``=`` characters, which would
    otherwise trip the operator scan and hide a real offender.
    """
    return (
        (number, line)
        for number, line in enumerate(text.splitlines(), start=1)
        if not line.lstrip().startswith("#")
    )


def test_engine_directory_has_source() -> None:
    files = _source_files()
    assert files, "expected to find python files under engine/"
    assert len(files) >= 8, f"only found {len(files)} engine files — is the scan working?"


def test_no_branches_anywhere_in_the_engine() -> None:
    """The headline assertion. Any branch outside the factory fails the build."""
    offenders: list[str] = []
    for path in _source_files():
        relative = path.relative_to(ENGINE)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found = _branch_nodes(tree)
        if relative == ALLOWED_IF_FILE:
            found = found[ALLOWED_IF_COUNT:]
        offenders += [_describe(node, path) for node in found]

    assert not offenders, (
        "the engine is supposed to contain no branching at all outside "
        f"{ALLOWED_IF_FILE}. Found:\n  " + "\n  ".join(offenders)
    )


def test_the_only_branch_is_the_backend_choice() -> None:
    """The exception is allowed, but it must stay exactly one if, and about backends."""
    path = ENGINE / ALLOWED_IF_FILE
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    branches = [n for n in ast.walk(tree) if isinstance(n, BANNED)]
    comprehensions = [
        c for c in ast.walk(tree) if isinstance(c, ast.comprehension) and c.ifs
    ]

    assert len(branches) == ALLOWED_IF_COUNT, (
        f"{ALLOWED_IF_FILE} should hold exactly {ALLOWED_IF_COUNT} if-statement, "
        f"found {len(branches)}"
    )
    assert not comprehensions, f"{ALLOWED_IF_FILE} should not filter a comprehension"

    test = branches[0].test
    compared = {n.id for n in ast.walk(test) if isinstance(n, ast.Name)}
    assert compared, "the factory branch compares nothing — what is it doing?"
    assert compared <= {"kind", "LAYA"}, (
        f"the factory branch compares {sorted(compared)}, which is not a backend choice"
    )


def test_every_backend_module_other_than_the_factory_is_pure() -> None:
    backends = sorted(path for path in (ENGINE / "backends").glob("*.py"))
    assert backends, "no backend modules found"
    impure = [
        _describe(node, path)
        for path in backends
        if path.name != ALLOWED_IF_FILE.name
        for node in _branch_nodes(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert not impure, "backends other than the factory must not branch:\n  " + "\n  ".join(impure)


@pytest.mark.parametrize("path", sorted(RULES_DIR.glob("*.rule")), ids=lambda p: p.stem)
def test_rule_files_cannot_express_a_condition(path: Path) -> None:
    """A rule file is declarative prose plus menus. No comparison operators."""
    text = path.read_text(encoding="utf-8")
    offenders = [
        f"line {number}: {line.strip()}"
        for number, line in _code_lines(text)
        for token in CONDITIONAL_OPERATORS
        if token in line
    ]
    assert not offenders, (
        f"{path.name} contains a conditional operator — rule files must stay declarative:\n  "
        + "\n  ".join(offenders)
    )


def test_rule_files_have_no_dangling_links() -> None:
    """Every ``->`` target and every ``@start`` must name a real ``@node``."""
    from engine.rules.parser import load, unresolved_links

    broken = {
        path.name: unresolved_links(load(path)) for path in sorted(RULES_DIR.glob("*.rule"))
    }
    dangling = {name: keys for name, keys in broken.items() if keys}
    assert not dangling, f"rule files reference layers that do not exist: {dangling}"
