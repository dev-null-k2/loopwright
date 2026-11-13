"""LLM-AS-JUDGE: a model grades an answer against a written rubric.

Use it only for what code cannot check (is the explanation clear? does it
name a real defence?). Three rules keep a judge honest:

  - a narrow, pass-or-fail question, never a 1-10 score
  - reasoning first, verdict last, so the verdict follows from the reasoning
  - calibrate against human labels before trusting the number

There is no offline judge. A rule-based "judge" would only be measuring
whether the scripted model matches the rules written next to it. With no real
model connected, the judge suite reports itself as skipped.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

JUDGE_PROMPT = """You are grading an AI assistant's answer. Be strict and literal.

Question: {ask}

Answer to grade:
{answer}

Rubric (every clause must hold for a pass):
{rubric}

First reason briefly about each clause. Then reply with only this JSON on the last line:
{{"reasoning": "<two sentences>", "pass": true or false}}"""

DATASET = Path(__file__).parent / "datasets" / "judge.jsonl"


def judge_answer(ask: str, answer: str, rubric: str, model: Callable[[str], str]) -> dict[str, Any]:
    text = model(JUDGE_PROMPT.format(ask=ask, answer=answer, rubric=rubric))
    try:
        verdict = json.loads(text[text.rindex("{"): text.rindex("}") + 1])
        return {"pass": bool(verdict.get("pass")), "reasoning": str(verdict.get("reasoning", ""))}
    except (ValueError, json.JSONDecodeError):
        return {"pass": False, "reasoning": "the judge did not return a verdict", "invalid": True}


def run_judge(run_turn: Callable[[str], str], model: Callable[[str], str] | None) -> dict[str, Any]:
    """Run each judge case through the agent, then through the judge."""
    cases = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    if model is None:
        return {"skipped": True, "cases": len(cases),
                "why": "the judge needs a real model; connect Claude Code or an API key and run `loopwright eval --judge`"}
    rows = []
    for case in cases:
        answer = run_turn(case["ask"])
        rows.append({"id": case["id"], "ask": case["ask"], "answer": answer[:1200],
                     **judge_answer(case["ask"], answer, case["rubric"], model)})
    return {"skipped": False, "cases": len(rows), "passed": sum(r["pass"] for r in rows),
            "pass_rate": round(sum(r["pass"] for r in rows) / len(rows), 3), "rows": rows}
