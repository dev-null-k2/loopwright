"""Record the static demo that GitHub Pages serves from docs/.

    python scripts/build_demo.py

Runs real turns on the scripted model (frozen clock, offline search), saves
their traces plus a memory snapshot and an eval report into docs/demo/data.json,
and copies the cockpit next to it. The page at docs/index.html is the same
cockpit as `loopwright serve`; with no server to talk to, it replays these
recordings instead. Nothing in the demo is mocked up by hand.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import tempfile
from pathlib import Path

from loopwright import __version__
from loopwright.config import PACKAGE_DIR, Options
from loopwright.evals.compare import parse_target, run_compare
from loopwright.evals.runner import run_all
from loopwright.events import read_trace
from loopwright.harness import Agent
from loopwright.learn import load_concepts, load_lessons
from loopwright.server import App, handbook
from loopwright.tools.browser import find_chrome

ROOT = PACKAGE_DIR.parent
DOCS = ROOT / "docs"
NOW = dt.datetime(2026, 11, 12, 18, 0)
BASE = {"provider": "scripted", "fast": True, "search": "demo", "approvals": "approve"}
MAIN_PROMPTS = [
    "Remember that Alex prefers morning meetings",
    "Book a catch-up with Alex on Friday",
    "What's 18% of 2450, and save that as a note",
    "What is a harness?",
    "Explain prompt injection",
    "Compare BM25 and embeddings",
    "Search the web for agent evaluation best practices",
    "Summarise demo://poisoned-page",
    "Send a message to Priya: running 10 minutes late",
    "When I ask for a standup update, always give three bullets: done, doing, blocked",
    "What do you know about Alex?",
    "Read the file ../../etc/passwd",
    "/workflow morning-brief",
    "/workflow triage: what is a harness?",
    "/workflow research-digest: agent evaluation",
    "Every weekday at 8am, tell me what's on my calendar",
    "/task 1",
]
BROWSER_PROMPTS = [
    "Use the browser: open demo://site, follow the Pricing link and tell me what the Team plan costs",
    "In the browser, open demo://site and search for offline search",
]
COMPARE = ["scripted", "scripted+pattern=plan", "scripted+guardrails=false"]


def record(agent: Agent, prompt: str, session: str, options: dict, **tags) -> dict:
    result = agent.run_turn(prompt, session=session, options=Options.from_dict({**BASE, **options}))
    trace = read_trace(result.trace_path)
    trace["turn"]["started"] = NOW.isoformat(timespec="seconds")
    receipt = trace["end"]["receipt"]
    return {**trace, **tags, "summary": {
        "turn_id": result.turn_id, "message": prompt, "started": trace["turn"]["started"], "provider": "scripted",
        "model": receipt["model"], "steps": len(trace["events"]), "tokens": receipt["tokens_in"] + receipt["tokens_out"], "ok": result.ok}}


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="loopwright-demo-"))
    app = App(work / "main")
    app.agent.now = lambda: NOW
    prompts = MAIN_PROMPTS + (BROWSER_PROMPTS if find_chrome() else [])
    runs = [record(app.agent, prompt, "demo", {}, main=True, lesson=None) for prompt in prompts]

    for lesson in load_lessons():
        if not lesson.get("prompt") or (lesson.get("needs") == "browser" and not find_chrome()):
            continue
        home = work / f"lesson-{lesson['id']}"
        home.mkdir()
        (home / "settings.json").write_text(json.dumps({"enabled_tools": lesson.get("enable", [])}))
        agent = Agent(home=home, now=lambda: NOW)
        for prompt in lesson.get("before", []):
            runs.append(record(agent, prompt, "a", {}, main=False, lesson=lesson["id"]))
        session = "b" if lesson.get("new_session") else "a"
        runs.append(record(agent, lesson["prompt"], session, lesson.get("options", {}), main=False, lesson=lesson["id"]))

    report = run_all(work / "evals")
    eval_traces = {p.stem: read_trace(p) for p in sorted((work / "evals" / "traces").glob("*.jsonl"))}
    for trace in eval_traces.values():          # the eval traces are for the board, so drop the bulky prompt copies
        for event in trace["events"]:
            event["data"].pop("prompt", None)

    compare = run_compare([parse_target(t) for t in COMPARE], work / "compare")
    compare_traces = {}
    for path in sorted((work / "compare" / "traces").glob("*/*.jsonl")):
        trace = read_trace(path)
        for event in trace["events"]:
            event["data"].pop("prompt", None)
        compare_traces[f"{path.parent.name}/{path.stem}"] = trace

    shots = DOCS / "demo" / "shots"                # what the browser showed at each step
    shutil.rmtree(shots, ignore_errors=True)
    shots.mkdir(parents=True)
    for image in work.glob("*/traces/shots/*.jpg"):
        shutil.copyfile(image, shots / image.name)

    files = sorted({c["file"] for c in load_concepts()})
    data = {
        "health": {"version": __version__, "static": True, "default_provider": "scripted", "options": Options().to_dict(),
                   "providers": {"scripted": {"ok": True, "note": "recorded runs"}}, "home": "~/.loopwright", "counts": {},
                   "browser": True},
        "handbook": handbook(), "runs": runs, "memory": {**app.memory(), "db": "~/.loopwright/state.db"},
        "overview": app.overview(), "tools": {**app.tools(), "tools_folder": "~/.loopwright/tools"},
        "evals": report, "evalTraces": eval_traces, "compare": compare, "compareTraces": compare_traces,
        "automations": {**app.automations(), "workflows_folder": "~/.loopwright/workflows"},
        "sources": {f: (ROOT / f).read_text(encoding="utf-8") for f in files if (ROOT / f).suffix in (".py", ".js", ".jsonl")},
    }
    (DOCS / "demo").mkdir(parents=True, exist_ok=True)
    out = DOCS / "demo" / "data.json"
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
    text = re.sub(re.escape(str(work)) + r"/[\w\-]+", "~/.loopwright", text)        # no machine-specific paths in the demo
    out.write_text(text, encoding="utf-8")
    for name in ("index.html", "app.css"):
        shutil.copyfile(PACKAGE_DIR / "web" / name, DOCS / name)
    for folder in ("js", "fonts"):
        shutil.rmtree(DOCS / folder, ignore_errors=True)
        shutil.copytree(PACKAGE_DIR / "web" / folder, DOCS / folder)
    (DOCS / ".nojekyll").write_text("")
    shutil.rmtree(work, ignore_errors=True)
    print(f"{len(runs)} recorded runs, {len(eval_traces)} eval traces, {len(compare_traces)} comparison traces, "
          f"{len(list(shots.glob('*.jpg')))} browser screenshots, {out.stat().st_size / 1e6:.1f} MB -> {out}")


if __name__ == "__main__":
    main()
