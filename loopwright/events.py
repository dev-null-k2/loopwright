"""EVENTS: the one idea the whole project hangs on.

Every step an agent takes (a guardrail scan, a memory lookup, a model call,
a tool call, a retry) is emitted here as one typed Event. Nothing else in the
codebase prints, logs or draws. That single choice buys three things:

  1. Observability  - the trace of a turn is just its events, written as JSONL.
  2. The cockpit    - the diagram lights up the `stage` each event names.
  3. Replay         - a turn can be played back later, at any speed, because
                      the events are the complete record of what happened.

An event says where it happened (`stage`, a box on the architecture diagram),
where the data came from (`src`, so the arrow between boxes can animate),
and what it means in plain English (`detail`).
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Every box on the architecture diagram: id -> (label, pillar, file that implements it).
# The web UI, the docs generator and the tests all read this one table, so the
# diagram can never name a box the code does not have.
STAGES: dict[str, tuple[str, str, str]] = {
    "user":        ("You", "gateway", "loopwright/cli.py"),
    "gateway":     ("Gateway", "gateway", "loopwright/server.py"),
    "guard_in":    ("Input guardrails", "safety", "loopwright/guardrails.py"),
    "working":     ("Working memory", "memory", "loopwright/memory/store.py"),
    "procedural":  ("Skills (procedural)", "memory", "loopwright/memory/skills.py"),
    "gate":        ("Retrieval gate", "memory", "loopwright/memory/gate.py"),
    "semantic":    ("Semantic memory", "memory", "loopwright/memory/retrieval.py"),
    "episodic":    ("Episodic memory", "memory", "loopwright/memory/store.py"),
    "kb":          ("Knowledge base (RAG)", "memory", "loopwright/memory/kb.py"),
    "context":     ("Context builder", "harness", "loopwright/context.py"),
    "planner":     ("Planner", "loop", "loopwright/patterns.py"),
    "model":       ("Model gateway", "loop", "loopwright/models/gateway.py"),
    "toolgate":    ("Tool gate", "harness", "loopwright/toolgate.py"),
    "human":       ("Human approval", "safety", "loopwright/toolgate.py"),
    "tools":       ("Tools", "loop", "loopwright/tools/builtin.py"),
    "guard_tool":  ("Tool-result guard", "safety", "loopwright/guardrails.py"),
    "subagent":    ("Subagents", "loop", "loopwright/tools/delegate.py"),
    "critic":      ("Critic", "loop", "loopwright/patterns.py"),
    "guard_out":   ("Output guardrails", "safety", "loopwright/guardrails.py"),
    "reply":       ("Reply", "gateway", "loopwright/harness.py"),
    "consolidate": ("Consolidation", "memory", "loopwright/memory/consolidation.py"),
    "checkpoint":  ("Checkpoints", "harness", "loopwright/harness.py"),
    "trace":       ("Trace", "ops", "loopwright/events.py"),
    "cost":        ("Cost receipt", "ops", "loopwright/receipt.py"),
    "evals":       ("Evals", "ops", "loopwright/evals/runner.py"),
    "release":     ("Release gate", "ops", "loopwright/evals/gate.py"),
}

STATUSES = ("ok", "skip", "warn", "blocked", "error")


@dataclass
class Event:
    seq: int                 # position in the turn, starting at 1
    t_ms: int                # milliseconds since the turn began
    kind: str                # machine name, e.g. "memory.gate" (see docs/events.md)
    stage: str               # the diagram box this happened in (a key of STAGES)
    src: str                 # the box the data came from, so the arrow can animate
    title: str               # one short line for the timeline
    detail: str = ""         # plain-English account of what happened and why
    status: str = "ok"       # ok | skip | warn | blocked | error
    concept: str = ""        # glossary card id, when the kind alone is ambiguous
    span: str = "main"       # "main", or "sub:<n>" for a subagent's own steps
    dur_ms: int | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


Sink = Callable[[Event], None]


class Recorder:
    """Collects one turn's events and fans each one out to its sinks
    (the trace file, the browser's event stream, the CLI printer)."""

    def __init__(self, turn_id: str | None = None, sinks: list[Sink] | None = None,
                 span: str = "main", parent: Recorder | None = None):
        self.turn_id = turn_id or new_id()
        self.sinks: list[Sink] = list(sinks or [])
        self.span = span
        self._parent = parent
        # a subagent shares its parent's list, clock and lock, so its steps
        # land in the same ordered trace
        self.events: list[Event] = parent.events if parent else []
        self._t0 = parent._t0 if parent else time.perf_counter()
        self._lock = parent._lock if parent else threading.Lock()
        self._last_stage = "user"

    def child(self, span: str) -> Recorder:
        """A recorder for a subagent: same trace, its own span label."""
        return Recorder(self.turn_id, self.sinks, span=span, parent=self)

    def emit(self, kind: str, stage: str, title: str, detail: str = "", *,
             src: str | None = None, status: str = "ok", concept: str = "",
             dur_ms: int | None = None, data: dict[str, Any] | None = None) -> Event:
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage!r}: add it to events.STAGES")
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        with self._lock:
            event = Event(
                seq=len(self.events) + 1,
                t_ms=int((time.perf_counter() - self._t0) * 1000),
                kind=kind, stage=stage, src=src or self._last_stage, title=title,
                detail=detail, status=status, concept=concept,
                span=self.span, dur_ms=dur_ms, data=_jsonable(data or {}),
            )
            self.events.append(event)
            self._last_stage = stage
        for sink in self.sinks:
            try:
                sink(event)
            except Exception:  # a broken sink must never break the turn
                pass
        return event

    def find(self, kind: str) -> list[Event]:
        return [e for e in self.events if e.kind == kind]


