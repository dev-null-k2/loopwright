"""MODEL COMPARISON: the same questions, through different models, side by side.

    loopwright compare --targets scripted local claude-code
    loopwright compare --targets local:qwen2.5:7b local:llama3.2:3b

"Which model should we use?" is the question a forward-deployed engineer is
asked most, and the honest answer is never a leaderboard: it is "run your own
tasks through each candidate and look". This does that in miniature.

Each target runs the eight behaviour cases from `datasets/live.jsonl` (the
same ones `loopwright selftest` uses) in fresh, isolated state. The checks are
on behaviour: did the calculator run, was the fact stored, was the injected
message NOT sent. A model that words its answers differently still passes.

For every cell you get pass or fail, time, tokens, cost and the tools called,
and the full trace, so a failure can be replayed step by step next to the
same question on a model that passed.

What to read off the table: quality (passes), latency, cost, and how much the
harness had to help (retries, repaired tool calls). The cheapest model that
passes YOUR cases is the right default; route the hard cases upward.

A target is `provider`, `provider:model`, or either with `+setting=value`
pairs to compare configurations of one model: `scripted+pattern=plan`.

ONE RUN IS AN ANECDOTE. A real model does not give the same answer twice, so
a single pass or fail says little. `--trials 3` asks every question three
times, each in fresh state, and reports three numbers per model:

    every trial      questions it got right all three times (pass^k): what you
                     can rely on
    any trial        questions it got right at least once (pass@k): what it is
                     capable of
    pass rate        the share of all runs that passed

The gap between the first two is flakiness. A cell that reads 2/3 is the most
useful thing on the page: replay the failing trial next to a passing one.

`--publish` writes the table as Markdown and JSON, with the date, the version
and the machine, so a result can be committed and compared with a later one.
"""

from __future__ import annotations

import json
import os
import platform
import re
import statistics
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loopwright.evals.runner import load_cases, run_case
from loopwright.models.gateway import PROVIDERS, pick_provider

UNREACHABLE = "could not get an answer from the model"


def parse_target(text: str) -> dict[str, Any]:
    """'local:qwen2.5:7b+pattern=plan' -> {"label", "provider", "model", "options"}."""
    head, *settings = text.strip().split("+")
    provider, _, model = head.partition(":")
    provider = pick_provider("auto") if provider in ("", "auto") else provider
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider '{provider}'. Choose from: {', '.join(PROVIDERS)}")
    options = dict(pair.split("=", 1) for pair in settings if "=" in pair)
    label = provider + (f" · {model}" if model else "") + "".join(f" · {k}={v}" for k, v in options.items())
    return {"label": label, "provider": provider, "model": model, "options": options,
            "slug": re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "target"}


