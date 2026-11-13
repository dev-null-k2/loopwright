"""THE EVAL RUNNER: fixed cases, a fresh agent for each, graded on the trace.

    loopwright eval            run everything, print a table, write the report
    loopwright eval --gate     same, and exit 1 if the release gate fails

What runs:

  suites      core, memory, safety, reliability, context, automation (workflows,
              scheduled tasks, the shell and the browser). Every case gets a
              brand-new home folder and a frozen clock, so cases cannot leak
              into each other and "Friday" always means the same date.
  trials      reliability cases run three times with failures injected at
              different points. pass@1 is the average; pass^3 is the share
              that passed every time.
  ablations   every suite again with one mechanism switched off. The drop is
              that mechanism's measured contribution.
  retrieval   labelled questions against the knowledge base: recall@1 and @3
              for BM25, dense and hybrid.
  gate        labelled messages against the retrieval gate's rules.
  judge       model-graded cases; skipped unless a real model is connected.

Everything except the judge uses the scripted model, so these are evals of
the HARNESS: deterministic, free, identical in CI and on your laptop. They
say nothing about how well a particular LLM reasons. See docs/evals.md.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loopwright import __version__
from loopwright.config import Options
from loopwright.evals import gate as release_gate
from loopwright.evals.graders import grade
from loopwright.evals.judge import run_judge
from loopwright.harness import Agent
from loopwright.memory import gate as retrieval_gate
from loopwright.memory import kb
from loopwright.models.gateway import reset_breakers

DATASETS = Path(__file__).parent / "datasets"
SUITES = ["core", "memory", "safety", "reliability", "context", "automation"]
FROZEN_NOW = dt.datetime(2026, 11, 12, 18, 0)        # a Thursday

# name -> (option overrides, what switching it off should show)
VARIANTS: dict[str, tuple[dict[str, Any], str]] = {
    "full": ({}, "everything on"),
    "no-memory": ({"memory": False}, "long-term memory off"),
    "always-retrieve": ({"gate": "always"}, "retrieval gate off: search memory on every turn"),
    "no-guardrails": ({"guardrails": False}, "input, tool-result and output guards off"),
    "no-retries": ({"retries": 0, "fallback": False}, "no retries and no fallback model"),
}

FILLER = ("We talked through the quarterly roadmap in detail, covering the billing migration, the notification "
          "vendor change, the on-call rota and the plan for the customer pilot, and agreed to revisit the open "
          "questions next week once the numbers are in.")


def load_cases(suite: str) -> list[dict[str, Any]]:
    """A case marked `"needs": "browser"` is left out on a machine with no Chromium-family browser."""
    from loopwright.tools.browser import find_chrome
    path = DATASETS / f"{suite}.jsonl"
    cases = [{**json.loads(line), "suite": suite} for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [c for c in cases if c.get("needs") != "browser" or find_chrome()]


def _setup(agent: Agent, setup: dict[str, Any]) -> None:
    for fact in setup.get("facts", []):
        agent.store.add_fact(fact["text"], fact.get("subject", "user"), fact.get("attr", ""), "eval-setup")
    for i in range(setup.get("history", 0)):
        agent.store.add_message("default", "user" if i % 2 == 0 else "assistant", f"({i + 1}) {FILLER}")
    for note in setup.get("notes", []):
        agent.store.add_note(note)
    for event in setup.get("events", []):
        agent.store.add_event(event["title"], event["date"], event["time"], event.get("attendees", []))
    for task in setup.get("tasks", []):
        agent.store.add_task(task["prompt"], task["schedule"], FROZEN_NOW.timestamp() - 60, task.get("origin", ""))
    for name, text in setup.get("workflows", {}).items():
        (agent.home / "workflows").mkdir(parents=True, exist_ok=True)
        (agent.home / "workflows" / name).write_text(text, encoding="utf-8")
    if setup.get("enable_tools"):
        (agent.home / "settings.json").write_text(json.dumps({"enabled_tools": setup["enable_tools"]}), encoding="utf-8")
        agent.load_extensions()
    workspace = agent.home / "workspace"
    for name, text in setup.get("files", {}).items():
        workspace.mkdir(parents=True, exist_ok=True)
        if text.startswith("@repeat:"):                     # "@repeat:<chunk>:<count>"
            chunk, _, count = text[len("@repeat:"):].rpartition(":")
            text = chunk * int(count)
        (workspace / name).write_text(text, encoding="utf-8")


def run_case(case: dict[str, Any], overrides: dict[str, Any] | None = None,
             traces_dir: Path | None = None) -> dict[str, Any]:
    """Run one case (all its trials) and grade it."""
    trials = []
    for index, trial in enumerate(case.get("trials") or [{}]):
        home = Path(tempfile.mkdtemp(prefix="loopwright-eval-"))
        reset_breakers()
        try:
            agent = Agent(home=home, now=lambda: FROZEN_NOW)
            _setup(agent, case.get("setup", {}))
            options = Options.from_dict({"provider": "scripted", "fast": True, "approvals": "ask",
                                         **case.get("options", {}), **trial, **(overrides or {})})
            results = []
            for turn in case["turns"]:
                say, session = (turn, "default") if isinstance(turn, str) else (turn["say"], turn.get("session", "default"))
                if say == "@scheduler":         # let the clock start whatever is due, with nobody watching
                    from loopwright.scheduler import Scheduler
                    results.extend(Scheduler(agent, options).tick(FROZEN_NOW))
                    continue
                results.append(agent.run_turn(say, session=session, options=options))
            checks = grade(results, agent, case["expect"])
            trials.append({
                "passed": all(c["pass"] for c in checks), "checks": checks, "trial": trial,
                "reply": results[-1].reply, "steps": len(results[-1].events),
                "tokens_in": sum(r.receipt["tokens_in"] for r in results), "turns": len(results),
                "tokens_out": sum(r.receipt["tokens_out"] for r in results),
                "duration_ms": sum(r.receipt["duration_ms"] for r in results),
                "cost_usd": None if any(r.receipt["cost_usd"] is None for r in results) else round(sum(r.receipt["cost_usd"] for r in results), 6),
                "tools": [name for r in results for name in r.receipt["tools"]],
                "model": results[-1].receipt["model"], "provider": results[-1].receipt["provider"],
                "memory_searches": sum(len([e for e in r.events if e.kind == "memory.search"]) for r in results),
                "attack_succeeded": bool(case.get("attack")) and len(agent.store.outbox()) > 0,
            })
            if traces_dir is not None and index == 0:
                traces_dir.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(results[-1].trace_path, traces_dir / f"{case['id']}.jsonl")
            agent.store.close()
        finally:
            shutil.rmtree(home, ignore_errors=True)
    return {"id": case["id"], "suite": case["suite"], "title": case["title"], "concept": case.get("concept", ""),
            "prompt": case["turns"][-1] if isinstance(case["turns"][-1], str) else case["turns"][-1]["say"],
            "attack": bool(case.get("attack")), "passed": all(t["passed"] for t in trials),
            "pass_rate": round(sum(t["passed"] for t in trials) / len(trials), 3), "trials": trials}


def run_variant(name: str, traces_dir: Path | None = None,
                on_case: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    overrides, _ = VARIANTS[name]
    cases = []
    for suite in SUITES:
        for case in load_cases(suite):
            result = run_case(case, overrides, traces_dir)
            cases.append(result)
            if on_case:
                on_case(result)
    return {"cases": cases, **summarise(cases)}


def summarise(cases: list[dict[str, Any]]) -> dict[str, Any]:
    suites: dict[str, dict[str, Any]] = {}
    for suite in SUITES:
        rows = [c for c in cases if c["suite"] == suite]
        multi = [c for c in rows if len(c["trials"]) > 1]
        suites[suite] = {
            "cases": len(rows), "passed": sum(c["passed"] for c in rows),
            "pass_at_1": round(sum(c["pass_rate"] for c in rows) / len(rows), 3) if rows else 0.0,
            "pass_all_trials": round(sum(c["passed"] for c in multi) / len(multi), 3) if multi else None,
            "trials": sum(len(c["trials"]) for c in rows),
        }
    trials = [t for c in cases for t in c["trials"]]
    attacks = [t for c in cases if c["attack"] for t in c["trials"]]
    turns = sum(t["turns"] for t in trials) or 1
    return {"suites": suites, "cases_total": len(cases), "cases_passed": sum(c["passed"] for c in cases),
            "attacks": len(attacks), "attacks_succeeded": sum(t["attack_succeeded"] for t in attacks),
            "attack_success_rate": round(sum(t["attack_succeeded"] for t in attacks) / len(attacks), 3) if attacks else 0.0,
            "avg_tokens_in_per_turn": round(sum(t["tokens_in"] for t in trials) / turns),
            "memory_searches": sum(t["memory_searches"] for t in trials), "turns": turns}


def run_retrieval() -> dict[str, Any]:
    """Recall@k of the knowledge base, per ranker, on labelled questions."""
    rows = [json.loads(line) for line in (DATASETS / "retrieval.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    out: dict[str, Any] = {"questions": len(rows), "misses": []}
    for mode in ("bm25", "dense", "hybrid"):
        at1 = at3 = 0
        for row in rows:
            ids = [h.id for h in kb.search(row["q"], k=3, mode=mode)]
            want = f"concept:{row['expect']}"
            at1 += ids[:1] == [want]
            at3 += want in ids
            if mode == "hybrid" and want not in ids:
                out["misses"].append({"q": row["q"], "expected": row["expect"], "got": ids})
        out[mode] = {"recall_at_1": round(at1 / len(rows), 3), "recall_at_3": round(at3 / len(rows), 3)}
    return out


def run_gate() -> dict[str, Any]:
    """Accuracy of the retrieval gate's rules on labelled messages. A false
    skip (memory was needed and not searched) is the costly kind of error."""
    rows = [json.loads(line) for line in (DATASETS / "gate.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    subjects = {"alex", "priya", "sam", "dana"}
    errors = []
    for row in rows:
        decision = retrieval_gate.decide(row["message"], subjects, "rules")
        if decision.retrieve != row["retrieve"]:
            errors.append({"message": row["message"], "expected": row["retrieve"], "got": decision.retrieve,
                           "reason": decision.reason})
    needed = sum(r["retrieve"] for r in rows)
    false_skips = sum(1 for e in errors if e["expected"])
    return {"messages": len(rows), "accuracy": round(1 - len(errors) / len(rows), 3),
            "false_skips": false_skips, "false_skip_rate": round(false_skips / needed, 3) if needed else 0.0,
            "false_retrieves": len(errors) - false_skips, "errors": errors}


def run_all(out_dir: Path, ablations: bool = True, judge_model: Callable[[str], str] | None = None,
            judge_turn: Callable[[str], str] | None = None,
            on_progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    progress = on_progress or (lambda event: None)

    full = run_variant("full", traces_dir=out_dir / "traces",
                       on_case=lambda c: progress({"stage": "full", "case": c["id"], "passed": c["passed"], "title": c["title"]}))
    table: dict[str, Any] = {}
    for name in (VARIANTS if ablations else ["full"]):
        progress({"stage": "ablation", "variant": name})
        result = full if name == "full" else run_variant(name)
        table[name] = {
            "what": VARIANTS[name][1],
            **{suite: result["suites"][suite]["pass_at_1"] for suite in SUITES},
            "cases_passed": result["cases_passed"], "cases_total": result["cases_total"],
            "attack_success_rate": result["attack_success_rate"], "attacks_succeeded": result["attacks_succeeded"],
            "attacks": result["attacks"], "avg_tokens_in_per_turn": result["avg_tokens_in_per_turn"],
            "memory_searches": result["memory_searches"], "turns": result["turns"],
            "failed": [c["id"] for c in result["cases"] if not c["passed"]],
        }
    progress({"stage": "retrieval"})
    report: dict[str, Any] = {
        "generated": dt.datetime.now().isoformat(timespec="seconds"), "version": __version__,
        "model": "scripted-v1 (rule-based; these are evals of the harness, not of an LLM)",
        "suites": full["suites"], "cases_total": full["cases_total"], "cases_passed": full["cases_passed"],
        "attack_success_rate": full["attack_success_rate"], "cases": full["cases"], "ablations": table,
        "retrieval": run_retrieval(), "gate": run_gate(),
        "judge": run_judge(judge_turn or (lambda ask: ""), judge_model),
    }
    report["verdict"] = release_gate.evaluate(report)
    report["seconds"] = round(time.perf_counter() - started, 2)
    (out_dir / "eval-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    progress({"stage": "done", "passed": report["verdict"]["passed"]})
    return report
