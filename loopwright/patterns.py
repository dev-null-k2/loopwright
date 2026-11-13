"""ORCHESTRATION PATTERNS: shapes you can put around the basic loop.

The plain loop (ReAct) decides one step at a time. Two common additions are
implemented here as optional stages, switchable per turn (`Options.pattern`):

  plan     PLAN-AND-EXECUTE. One call writes the steps before any tool runs.
           The plan goes into the prompt and the loop follows it. Helps on
           multi-step work, makes the agent's intent reviewable up front, and
           costs one extra call. A plan can also be wrong and anchor the loop.

  reflect  EVALUATOR-OPTIMIZER. After the loop drafts an answer, a critic
           checks it against a rubric. If it fails, the feedback is appended
           and the loop gets one more pass. Catches a class of errors the
           author misses, at the price of latency. One revision is the cap: a
           critic that can always ask for more never terminates.

The third pattern, orchestrator-workers, is the `delegate` tool
(tools/delegate.py). Routing shows up twice: the retrieval gate routes a turn
to memory or not, and the model gateway routes side jobs to a small model.
"""

from __future__ import annotations

import json
import re
from typing import Any

from loopwright.events import Recorder
from loopwright.guardrails import CITATION
from loopwright.models.base import Message
from loopwright.models.gateway import ModelGateway

PLAN_NOTE = ("\n\nBefore acting, write a short numbered plan (2-5 steps) for the user's latest request. "
             "Name the tool each step would use. Do not call tools and do not answer yet.")

CRITIC_PROMPT = """You are reviewing an assistant's draft answer before it is sent.

User request: {request}

Draft answer: {draft}

Check: (1) it answers what was asked; (2) it claims nothing the draft does not support;
(3) a comparison ends with a one-line bottom line; (4) it is not padded.

Reply with only this JSON: {{"pass": true or false, "feedback": "<one sentence on what to fix, or empty>"}}"""


def make_plan(gateway: ModelGateway, system: str, messages: list[Message], rec: Recorder) -> str:
    response = gateway.call(system + PLAN_NOTE, messages, [], purpose="plan", src="context")
    plan = response.text.strip()
    rec.emit("plan.made", "planner", f"Plan: {len([x for x in plan.splitlines() if x.strip()])} step(s)",
             "Plan-and-execute. The steps are written down before any tool runs, then added to the prompt. "
             "You can read what the agent intends before it does anything.",
             src="model", data={"plan": plan})
    return plan


def critique(request: str, draft: str, cited: set[str], gateway: ModelGateway, rec: Recorder) -> tuple[bool, str]:
    """-> (passed, feedback). Rules offline; one model call with a real model."""
    small = gateway.small("critic")
    checks: list[dict[str, Any]] = []
    if small is None:
        passed, feedback = _rule_critic(request, draft, cited, checks)
        how = "rules"
    else:
        try:
            text = small(CRITIC_PROMPT.format(request=request, draft=draft))
            verdict = json.loads(text[text.index("{"): text.rindex("}") + 1])
            passed, feedback, how = bool(verdict.get("pass")), str(verdict.get("feedback", "")), "model"
        except Exception as exc:     # a broken critic must not block the answer
            passed, feedback, how = True, "", f"skipped ({type(exc).__name__})"
    rec.emit("critic.review", "critic", "Critic: draft passes" if passed else "Critic: revise",
             "Evaluator-optimizer. A second pass judges the draft against a rubric before the user sees it. "
             + ("Nothing to fix." if passed else f"Feedback sent back to the loop: {feedback}"),
             src="model", status="ok" if passed else "warn",
             data={"passed": passed, "feedback": feedback, "how": how, "checks": checks, "draft": draft[:800]})
    return passed, feedback


def _rule_critic(request: str, draft: str, cited: set[str], checks: list[dict[str, Any]]) -> tuple[bool, str]:
    def check(name: str, ok: bool, fix: str) -> str:
        checks.append({"check": name, "pass": ok})
        return "" if ok else fix
    problems = [
        check("not empty", bool(draft.strip()), "The answer is empty; answer the request."),
        check("citations are grounded", all(c.strip().lower() in cited for c in CITATION.findall(draft)),
              "Remove or fix citations to passages that were not retrieved."),
        check("comparison has a bottom line",
              not re.search(r"\b(compare|versus|vs\.?|difference between)\b", request, re.I) or "in short" in draft.lower(),
              "Add a one-line bottom line starting with 'In short:'."),
        check("not padded", len(draft) < 2400, "Cut it down; it is too long."),
    ]
    problems = [p for p in problems if p]
    return not problems, " ".join(problems)
