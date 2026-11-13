"""YOUR OWN EVAL CASES: turn a real run into a regression test.

The best eval cases are not invented. They are real turns: one that went
wrong (so it never goes wrong the same way again) or one that went right (so
a later change cannot quietly break it). Every observability product has this
button; here it is forty lines.

  feedback   A thumbs up or down, with an optional note, saved next to the
             traces. It is the cheapest human label there is, and the first
             place to look when deciding what to turn into a case.
  a case     Built from the run's own trace: the message, plus assertions on
             what the run DID (which tools ran, whether the gate retrieved,
             that nothing was blocked). Edit ~/.loopwright/evals/mine.jsonl
             to tighten or loosen them; the format is the same as the built-in
             suites (loopwright/evals/datasets/).

`loopwright eval --mine` runs them against whichever model you choose. They
are yours, so they are not part of the project's release gate.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from loopwright.evals.runner import run_case


def cases_path(home: Path) -> Path:
    return Path(home) / "evals" / "mine.jsonl"


def load_mine(home: Path) -> list[dict[str, Any]]:
    path = cases_path(home)
    if not path.is_file():
        return []
    return [{**json.loads(line), "suite": "mine"} for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def case_from_trace(trace: dict[str, Any], note: str = "") -> dict[str, Any]:
    """Assertions a sensible reviewer would write after watching this run."""
    events, turn = trace["events"], trace["turn"]
    ran = [e["data"]["tool"] for e in events if e["kind"] == "tool.run" and e.get("span", "main") == "main"]
    refused = [e["data"]["tool"] for e in events if e["kind"] == "toolgate.refuse"]
    expect: list[dict[str, Any]] = [{"tool_called": name} for name in dict.fromkeys(ran)]
    gate = next((e for e in events if e["kind"] == "memory.gate" and e["data"].get("mode") != "off"), None)
    if gate:
        expect.append({"event": {"kind": "memory.gate", "status": gate["status"]}})
    if refused:
        expect.append({"event": {"kind": "toolgate.refuse"}})
    if not expect:
        expect.append({"event": {"kind": "reply.send", "status": "ok"}})
    return {"id": f"mine-{turn['turn_id'][-8:]}", "title": (note or turn["message"])[:70], "turns": [turn["message"]],
            "expect": expect, "from_turn": turn["turn_id"], "note": note}


def add_case(home: Path, trace: dict[str, Any], note: str = "") -> dict[str, Any]:
    case = case_from_trace(trace, note)
    kept = [c for c in load_mine(home) if c["id"] != case["id"]]
    path = cases_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({k: v for k, v in c.items() if k != "suite"}) + "\n" for c in [*kept, case]), encoding="utf-8")
    return case


def remove_case(home: Path, case_id: str) -> bool:
    cases = load_mine(home)
    kept = [c for c in cases if c["id"] != case_id]
    if len(kept) == len(cases):
        return False
    cases_path(home).write_text("".join(json.dumps({k: v for k, v in c.items() if k != "suite"}) + "\n" for c in kept), encoding="utf-8")
    return True


def run_mine(home: Path, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    results = [run_case(case, overrides) for case in load_mine(home)]
    return {"cases": results, "passed": sum(r["passed"] for r in results), "total": len(results)}


def add_feedback(home: Path, turn_id: str, rating: str, note: str = "") -> dict[str, Any]:
    row = {"turn_id": turn_id, "rating": "up" if rating == "up" else "down", "note": note[:500],
           "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    path = Path(home) / "feedback.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
    return row


def load_feedback(home: Path) -> dict[str, dict[str, Any]]:
    """turn_id -> the latest feedback on it."""
    path = Path(home) / "feedback.jsonl"
    rows: dict[str, dict[str, Any]] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                rows[row["turn_id"]] = row
    return rows
