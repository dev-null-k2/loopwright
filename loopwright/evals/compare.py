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
"""

from __future__ import annotations

import json
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
                on_progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    cases = load_cases("live")
    columns = []
    for target in targets:
        rows, unreachable = [], False
        for case in cases:
            if unreachable:
                rows.append({"id": case["id"], "status": "skipped", "passed": False, "note": "the provider could not be reached"})
                continue
            overrides = {"provider": target["provider"], "fast": target["provider"] == "scripted", "fallback": False,
                         **({"model": target["model"]} if target["model"] else {}), **target["options"]}
            started = time.perf_counter()
            result = run_case(case, overrides, (out_dir / "traces" / target["slug"]) if out_dir else None)
            trial = result["trials"][0]
            unreachable = UNREACHABLE in trial["reply"]
            row = {"id": case["id"], "status": "unreachable" if unreachable else "pass" if result["passed"] else "fail",
                   "passed": result["passed"], "ms": int((time.perf_counter() - started) * 1000),
                   "tokens": trial["tokens_in"] + trial["tokens_out"], "cost_usd": trial["cost_usd"],
                   "tools": trial["tools"], "steps": trial["steps"], "model": trial["model"],
                   "reply": " ".join(trial["reply"].split())[:400],
                   "failed_checks": [f"{c['check']}: {c['note']}"[:200] for c in trial["checks"] if not c["pass"]]}
            rows.append(row)
            if on_progress:
                on_progress({"target": target["label"], "case": case["id"], "status": row["status"],
                             "done": len(rows), "total": len(cases)})
        ran = [r for r in rows if r["status"] in ("pass", "fail")]
        costs = [r["cost_usd"] for r in ran]
        columns.append({**target, "rows": rows, "passed": sum(r["passed"] for r in rows), "total": len(cases),
                        "reachable": not unreachable or bool(ran),
                        "median_ms": int(statistics.median(r["ms"] for r in ran)) if ran else None,
                        "tokens": sum(r["tokens"] for r in ran),
                        "cost_usd": None if not ran or any(c is None for c in costs) else round(sum(costs), 4),
                        "tool_calls": sum(len(r["tools"]) for r in ran),
                        "model": next((r["model"] for r in ran if r.get("model")), target["model"])})
    report = {"generated": time.strftime("%Y-%m-%d %H:%M"), "targets": columns,
              "cases": [{"id": c["id"], "title": c["title"],
                         "prompt": c["turns"][-1] if isinstance(c["turns"][-1], str) else c["turns"][-1]["say"]} for c in cases]}
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "compare.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


def format_table(report: dict[str, Any]) -> str:
    targets = report["targets"]
    width = max(22, *(len(t["label"]) + 2 for t in targets))
    lines = ["case".ljust(10) + "".join(t["label"].ljust(width) for t in targets)]
    for i, case in enumerate(report["cases"]):
        cells = []
        for t in targets:
            r = t["rows"][i]
            cells.append((f"{r['status']}  {r['ms'] / 1000:.1f}s  {r['tokens']:,} tok" if "ms" in r else r["status"]).ljust(width))
        lines.append(case["id"].ljust(10) + "".join(cells))
    lines.append("")
    lines.append("passed".ljust(10) + "".join(f"{t['passed']}/{t['total']}".ljust(width) for t in targets))
    lines.append("median".ljust(10) + "".join((f"{t['median_ms'] / 1000:.1f}s" if t["median_ms"] is not None else "n/a").ljust(width) for t in targets))
    lines.append("tokens".ljust(10) + "".join(f"{t['tokens']:,}".ljust(width) for t in targets))
    lines.append("cost".ljust(10) + "".join(("n/a" if t["cost_usd"] is None else f"${t['cost_usd']:.4f}").ljust(width) for t in targets))
    return "\n".join(lines)
