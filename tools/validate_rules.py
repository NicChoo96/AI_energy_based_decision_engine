"""Parse every rule file in ``rules/`` and report what the mesh looks like.

Run it after editing a ``.rule`` file — it catches typos, dangling ``->`` links,
unreachable nodes and unknown signal types before you spend a model call.

    python -m tools.validate_rules
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

from engine.config import ROOT
from engine.rules import RuleSyntaxError, load

RULES_DIR = ROOT / "rules"
REPORT_WIDTH = 78
UNREACHABLE = 1 << 30


def _banner(text: str) -> None:
    print()
    print("-" * REPORT_WIDTH)
    print(text)
    print("-" * REPORT_WIDTH)


def _label(path: Path) -> str:
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def _walk(ruleset) -> tuple[list[str], dict[str, int]]:
    """Breadth-first pass returning the visited order and each node's layer depth."""
    seen: list[str] = []
    depth: dict[str, int] = {ruleset.start: 1}
    queue: deque[str] = deque([ruleset.start])
    while queue:
        key = queue.popleft()
        seen.append(key)
        node = ruleset.nodes[key]
        for target in node.links:
            candidate = depth[key] + 1
            if candidate < depth.get(target, UNREACHABLE):
                depth[target] = candidate
                queue.append(target)
    return seen, depth


def _route_count(ruleset) -> int:
    """Distinct start-to-terminal routes: every action landing counts as one leaf."""
    memo: dict[str, int] = {}
    visiting: set[str] = set()

    def walk(key: str) -> int:
        if key in memo:
            return memo[key]
        node = ruleset.nodes[key]
        cycle = key in visiting
        visiting.add(key)
        onward = sum(walk(link) for link in node.links) if not cycle else 1
        visiting.discard(key)
        outcomes = len(node.actions) + onward
        memo[key] = outcomes or 1
        return memo[key]

    return walk(ruleset.start)


def inspect(path: Path) -> int:
    _banner(_label(path))
    try:
        ruleset = load(path)
    except RuleSyntaxError as exc:
        print(f"  PARSE ERROR   {exc}")
        return 1

    seen, depth = _walk(ruleset)
    reached = set(seen)
    orphans = sorted(set(ruleset.nodes) - reached)
    layers = max(depth.values(), default=0)
    signals = sum(len(node.signals) for node in ruleset.nodes.values())
    calls = sum(len(node.actions) for node in ruleset.nodes.values())

    print(f"  title           {ruleset.title}")
    print(f"  suggested       {ruleset.suggested_backend or '(none)'}")
    print(f"  start           {ruleset.start}")
    print(f"  nodes           {len(ruleset.nodes)} ({len(reached)} reachable)")
    print(f"  layers deep     {layers}  -> {layers} model calls per run")
    print(f"  distinct paths  {_route_count(ruleset):,}")
    print(f"  signal asks     {signals}")
    print(f"  terminal calls  {calls}")
    print(f"  examples        {len(ruleset.examples)}")

    print()
    print("  shape")
    buckets: dict[int, list[str]] = {}
    for key, level in depth.items():
        buckets.setdefault(level, []).append(key)
    for level in sorted(buckets):
        print(f"    L{level}   {', '.join(sorted(buckets[level]))}")

    print()
    print("  endings")
    for key in sorted(reached):
        node = ruleset.nodes[key]
        landings = " ".join(f"!{opt.target}({' '.join(opt.args)})" for opt in node.actions)
        kind = "effect only" if not node.options else "routes onward"
        print(f"    {key:<24} {kind:<14} {landings or '-'}")

    if orphans:
        print()
        print("  UNREACHABLE — nothing routes here")
        for key in orphans:
            print(f"    {key}")
        return 1
    return 0


def main(argv: list[str]) -> int:
    requested = [Path(arg) for arg in argv[1:]]
    targets = requested or sorted(RULES_DIR.glob("*.rule"))
    for path in targets:
        if not path.exists():
            print(f"missing: {path}")
    present = [path for path in targets if path.exists()]
    failures = sum(inspect(path) for path in present)
    print()
    print(f"checked {len(present)} rule file(s); {failures} with problems")
    return min(failures, 1)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
