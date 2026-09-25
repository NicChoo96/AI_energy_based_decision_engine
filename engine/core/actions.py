"""Actions and triggers — what a rule mesh does when it reaches a leaf.

The engine never asks *what* an action means. It looks the name up in a table
and calls it. Unknown names produce a visibly failed result rather than a
crash, so a typo in a ``.rule`` file shows up in the UI instead of a traceback.

Add your own by defining a function and decorating it with :func:`action`::

    from engine.core.actions import action

    @action
    def custom_payout(ctx, name, args):
        return ActionResult(action=name, args=args, detail="paid out")
"""

from __future__ import annotations

import importlib.util
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from engine.core.state import StateLedger

ActionFn = Callable[["ActionContext", str, tuple[str, ...]], "ActionResult"]


@dataclass(frozen=True)
class ActionContext:
    """Everything an action is allowed to know."""

    flow: str
    run_id: str
    root: Path
    ledger: StateLedger
    live: bool = False

    @property
    def state(self) -> str:
        return self.ledger.text


@dataclass(frozen=True)
class ActionResult:
    action: str
    args: tuple[str, ...] = ()
    status: str = "ok"
    detail: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "action": self.action,
            "args": list(self.args),
            "status": self.status,
            "detail": self.detail,
            "payload": dict(self.payload),
        }


REGISTRY: dict[str, ActionFn] = {}


def action(fn: ActionFn) -> ActionFn:
    """Register ``fn`` under its own name."""
    REGISTRY[fn.__name__] = fn
    return fn


def perform(name: str, ctx: ActionContext, args: tuple[str, ...]) -> ActionResult:
    """Run a registered action, or return a failed result for unknown names."""
    return REGISTRY.get(name, _unknown)(ctx, name, tuple(args))


def _pad(args: tuple[str, ...], size: int) -> list[str]:
    return (list(args) + [""] * size)[:size]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# --------------------------------------------------------------------------- #
# built-in actions
# --------------------------------------------------------------------------- #
@action
def emit(ctx: ActionContext, name: str, args: tuple[str, ...]) -> ActionResult:
    """Write a line of text into the trace."""
    return ActionResult(action=name, args=args, detail=" ".join(args) or "(empty emit)")


@action
def trigger(ctx: ActionContext, name: str, args: tuple[str, ...]) -> ActionResult:
    """Fire an integration event: ``! trigger | <channel> | <target> | <message>``."""
    channel, target, message = _pad(args, 3)
    return ActionResult(
        action=name,
        args=args,
        status="fired",
        detail=f"{channel or 'event'} -> {target or 'unspecified'} :: {message or '(no payload)'}",
        payload={
            "channel": channel,
            "target": target,
            "message": message,
            "simulated": not ctx.live,
        },
    )


@action
def annotate(ctx: ActionContext, name: str, args: tuple[str, ...]) -> ActionResult:
    """Push an extra sentence into the state so later layers can feel it.

    Effects fire *before* the layer asks its question, which makes this the
    mid-flow equivalent of the seed text.
    """
    fact = " ".join(args)
    ctx.ledger.note(f"Effect '{name}': {fact}")
    return ActionResult(action=name, args=args, detail=f"state += {fact}")


@action
def record(ctx: ActionContext, name: str, args: tuple[str, ...]) -> ActionResult:
    """Append an audit row to ``runs/<flow>.jsonl``."""
    path = Path(ctx.root) / "runs" / f"{_slug(ctx.flow)}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "run": ctx.run_id,
        "flow": ctx.flow,
        "action": name,
        "args": list(args),
        "state": ctx.state,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
    return ActionResult(
        action=name, args=args, detail=f"appended to {path.name}", payload={"path": str(path)}
    )


def _simulate_http(ctx: ActionContext, name: str, verb: str, url: str, body: str) -> ActionResult:
    return ActionResult(
        action=name,
        args=(verb, url, body),
        status="simulated",
        detail=f"{verb} {url or '(no url)'} — not sent (enable live mode)",
        payload={"method": verb, "url": url, "body": body, "simulated": True},
    )


def _real_http(ctx: ActionContext, name: str, verb: str, url: str, body: str) -> ActionResult:
    import requests

    response = _VERBS[verb](requests, url, body)
    return ActionResult(
        action=name,
        args=(verb, url, body),
        status=str(response.status_code),
        detail=f"{verb} {url} -> {response.status_code}",
        payload={"method": verb, "url": url, "body": body, "response": response.text[:2000]},
    )


@action
def http(ctx: ActionContext, name: str, args: tuple[str, ...]) -> ActionResult:
    """Call a real webhook: ``! http | POST | <url> | <body>``."""
    verb, url, body = _pad(args, 3)
    return _TRANSPORT[bool(ctx.live)](ctx, name, verb.upper() or "GET", url, body)


_VERBS: dict[str, Callable] = {
    "GET": lambda r, url, body: r.get(url, timeout=30),
    "POST": lambda r, url, body: r.post(url, data=body, timeout=30),
    "PUT": lambda r, url, body: r.put(url, data=body, timeout=30),
    "PATCH": lambda r, url, body: r.patch(url, data=body, timeout=30),
    "DELETE": lambda r, url, body: r.delete(url, timeout=30),
}

_TRANSPORT: dict[bool, Callable] = {True: _real_http, False: _simulate_http}


def _unknown(ctx: ActionContext, name: str, args: tuple[str, ...]) -> ActionResult:
    return ActionResult(
        action=name,
        args=args,
        status="unknown-action",
        detail=f"no action named '{name}' is registered — known: {', '.join(sorted(REGISTRY))}",
    )


def _no_plugin(path: Path, exc: BaseException) -> str:
    """No ``plugins.py`` at all is the normal case, not a problem."""
    return ""


def _broken_plugin(path: Path, exc: BaseException) -> str:
    return f"{path.name} did not import: {type(exc).__name__}: {exc}"


#: How to report a failed plugin import, keyed on whether the file was missing.
_PLUGIN_STATUS: dict[bool, Callable[[Path, BaseException], str]] = {
    True: _no_plugin,
    False: _broken_plugin,
}


def load_plugins(root: Path | str) -> str:
    """Import ``<root>/plugins.py`` so projects can register their own actions.

    Returns a one-line status, or the empty string when the project simply has
    no plugin file — which is the common case and not worth a warning.
    """
    path = Path(root) / "plugins.py"
    try:
        spec = importlib.util.spec_from_file_location("loop_branches_plugins", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - plugin code is untrusted by design
        return _PLUGIN_STATUS[isinstance(exc, FileNotFoundError)](path, exc)
    return f"loaded {path.name} ({len(REGISTRY)} actions registered)"