def run_compare(targets: list[dict[str, Any]], out_dir: Path | None = None,
                on_progress: Callable[[dict[str, Any]], None] | None = None, trials: int = 1) -> dict[str, Any]:
    cases = load_cases("live")
    trials = max(1, min(int(trials), 10))
    described = [{"id": c["id"], "title": c["title"],
                  "prompt": c["turns"][-1] if isinstance(c["turns"][-1], str) else c["turns"][-1]["say"]} for c in cases]
    columns: list[dict[str, Any]] = []

    def report(partial: bool) -> dict[str, Any]:
        out = {"generated": time.strftime("%Y-%m-%d %H:%M"), "trials": trials, "targets": columns, "cases": described,
               **({"partial": True} if partial else {})}
        if out_dir:                 # saved after every question: a long run on a local model keeps what it has finished
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "compare.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
        return out

    for target in targets:
        rows: list[dict[str, Any]] = []
        column: dict[str, Any] = {**target, "rows": rows, "total": len(cases), "trials": trials}
        columns.append(column)
        unreachable = False
        for case in cases:
            if unreachable:
                rows.append({"id": case["id"], "status": "skipped", "passed": False, "passes": 0, "trials": 0,
                             "note": "the provider could not be reached"})
                continue
            overrides = {"provider": target["provider"], "fast": target["provider"] == "scripted", "fallback": False,
                         **({"model": target["model"]} if target["model"] else {}), **target["options"]}
            started = time.perf_counter()
            # each trial is a fresh agent in a fresh folder; after the first unreachable answer there is no point repeating
            result = run_case({**case, "trials": [{}]}, overrides, (out_dir / "traces" / target["slug"]) if out_dir else None)
            runs = list(result["trials"])
            unreachable = UNREACHABLE in runs[0]["reply"]
            if not unreachable and trials > 1:
                runs += run_case({**case, "trials": [{} for _ in range(trials - 1)]}, overrides)["trials"]
            passes = sum(1 for t in runs if t["passed"])
            shown = next((t for t in runs if not t["passed"]), runs[0])      # the failing trial is the one worth reading
            costs = [t["cost_usd"] for t in runs]
            row = {"id": case["id"],
                   "status": "unreachable" if unreachable else "pass" if passes == len(runs) else "flaky" if passes else "fail",
                   "passed": passes == len(runs) and not unreachable, "passes": passes, "trials": len(runs),
                   "ms": int((time.perf_counter() - started) * 1000) if len(runs) == 1 else int(statistics.median(t["duration_ms"] for t in runs)),
                   "tokens": round(statistics.fmean(t["tokens_in"] + t["tokens_out"] for t in runs)),
                   "cost_usd": None if any(c is None for c in costs) else round(sum(costs), 6),
                   "tools": shown["tools"], "steps": shown["steps"], "model": shown["model"],
                   "reply": " ".join(shown["reply"].split())[:400],
                   "failed_checks": [f"{c['check']}: {c['note']}"[:200] for c in shown["checks"] if not c["pass"]]}
            rows.append(row)
            ran = [r for r in rows if r["status"] in ("pass", "flaky", "fail")]
            costs = [r["cost_usd"] for r in ran]
            runs_total = sum(r["trials"] for r in ran)
            column.update({
                "passed": sum(r["passed"] for r in rows),                       # right on every trial
                "any_pass": sum(1 for r in ran if r["passes"]),                 # right at least once
                "flaky": sum(1 for r in ran if r["status"] == "flaky"),
                "pass_rate": round(sum(r["passes"] for r in ran) / runs_total, 3) if runs_total else 0.0,
                "reachable": not unreachable or bool(ran),
                "median_ms": int(statistics.median(r["ms"] for r in ran)) if ran else None,
                "tokens": sum(r["tokens"] for r in ran),                        # for one pass over the questions
                "cost_usd": None if not ran or any(c is None for c in costs) else round(sum(costs), 4),
                "tool_calls": sum(len(r["tools"]) for r in ran),
                "model": next((r["model"] for r in ran if r.get("model")), target["model"])})
            if on_progress:
                on_progress({"target": target["label"], "case": case["id"], "status": row["status"],
                             "passes": row["passes"], "trials": row["trials"], "done": len(rows), "total": len(cases)})
            report(partial=True)
        if not rows or all(r["status"] in ("unreachable", "skipped") for r in rows):
            column.update({"passed": 0, "any_pass": 0, "flaky": 0, "pass_rate": 0.0, "reachable": False, "median_ms": None,
                           "tokens": 0, "cost_usd": None, "tool_calls": 0, "model": target["model"]})
    return report(partial=False)


def _cell(row: dict[str, Any], many: bool) -> str:
    if "ms" not in row:
        return row["status"]
    count = f" {row['passes']}/{row['trials']}" if many else ""
    return f"{row['status']}{count}  {row['ms'] / 1000:.1f}s  {row['tokens']:,} tok"


def format_table(report: dict[str, Any]) -> str:
    targets, many = report["targets"], report.get("trials", 1) > 1
    width = max(32 if many else 24, *(len(t["label"]) + 2 for t in targets))
    lines = ["case".ljust(12) + "".join(t["label"].ljust(width) for t in targets)]
    for i, case in enumerate(report["cases"]):
        lines.append(case["id"].ljust(12) + "".join(_cell(t["rows"][i], many).ljust(width) for t in targets if i < len(t["rows"])))
    lines.append("")
    if many:
        lines.append("every trial".ljust(12) + "".join(f"{t['passed']}/{t['total']}".ljust(width) for t in targets))
        lines.append("any trial".ljust(12) + "".join(f"{t['any_pass']}/{t['total']}".ljust(width) for t in targets))
        lines.append("pass rate".ljust(12) + "".join(f"{t['pass_rate'] * 100:.0f}%".ljust(width) for t in targets))
    else:
        lines.append("passed".ljust(12) + "".join(f"{t['passed']}/{t['total']}".ljust(width) for t in targets))
    lines.append("median".ljust(12) + "".join((f"{t['median_ms'] / 1000:.1f}s" if t["median_ms"] is not None else "n/a").ljust(width) for t in targets))
    lines.append("tokens".ljust(12) + "".join(f"{t['tokens']:,}".ljust(width) for t in targets))
    lines.append("cost".ljust(12) + "".join(("n/a" if t["cost_usd"] is None else f"${t['cost_usd']:.4f}").ljust(width) for t in targets))
    return "\n".join(lines)