def new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


def _jsonable(value: Any) -> Any:
    """Event data must survive json.dumps; anything exotic becomes its repr."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        if isinstance(value, dict):
            return {str(k): _jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_jsonable(v) for v in value]
        return repr(value)


# ---------------------------------------------------------------- traces

def trace_dir(home: Path) -> Path:
    path = home / "traces"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_trace(home: Path, header: dict[str, Any], events: list[Event],
                footer: dict[str, Any]) -> Path:
    """One JSONL file per turn: a header line, one line per event, a footer.
    This file is the whole input to replay; nothing else is needed."""
    path = trace_dir(home) / f"{header['turn_id']}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "turn", **header}, ensure_ascii=False) + "\n")
        for event in events:
            fh.write(json.dumps({"type": "event", **event.to_json()}, ensure_ascii=False) + "\n")
        fh.write(json.dumps({"type": "end", **footer}, ensure_ascii=False) + "\n")
    return path


def read_trace(path: Path | str) -> dict[str, Any]:
    """Load a trace back as {"turn": header, "events": [...], "end": footer}."""
    turn: dict[str, Any] = {}
    end: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            kind = row.pop("type", "event")
            if kind == "turn":
                turn = row
            elif kind == "end":
                end = row
            else:
                events.append(row)
    return {"turn": turn, "events": events, "end": end}


def list_traces(home: Path, limit: int = 50) -> list[dict[str, Any]]:
    """Newest first, with just enough of each trace to show in a list."""
    rows = []
    for path in sorted(trace_dir(home).glob("*.jsonl"), reverse=True)[:limit]:
        try:
            trace = read_trace(path)
        except (OSError, json.JSONDecodeError):
            continue
        turn, end = trace["turn"], trace["end"]
        receipt = end.get("receipt", {})
        rows.append({
            "turn_id": turn.get("turn_id", path.stem),
            "message": turn.get("message", ""),
            "started": turn.get("started", ""),
            "provider": turn.get("provider", ""),
            "model": turn.get("model", ""),
            "steps": len(trace["events"]),
            "tokens": receipt.get("tokens_in", 0) + receipt.get("tokens_out", 0),
            "ok": end.get("ok", True),
        })
    return rows
