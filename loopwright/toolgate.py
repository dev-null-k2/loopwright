"""THE TOOL GATE: every tool call the model asks for passes through here.

The model proposes; the gate disposes. Between "the model wants to call
send_message" and the function running, this file answers, in order:

  1. Does the tool exist?                 (models invent tool names)
  2. Do the arguments fit the schema?     (models send malformed arguments)
  3. Is there budget left?                (a loop must not run forever)
  4. Is this the same call again?         (a stuck loop repeats itself)
  5. Is it allowed?                       allow | ask a human | deny
  6. Was it already done?                 (idempotency: never do a write twice)

A "no" at any step is not a crash. It becomes a tool result the model reads
("refused: ..."), so the model can correct itself, try another way, or tell
the user. Refusals are events too, which is how the evals can assert that an
attack was blocked rather than merely not attempted.

The permission rule (step 5):

  read tools      allowed
  write tools     allowed (local, visible, reversible)
  external tools  need a human's approval
  external tools in a turn that has read untrusted content: DENIED outright.
      Private data + untrusted content + a way to send data out is the lethal
      trifecta. The gate removes the third leg, in code, regardless of how
      convincing the injected text was.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from loopwright import guardrails
from loopwright.config import Options
from loopwright.events import Recorder
from loopwright.tools.registry import Registry, ToolContext, ToolError, TransientToolError, to_text, validate

MAX_REPEATS = 2            # identical call allowed this many times per turn
MAX_RESULT_CHARS = 6000

Approver = Callable[[dict[str, Any]], bool]


@dataclass
class TurnState:
    """What the gate needs to remember across one turn's tool calls."""
    tainted: bool = False                      # untrusted content has entered the context
    taint_sources: list[str] = field(default_factory=list)
    private_read: bool = False                 # private data has entered the context
    calls: int = 0
    seen: Counter = field(default_factory=Counter)
    chaos_left: int = 0
    log: list[dict[str, Any]] = field(default_factory=list)   # every call, for evals and the receipt


@dataclass
class ToolOutcome:
    content: str
    is_error: bool = False
    blocked: bool = False


