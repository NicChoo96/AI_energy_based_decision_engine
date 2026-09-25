"""HTTP surface for the console.

A run is a thread pushing ``Engine`` events into a queue; the browser reads them
back over Server-Sent Events. That keeps the engine synchronous and dependency
free while still giving the page a live feed.
"""

from __future__ import annotations

import json
import queue
import threading
import uuid
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from engine.backends import JEV, KINDS, LAYA, create_backend
from engine.config import ROOT, load_env
from engine.core import Engine
from engine.core.actions import load_plugins
from engine.rules import RuleSyntaxError, load as load_rule, parse

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
RULES_DIR = ROOT / "rules"

app = FastAPI(title="Rule mesh console", docs_url="/api/docs")


# --------------------------------------------------------------------------- #
# run bookkeeping
# --------------------------------------------------------------------------- #
class RunHandle:
    """One execution, its event backlog, and the thread producing it."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.events: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.thread: threading.Thread | None = None
        self.status = "starting"
        self.summary: dict[str, Any] = {}
        self.finished = False

    def push(self, event: dict[str, Any]) -> None:
        self.events.put(event)

    def close(self) -> None:
        self.status = "done"
        self.finished = True
        self.events.put(None)

    def stream(self) -> Iterator[str]:
        """Yield SSE frames until the run ends. A late viewer gets the backlog.

        Once the run has been drained the sentinel is gone, so a reconnect just
        closes immediately instead of blocking forever on an empty queue.
        """
        if self.finished and self.events.empty():
            yield f"event: closed\ndata: {json.dumps(self.summary)}\n\n"
            return
        while True:
            event = self.events.get()
            if event is None:
                yield f"event: closed\ndata: {json.dumps(self.summary)}\n\n"
                return
            yield f"event: {event.get('kind', 'message')}\ndata: {json.dumps(event)}\n\n"


RUNS: dict[str, RunHandle] = {}


# --------------------------------------------------------------------------- #
# rule files
# --------------------------------------------------------------------------- #
class RuleSource(BaseModel):
    text: str


class RunRequest(BaseModel):
    rule: str = "refund_triage"
    backend: str = ""  # empty means "whatever the rule suggests"
    seed: str = ""
    live: bool = False
    max_layers: int = Field(default=80, ge=1, le=500)


def _rule_path(stem: str) -> Path:
    """Resolve a bare stem or an explicit path, refusing anything outside rules/."""
    candidate = Path(stem)
    path = candidate if candidate.is_file() else RULES_DIR / f"{candidate.stem}.rule"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"no rule file named '{stem}'")
    if RULES_DIR.resolve() not in path.resolve().parents:
        raise HTTPException(status_code=403, detail="rule files must live in rules/")
    return path


def _describe(path: Path) -> dict[str, Any]:
    try:
        ruleset = load_rule(path)
    except RuleSyntaxError as exc:
        return {"stem": path.stem, "broken": True, "error": str(exc)}
    endings = sum(len(node.actions) for node in ruleset.nodes.values())
    questions = sum(len(node.questions) for node in ruleset.nodes.values())
    return {
        "stem": path.stem,
        "broken": False,
        "title": ruleset.title,
        "about": ruleset.about,
        "backend": ruleset.suggested_backend or JEV,
        "start": ruleset.start,
        "layers": len(ruleset.nodes),
        "endings": endings,
        "questions": questions,
        "examples": list(ruleset.examples),
        "size": path.stat().st_size,
    }


@app.get("/api/backends")
def backends() -> dict[str, Any]:
    return {
        "kinds": list(KINDS),
        "labels": {
            JEV: "JEV — remote energy model (OpenRouter)",
            LAYA: "LAYA — local energy model (runs on this machine)",
        },
    }


@app.get("/api/rules")
def rules() -> dict[str, Any]:
    return {"rules": [_describe(path) for path in sorted(RULES_DIR.glob("*.rule"))]}


@app.get("/api/rules/{stem}")
def read_rule(stem: str) -> dict[str, Any]:
    path = _rule_path(stem)
    return {"stem": path.stem, "text": path.read_text(encoding="utf-8")}


@app.put("/api/rules/{stem}")
def write_rule(stem: str, body: RuleSource) -> dict[str, Any]:
    """Save a rule file. It is only written if it parses — no half-broken meshes."""
    path = _rule_path(stem)
    try:
        parse(body.text, source=path.stem)
    except RuleSyntaxError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    path.write_text(body.text, encoding="utf-8")
    return _describe(path)


@app.post("/api/rules/{stem}/check")
def check_rule(stem: str, body: RuleSource) -> dict[str, Any]:
    """Parse without saving, so the editor can show problems as you type."""
    try:
        ruleset = parse(body.text, source=stem)
    except RuleSyntaxError as exc:
        return {"ok": False, "error": str(exc)}
    endings = sum(len(node.actions) for node in ruleset.nodes.values())
    return {
        "ok": True,
        "title": ruleset.title,
        "layers": len(ruleset.nodes),
        "endings": endings,
        "examples": list(ruleset.examples),
    }


# --------------------------------------------------------------------------- #
# running
# --------------------------------------------------------------------------- #
def _drive(handle: RunHandle, path: Path, request: RunRequest) -> None:
    """Body of a run thread. Never raises: failures arrive as an ``error`` event."""
    try:
        load_env()
        plugins = load_plugins(ROOT)
        ruleset = load_rule(path)
        kind = (request.backend or ruleset.suggested_backend or JEV).strip().lower()
        if kind not in KINDS:
            raise ValueError(f"unknown backend '{kind}'; expected one of {', '.join(KINDS)}")
        backend = create_backend(kind)
        engine = Engine(
            ruleset=ruleset,
            backend=backend,
            root=ROOT,
            live=request.live,
            max_layers=request.max_layers,
            on_event=handle.push,
        )
        if plugins:
            handle.push({"kind": "plugins", "detail": plugins})
        trace = engine.run(request.seed)
        handle.summary = trace.to_json()
        handle.status = "closed"
    except Exception as exc:  # noqa: BLE001 - surfaced to the browser verbatim
        handle.push({"kind": "error", "message": f"{type(exc).__name__}: {exc}"})
        handle.summary = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        handle.close()


@app.post("/api/run")
def start_run(request: RunRequest) -> dict[str, Any]:
    path = _rule_path(request.rule)
    run_id = uuid.uuid4().hex[:12]
    handle = RunHandle(run_id)
    RUNS[run_id] = handle
    thread = threading.Thread(
        target=_drive, args=(handle, path, request), name=f"run-{run_id}", daemon=True
    )
    thread.start()
    return {"run": run_id, "rule": path.stem, "backend": request.backend or "suggested"}


@app.get("/api/runs/{run_id}")
def run_events(run_id: str) -> StreamingResponse:
    handle = RUNS.get(run_id)
    if handle is None:
        raise HTTPException(status_code=404, detail=f"no run '{run_id}'")
    return StreamingResponse(
        handle.stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/runs")
def list_runs() -> dict[str, Any]:
    return {"runs": [{"run": key, "status": value.status} for key, value in RUNS.items()]}


@app.delete("/api/runs/{run_id}")
def forget_run(run_id: str) -> dict[str, str]:
    RUNS.pop(run_id, None)
    return {"forgotten": run_id}


# Mounted last so it never shadows the /api routes above.
app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
