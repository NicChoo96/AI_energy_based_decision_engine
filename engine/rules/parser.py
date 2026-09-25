"""A tiny line-oriented language for describing decision meshes in pure text.

Every layer is one question. The model answers it; the answer picks the branch.
Nothing else decides anything.

    # Comments start with '#'. Blank lines are ignored.
    @rule    Refund triage
    @about   Where a duplicate-charge complaint should go.
    @start   classify
    @example Customer was charged twice for order #102 and threatens to cancel.

    @node classify
    @ask  Which capability should own this request?
    @option billing   | invoices, refunds, duplicate charges | -> billing_play
    @option technical | bugs, outages, broken behaviour     | -> technical_play
    @option risk      | fraud, abuse, policy violations     | ! trigger | fraud-desk | P1

    @node billing_play
    @signal urgency | score | How urgent is the money problem? | can wait | this week | blocking revenue
    @signal churn   | noul  | Is the customer threatening to leave?
    @effect emit | billing layer entered
    @ask  How should billing handle this?
    @option auto_refund | small duplicate charge and nobody is angry | ! trigger | payments-api | refund
    @option escalate    | urgent, or the customer is threatening to churn | ! trigger | pager | billing-lead

    @node final
    @end
    @effect emit | flow finished

Directives
----------
``@rule`` / ``@about`` / ``@example`` / ``@start`` / ``@node`` / ``@ask`` /
``@note`` / ``@signal`` / ``@effect`` / ``@option`` / ``@end`` / ``@suggest``.

``@signal <name> | <choice|score|noul> | <question> | <criteria...>``
    A non-branching question. Its answer is folded into the state text so that
    later layers can feel it, but it never picks a branch by itself.

``@option <key> | <semantics> | -> <node-key>``
    The model choosing ``key`` continues the flow at ``node-key``.

``@option <key> | <semantics> | ! <action> | <arg> ...``
    The model choosing ``key`` fires ``action`` and ends the flow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from engine.backends.base import (
    CHOICE,
    NOUL,
    NOUL_KEYS,
    SCORE,
    Question,
    normalise_noul_key,
)
from engine.rules.model import (
    LAND_ACTION,
    LAND_GOTO,
    NODE_ROUTE,
    NODE_TERMINAL,
    Effect,
    Node,
    Option,
    RuleSet,
)


class RuleSyntaxError(ValueError):
    """The rule text could not be understood."""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _split(text: str, maxsplit: int = -1) -> list[str]:
    return [part.strip() for part in text.split("|", maxsplit)]


def _pair(part: str) -> tuple[str, str]:
    head, _, tail = part.partition("=")
    return head.strip(), tail.strip()


def _directive_and_rest(raw: str) -> tuple[str, str]:
    tokens = raw.strip().split(None, 1) or [""]
    directive, *tail = tokens
    return directive, (tail + [""])[0]


# --------------------------------------------------------------------------- #
# draft state
# --------------------------------------------------------------------------- #
@dataclass
class _Draft:
    key: str
    ask: str = ""
    kind: str = NODE_ROUTE
    note: str = ""
    signals: list[Question] = field(default_factory=list)
    options: list[Option] = field(default_factory=list)
    effects: list[Effect] = field(default_factory=list)


@dataclass
class _Ctx:
    title: str = "Untitled flow"
    about: str = ""
    start: str = ""
    suggest: str = ""
    nodes: dict[str, _Draft] = field(default_factory=dict)
    examples: list[str] = field(default_factory=list)
    current: _Draft = field(default_factory=lambda: _Draft(key=""))


# --------------------------------------------------------------------------- #
# directive handlers — one per line prefix, dispatched through a table
# --------------------------------------------------------------------------- #
def _noop(ctx: _Ctx, rest: str) -> None:
    return None


def _rule(ctx: _Ctx, rest: str) -> None:
    ctx.title = rest.strip() or ctx.title


def _about(ctx: _Ctx, rest: str) -> None:
    ctx.about = rest.strip()


def _suggest(ctx: _Ctx, rest: str) -> None:
    ctx.suggest = rest.strip().lower()


def _example(ctx: _Ctx, rest: str) -> None:
    ctx.examples.append(rest.strip())


def _start(ctx: _Ctx, rest: str) -> None:
    ctx.start = rest.strip()


def _node(ctx: _Ctx, rest: str) -> None:
    key, _, note = rest.partition("|")
    draft = _Draft(key=key.strip(), note=note.strip())
    ctx.nodes[draft.key] = draft
    ctx.current = draft


def _ask(ctx: _Ctx, rest: str) -> None:
    ctx.current.ask = rest.strip()


def _note(ctx: _Ctx, rest: str) -> None:
    ctx.current.note = rest.strip()


def _end(ctx: _Ctx, rest: str) -> None:
    ctx.current.kind = NODE_TERMINAL


def _effect(ctx: _Ctx, rest: str) -> None:
    action, *args = _split(rest)
    ctx.current.effects.append(Effect(action=action, args=tuple(args)))


def _signal(ctx: _Ctx, rest: str) -> None:
    name, qtype, instructions, *criteria = _split(rest)
    kind = qtype.strip().lower()
    ctx.current.signals.append(
        Question(
            name=name,
            qtype=kind,
            instructions=instructions,
            criteria=_AS_CRITERIA.get(kind, _bad_criteria)(criteria),
        )
    )


def _option(ctx: _Ctx, rest: str) -> None:
    # Pad to exactly three fields so a missing destination reaches _bad_target
    # (a RuleSyntaxError) instead of blowing up as an unpacking ValueError.
    key, semantics, target = (_split(rest, 2) + ["", ""])[:3]
    marker, _, remainder = target.partition(" ")
    ctx.current.options.append(_TARGETS.get(marker, _bad_target)(key, semantics, remainder))


def _option_goto(key: str, semantics: str, remainder: str) -> Option:
    return Option(key=key, semantics=semantics, kind=LAND_GOTO, target=remainder.strip())


def _option_action(key: str, semantics: str, remainder: str) -> Option:
    action, *args = _split(remainder)
    return Option(
        key=key, semantics=semantics, kind=LAND_ACTION, target=action, args=tuple(args)
    )


# --------------------------------------------------------------------------- #
# lookup tables that replace every conditional the parser would otherwise need
# --------------------------------------------------------------------------- #
def _bad_criteria(_: list[str]) -> dict:
    raise RuleSyntaxError("unknown @signal type — use choice, score or noul")


def _raise_noul_keys(keys: list[str]) -> None:
    raise RuleSyntaxError(
        "a noul signal takes 'yes = ... | no = ...' (or 'true = ... | false = ...'); "
        f"these keys are not polarities: {', '.join(keys)}"
    )


def _accept_noul_keys(keys: list[str]) -> None:
    return None


_NOUL_KEY_GUARD: dict[bool, Callable[[list[str]], None]] = {
    True: _accept_noul_keys,
    False: _raise_noul_keys,
}


def _bad_target(key: str, semantics: str, remainder: str) -> Option:
    raise RuleSyntaxError(
        f"@option '{key}' needs a destination: '-> <node-key>' or '! <action>'"
    )


def _noul_criteria(parts: list[str]) -> dict[str, str]:
    """Canonicalise a noul signal onto the ``true``/``false`` keys the models want.

    Rule files may write ``yes = ... | no = ...``; the wire format accepts only
    ``true``/``false``, so the spelling is fixed up here and anything else is
    reported against the offending key.
    """
    given = dict(map(_pair, parts))
    canonical = {normalise_noul_key(key): value for key, value in given.items()}
    _NOUL_KEY_GUARD[set(canonical) <= set(NOUL_KEYS)](sorted(given))
    return canonical


_AS_CRITERIA: dict[str, Callable[[list[str]], object]] = {
    CHOICE: lambda parts: dict(map(_pair, parts)),
    NOUL: _noul_criteria,
    SCORE: lambda parts: [part.strip() for part in parts],
}

_TARGETS: dict[str, Callable[[str, str, str], Option]] = {
    "->": _option_goto,
    "!": _option_action,
}

_HANDLERS: dict[str, Callable[[_Ctx, str], None]] = {
    "@rule": _rule,
    "@about": _about,
    "@suggest": _suggest,
    "@example": _example,
    "@start": _start,
    "@node": _node,
    "@ask": _ask,
    "@note": _note,
    "@end": _end,
    "@effect": _effect,
    "@signal": _signal,
    "@option": _option,
}

#: Directives that only make sense inside a layer. Keyed so the check is a dict
#: lookup rather than a membership test at the call site.
_NEEDS_NODE: dict[str, bool] = {
    "@ask": True,
    "@note": True,
    "@end": True,
    "@effect": True,
    "@signal": True,
    "@option": True,
}


# --------------------------------------------------------------------------- #
# assembly + validation
# --------------------------------------------------------------------------- #
def _build_node(draft: _Draft) -> Node:
    return Node(
        key=draft.key,
        ask=draft.ask,
        kind=draft.kind,
        signals=tuple(draft.signals),
        options=tuple(draft.options),
        effects=tuple(draft.effects),
        note=draft.note,
    )


def unresolved_links(ruleset: RuleSet) -> list[str]:
    """Keys referenced by ``->`` (plus ``@start``) that no ``@node`` defines."""
    buckets: dict[str, set[str]] = {LAND_GOTO: set(), LAND_ACTION: set()}
    for node in ruleset.nodes.values():
        for option in node.options:
            buckets[option.kind].add(option.target)
    known = set(ruleset.nodes)
    return sorted((buckets[LAND_GOTO] | {ruleset.start}) - known)


def _raise_orphans(ctx: _Ctx) -> None:
    raise RuleSyntaxError(
        "found @ask/@option/@signal before any @node — every directive must belong to a layer"
    )


def _raise_no_nodes(ctx: _Ctx) -> None:
    raise RuleSyntaxError("no @node was defined — a rule file needs at least one layer")


def _ignore_orphans(ctx: _Ctx) -> None:
    return None


def _raise_dangling(ruleset: RuleSet) -> None:
    raise RuleSyntaxError(
        "these layers are referenced but never defined: " + ", ".join(unresolved_links(ruleset))
    )


def _ignore_dangling(ruleset: RuleSet) -> None:
    return None


_ORPHAN_GUARD: dict[bool, Callable[[_Ctx], None]] = {
    True: _raise_orphans,
    False: _ignore_orphans,
}

_EMPTY_GUARD: dict[bool, Callable[[RuleSet], None]] = {
    True: _raise_no_nodes,
    False: _ignore_dangling,
}

_DANGLING_GUARD: dict[bool, Callable[[RuleSet], None]] = {
    True: _raise_dangling,
    False: _ignore_dangling,
}


def _guard_orphan(ctx: _Ctx, directive: str) -> None:
    """A layer-scoped directive seen while no ``@node`` is open is an error."""
    _ORPHAN_GUARD[_NEEDS_NODE.get(directive, False) and not ctx.current.key](ctx)


def _build_rule(ctx: _Ctx, source: str) -> RuleSet:
    nodes = {key: _build_node(draft) for key, draft in ctx.nodes.items()}
    _EMPTY_GUARD[not nodes](ctx)
    ruleset = RuleSet(
        title=ctx.title,
        start=ctx.start or next(iter(nodes)),
        nodes=nodes,
        source=source,
        about=ctx.about,
        examples=tuple(ctx.examples),
        suggested_backend=ctx.suggest,
    )
    _DANGLING_GUARD[bool(unresolved_links(ruleset))](ruleset)
    return ruleset


def parse(text: str, source: str = "<text>") -> RuleSet:
    """Parse rule text into an executable :class:`~engine.rules.model.RuleSet`."""
    ctx = _Ctx()
    for raw in text.splitlines():
        directive, rest = _directive_and_rest(raw)
        _guard_orphan(ctx, directive)
        _HANDLERS.get(directive, _noop)(ctx, rest)
    return _build_rule(ctx, source)


def load(path) -> RuleSet:
    """Parse a ``.rule`` file from disk."""
    from pathlib import Path

    source = Path(path)
    return parse(source.read_text(encoding="utf-8"), source=str(source))