class ToolGate:
    def __init__(self, registry: Registry, ctx: ToolContext, options: Options, rec: Recorder,
                 state: TurnState, approver: Approver | None = None):
        self.registry, self.ctx, self.options, self.rec, self.state = registry, ctx, options, rec, state
        self.approver = approver

    # ---------------------------------------------------------------- public
    def execute(self, call_id: str, name: str, args: dict[str, Any]) -> ToolOutcome:
        outcome = self._execute(call_id, name, args)
        self.state.log.append({"name": name, "args": args, "ok": not outcome.is_error,
                               "blocked": outcome.blocked, "output": outcome.content[:400]})
        return outcome

    # ---------------------------------------------------------------- the six questions
    def _execute(self, call_id: str, name: str, args: dict[str, Any]) -> ToolOutcome:
        tool = self.registry.get(name)
        checks: list[dict[str, Any]] = []

        def check(label: str, ok: bool, note: str = "") -> bool:
            checks.append({"check": label, "pass": ok, "note": note})
            return ok

        def refuse(title: str, why: str, reason: str) -> ToolOutcome:
            self.rec.emit("toolgate.refuse", "toolgate", title, why, src="model", status="blocked",
                          data={"tool": name, "args": args, "checks": checks, "reason": reason})
            return ToolOutcome(json.dumps({"error": f"refused: {reason}", "blocked": True}), True, True)

        if not check("tool exists", tool is not None, "" if tool else f"known tools: {', '.join(self.registry.names())}"):
            return refuse(f"Refused {name}: no such tool",
                          "The model asked for a tool that does not exist. It is told which tools do, so it can correct itself.",
                          f"no tool named {name!r}. Available: {', '.join(self.registry.names())}")
        errors = validate(tool.parameters, args)
        if not check("arguments fit the schema", not errors, "; ".join(errors)):
            return refuse(f"Refused {name}: bad arguments",
                          "The arguments did not match the tool's JSON Schema. The exact errors go back to the model.",
                          "invalid arguments: " + "; ".join(errors))
        self.state.calls += 1
        if not check("tool budget left", self.state.calls <= self.options.max_tool_calls,
                     f"{self.state.calls}/{self.options.max_tool_calls}"):
            return refuse(f"Refused {name}: tool budget spent",
                          "The turn has used all its tool calls. A budget is what stops a confused loop from running up a bill.",
                          f"tool budget of {self.options.max_tool_calls} calls is spent; answer with what you have")
        fingerprint = name + json.dumps(args, sort_keys=True)
        self.state.seen[fingerprint] += 1
        if not check("not a repeat", tool.repeatable or self.state.seen[fingerprint] <= MAX_REPEATS,
                     f"seen {self.state.seen[fingerprint]} time(s)"):
            return refuse(f"Refused {name}: same call repeated",
                          "The identical call was already made twice this turn. Repeating it is the signature of a stuck loop.",
                          "this exact call was already made twice; do something different")

        decision, why = self._permission(tool)
        check("permission", decision != "deny", f"{decision}: {why}")
        if decision == "deny":
            return refuse(f"Blocked {name}: {why}",
                          "The permission policy denied this call in code. No prompt, however persuasive, can argue with it.",
                          why)
        self.rec.emit("toolgate.check", "toolgate", f"Tool gate: {name} {'needs approval' if decision == 'ask' else 'allowed'}",
                      f"Checked {len(checks)} things before running `{name}` ({tool.effect} tool). {why}.",
                      src="model", data={"tool": name, "args": args, "effect": tool.effect, "decision": decision,
                                         "checks": checks})
        if decision == "ask" and not self._approved(call_id, tool.name, args, why):
            return ToolOutcome(json.dumps({"error": "the user declined this action", "blocked": True}), True, True)

        return self._run(tool, call_id, args)

    def _permission(self, tool) -> tuple[str, str]:
        if tool.effect != "external":
            return "allow", f"{tool.effect} tools run without asking"
        if self.options.guardrails and self.state.tainted:
            return "deny", ("this turn read untrusted content (" + ", ".join(self.state.taint_sources) +
                            "), so tools with outside effects are locked for the rest of it")
        if not self.options.guardrails:
            return "allow", "guardrails are switched off, so nothing is checked"
        if self.options.approvals == "deny":
            return "deny", "the approval policy is set to deny outside effects"
        return "ask", "tools with effects outside the conversation need a human's approval"

    def _approved(self, call_id: str, name: str, args: dict[str, Any], why: str) -> bool:
        request = {"call_id": call_id, "tool": name, "args": args, "why": why}
        self.rec.emit("human.request", "human", f"Waiting for approval: {name}",
                      "Human in the loop. The turn pauses here until a person says yes or no. The model "
                      "proposed the action; a human owns the decision.",
                      src="toolgate", status="warn", data=request)
        started = time.perf_counter()
        if self.options.approvals == "approve" or self.approver is None:
            approved, who = self.options.approvals == "approve", "policy"
        else:
            approved, who = bool(self.approver(request)), "human"
        self.rec.emit("human.decision", "human", f"{'Approved' if approved else 'Declined'} by {who}",
                      "The decision goes back to the model as the tool's result. A refusal is information "
                      "the model must handle gracefully, not an error to retry around.",
                      src="human", status="ok" if approved else "blocked",
                      dur_ms=int((time.perf_counter() - started) * 1000),
                      data={"approved": approved, "by": who, **request})
        return approved

    # ---------------------------------------------------------------- running it
    def _run(self, tool, call_id: str, args: dict[str, Any]) -> ToolOutcome:
        idem_key = hashlib.sha1(f"{self.rec.turn_id}|{tool.name}|{json.dumps(args, sort_keys=True)}".encode()).hexdigest()
        if tool.effect != "read":
            previous = self.ctx.store.tool_result(idem_key)
            if previous is not None:
                self.rec.emit("toolgate.idempotent", "toolgate", f"{tool.name} already ran: returning the recorded result",
                              "Idempotency. This exact write was already performed in this turn (the model retried, "
                              "or the turn was resumed). Doing it again would book two meetings or send two "
                              "messages, so the first result is returned instead.",
                              src="model", status="skip", data={"tool": tool.name, "idempotency_key": idem_key[:12]})
                return ToolOutcome(previous)

        attempts = 2 if tool.effect == "read" else 1     # reads are safe to retry automatically
        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                if self.state.chaos_left > 0:
                    self.state.chaos_left -= 1
                    raise TransientToolError(f"{tool.name} is temporarily unavailable (injected failure)")
                text = to_text(tool.fn(args, self.ctx))
            except TransientToolError as exc:
                dur = int((time.perf_counter() - started) * 1000)
                if attempt < attempts:
                    self.rec.emit("tool.retry", "tools", f"{tool.name} failed, retrying once",
                                  f"{exc}. A read has no side effects, so the harness retries it without "
                                  "involving the model.", src="toolgate", status="warn", dur_ms=dur,
                                  data={"tool": tool.name, "error": str(exc), "attempt": attempt})
                    continue
                self.rec.emit("tool.error", "tools", f"{tool.name} failed: {exc}",
                              "The failure is handed to the model as the tool's result. The model decides "
                              "what to do: retry, try another route, or tell the user.",
                              src="toolgate", status="error", dur_ms=dur,
                              data={"tool": tool.name, "args": args, "error": str(exc), "transient": True})
                return ToolOutcome(json.dumps({"error": str(exc), "transient": True}), True)
            except ToolError as exc:
                self.rec.emit("tool.error", "tools", f"{tool.name} failed: {exc}",
                              "A tool error is a result, not a crash. The message goes back to the model.",
                              src="toolgate", status="error", dur_ms=int((time.perf_counter() - started) * 1000),
                              data={"tool": tool.name, "args": args, "error": str(exc)})
                return ToolOutcome(json.dumps({"error": str(exc)}), True)
            except Exception as exc:  # a bug in a tool must not take the turn down
                self.rec.emit("tool.error", "tools", f"{tool.name} crashed: {type(exc).__name__}",
                              "An unexpected exception inside a tool is caught at the gate and reported to "
                              "the model in general terms.", src="toolgate", status="error",
                              data={"tool": tool.name, "error": f"{type(exc).__name__}: {exc}"})
                return ToolOutcome(json.dumps({"error": f"internal tool error ({type(exc).__name__})"}), True)
            break

        dur = int((time.perf_counter() - started) * 1000)
        truncated = len(text) > MAX_RESULT_CHARS
        if truncated:
            text = text[:MAX_RESULT_CHARS] + f"\n[truncated: {len(text) - MAX_RESULT_CHARS} more characters]"
        if tool.effect != "read":
            self.ctx.store.save_tool_result(idem_key, text)
        if tool.private:
            self.state.private_read = True
        self.rec.emit("tool.run", "tools", f"Ran {tool.name}",
                      f"The function ran on our side and returned {len(text)} characters. The model has "
                      "not seen it yet.", src="toolgate", dur_ms=dur,
                      data={"tool": tool.name, "args": args, "output": text[:1500], "truncated": truncated,
                            "effect": tool.effect})
        if tool.untrusted and self.options.guardrails:
            text = self._guard_result(tool.name, args, text)
        return ToolOutcome(text)

    def _guard_result(self, name: str, args: dict[str, Any], text: str) -> str:
        source = str(args.get("url") or args.get("path") or (f"web search: {args['query']}" if "query" in args else name))
        result = guardrails.guard_tool_result(name, source, text)
        self.state.tainted = True
        self.state.taint_sources.append(source)
        flagged = bool(result.findings)
        self.rec.emit("guard.tool_result", "guard_tool",
                      f"Untrusted content fenced{' (injection suspected)' if flagged else ''}",
                      ("This content came from outside, so it is wrapped in <untrusted_content> and the turn "
                       "is marked tainted: tools with outside effects are now locked. "
                       + ("The scan also found text that reads like instructions to the agent."
                          if flagged else "The scan found nothing suspicious; the fence applies anyway.")),
                      src="tools", status="warn" if flagged else "ok",
                      data={"tool": name, "source": source, "findings": result.to_json(), "tainted": True})
        return result.text
