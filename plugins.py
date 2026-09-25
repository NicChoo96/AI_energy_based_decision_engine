"""Project-local actions for the rule meshes in ``rules/``.

Any ``! name | arg | arg`` option in a ``.rule`` file resolves against the action
registry. The built-ins (``emit``, ``trigger``, ``annotate``, ``record``,
``http``) live in :mod:`engine.core.actions`; this module adds the ones that only
make sense for these particular flows, so the rule files stay readable and the
wiring stays in Python.

``engine.core.actions.load_plugins`` imports this file on every run. It is
optional — delete it and the meshes still work, as long as your rule files do not
name any of the actions below.

``@action`` registers a function under its own name, so the function name *is*
the name you write in a rule file. The call signature is always
``(ctx, name, args)`` where ``args`` is the tuple of ``|``-separated fields from
the rule line::

    @action
    def slack_ping(ctx, name, args):
        channel, message = _pad(args, 2)
"""

from __future__ import annotations

from engine.core.actions import ActionContext, ActionResult, _pad, action

#: Escalation words that mean "wake someone up now".
URGENT = frozenset({"sev1", "sev2", "critical", "high"})


@action
def slack_ping(ctx: ActionContext, name: str, args: tuple[str, ...]) -> ActionResult:
    """Post to a chat channel: ``! slack_ping | <channel> | <message>``."""
    channel, message = _pad(args, 2)
    return ActionResult(
        action=name,
        args=args,
        status="fired",
        detail=f"#{channel or 'general'} :: {message or '(no message)'}",
        payload={"channel": channel, "message": message, "simulated": not ctx.live},
    )


@action
def page(ctx: ActionContext, name: str, args: tuple[str, ...]) -> ActionResult:
    """Wake a human: ``! page | <team> | <severity>``.

    The one action this whole project exists to get right.
    """
    team, severity = _pad(args, 2)
    escalation = "P1" if severity.lower() in URGENT else "P3"
    return ActionResult(
        action=name,
        args=args,
        status="fired",
        detail=f"{escalation} page -> {team or 'oncall'} (severity {severity or 'unstated'})",
        payload={"team": team, "severity": severity, "escalation": escalation},
    )


@action
def freeze_account(ctx: ActionContext, name: str, args: tuple[str, ...]) -> ActionResult:
    """``! freeze_account | <account> | <reason>`` — never really executed."""
    account, reason = _pad(args, 2)
    return ActionResult(
        action=name,
        args=args,
        status="dry-run",
        detail=f"would freeze {account or '(no account)'}: {reason or '(no reason)'}",
        payload={"account": account, "reason": reason, "executed": False},
    )