def machine() -> str:
    """Enough about this computer to make a timing meaningful, and nothing that identifies it."""
    return f"{platform.system()} {platform.machine()}, {os.cpu_count()} cores, Python {platform.python_version()}"


def publish(report: dict[str, Any], folder: Path, version: str, command: str = "") -> Path:
    """Write the comparison as `models.md` (to read) and `models.json` (to diff) in `folder`."""
    folder.mkdir(parents=True, exist_ok=True)
    targets, n = report["targets"], report.get("trials", 1)
    real = [t for t in targets if t["provider"] != "scripted" and t.get("reachable")]
    secs = lambda ms: "n/a" if ms is None else f"{ms} ms" if ms < 1000 else f"{ms / 1000:.1f} s"  # noqa: E731
    lines = ["# Model scores", "",
             f"The eight behaviour questions of `loopwright selftest`, {n} trial{'s' if n != 1 else ''} each, every trial in "
             "fresh state. The checks are on what the agent did (which tool ran, what was stored, what was not sent), "
             "not on how the model worded its answer.", "",
             f"Generated {report['generated']} by Loopwright {version} on {machine()}.", ""]
    if not real:
        lines += ["**No real model is in this table yet.** The scripted row is the rule-based stand-in: it shows the "
                  "harness and the questions work, and says nothing about any language model. Add real rows with the "
                  "command at the bottom.", ""]
    lines += ["| Model | Right on every trial | Right at least once | Pass rate | Median time per question | Tokens per pass | Cost |",
              "|---|---|---|---|---|---|---|"]
    for t in targets:
        name = t["label"] + (f" ({t['model']})" if t.get("model") and t["model"] not in t["label"] else "")
        if not t.get("reachable"):
            lines.append(f"| {name} | could not be reached | | | | | |")
            continue
        cost = "n/a" if t["cost_usd"] is None else "$0" if t["cost_usd"] == 0 else f"${t['cost_usd']:.4f}"
        lines.append(f"| {name}{' *(stand-in, not an LLM)*' if t['provider'] == 'scripted' else ''} | {t['passed']}/{t['total']} | "
                     f"{t['any_pass']}/{t['total']} | {t['pass_rate'] * 100:.0f}% | {secs(t['median_ms'])} | {t['tokens']:,} | {cost} |")
    lines += ["", "## By question", "", "Passes out of trials. A cell that is neither full nor empty is a flaky behaviour.", "",
              "| Question | " + " | ".join(t["label"] for t in targets) + " |", "|---|" + "---|" * len(targets)]
    for i, case in enumerate(report["cases"]):
        cells = [f"{t['rows'][i]['passes']}/{t['rows'][i]['trials']}" if i < len(t["rows"]) and t["rows"][i]["trials"] else "not run" for t in targets]
        lines.append(f"| `{case['id']}` {case['title']} | " + " | ".join(cells) + " |")
    lines += ["", "## How to read it", "",
              "- **Right on every trial** is what you can rely on. **Right at least once** is what the model can do. "
              "The gap between them is flakiness, and it is the number a single run hides.",
              "- Time is per question, median over trials, on the machine named above. A local model's time is mostly "
              "that machine; a hosted model's is mostly the network and the provider.",
              "- Eight questions are a smoke test of agent behaviour, not a benchmark. Two models that both score 8/8 "
              "here can still differ a great deal on your own tasks: save those as cases and compare on them.",
              "", "## Run it yourself", "", "```bash",
              command or "loopwright compare --targets scripted local claude-code --trials 3 --publish results", "```", ""]
    (folder / "models.md").write_text("\n".join(lines), encoding="utf-8")
    slim = {**report, "version": version, "machine": machine(),
            "targets": [{k: v for k, v in t.items() if k != "rows"} | {"rows": [{k: r.get(k) for k in ("id", "status", "passes", "trials", "ms", "tokens", "cost_usd", "failed_checks")}
                                                                                 for r in t["rows"]]} for t in targets]}
    (folder / "models.json").write_text(json.dumps(slim, indent=1), encoding="utf-8")
    return folder / "models.md"
