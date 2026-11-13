"""THE LOOP. This file is the agent; everything else is what surrounds it.

Strip away every framework and an agent is this:

    while True:
        response = model(context, tools)     # REASON: the model picks a move
        if response has no tool calls:
            return response.text             # it is talking to the human: done
        for call in response.tool_calls:
            result = run(call)               # ACT: our code runs the tool
            context.append(result)           # OBSERVE: the model sees it next time

That is ReAct (reason + act). The model never executes anything; it asks, the
harness runs, and the result is appended to the conversation for the next
call. "Agentic" means the model, not the programmer, decides how many times
to go round and which tool to use each time.

A loop needs reasons to stop, or a confused model will spin until the budget
is gone. There are three here:

  1. the model asks for no tools        the normal ending
  2. max_iterations is reached          a hard cap on rounds
  3. the token budget is spent          a hard cap on cost

On 2 and 3 the work so far is not thrown away: one last call is made with no
tools available, asking for the best answer from what was gathered.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from loopwright.config import Options
from loopwright.events import Recorder
from loopwright.models.base import Message
from loopwright.models.gateway import ModelGateway
from loopwright.toolgate import ToolGate
from loopwright.tools.registry import Registry

LIMIT_NOTE = ("\n\nYou have reached the step limit for this turn and can call no more tools. Answer now from "
              "what you have gathered, and say plainly what is missing.")


@dataclass
class LoopResult:
    text: str
    iterations: int
    stop: str               # answered | max_iterations | token_budget


def run_loop(gateway: ModelGateway, system: str, messages: list[Message], registry: Registry, gate: ToolGate,
             rec: Recorder, options: Options, *, purpose: str = "loop", start: int = 1,
             before_call: Callable[[list[Message]], None] | None = None,
             after_step: Callable[[int, list[Message]], None] | None = None) -> LoopResult:
    """Run one agent's loop. `messages` is mutated: when this returns it holds
    the whole working memory of the run (requests, tool calls, results)."""
    stop = "max_iterations"
    iteration = start - 1
    for iteration in range(start, options.max_iterations + 1):
        if gateway.tokens >= options.token_budget:
            stop = "token_budget"
            break
        if before_call and iteration > 1:
            before_call(messages)

        # ---- REASON: one model call over everything gathered so far
        response = gateway.call(system, messages, registry.schemas(), purpose=purpose, iteration=iteration,
                                src="context" if iteration == start else "tools", rec=rec)
        messages.append({"role": "assistant", "content": response.text,
                         "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments}
                                        for c in response.tool_calls]})

        # ---- stop condition 1: no tool calls means the model is answering
        if not response.tool_calls:
            rec.emit("loop.stop", "model", "Loop ends: the model answered",
                     f"No tool calls in the response, so the loop is over after {iteration} round(s). "
                     "This is the normal way a turn ends.", src="model",
                     data={"reason": "answered", "iterations": iteration})
            return LoopResult(response.text, iteration, "answered")

        # ---- ACT: every requested call goes through the tool gate
        for call in response.tool_calls:
            outcome = gate.execute(call.id, call.name, call.arguments)
            # ---- OBSERVE: the result joins the conversation for the next call
            messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                             "content": outcome.content, "is_error": outcome.is_error})
        rec.emit("loop.observe", "model", f"{len(response.tool_calls)} result(s) added to the context",
                 "The tool results are appended to the conversation. The model has no other way to learn "
                 "what happened: on the next call it re-reads everything, including these.",
                 src="tools", data={"iteration": iteration, "messages_now": len(messages),
                                    "results": [m["content"][:300] for m in messages[-len(response.tool_calls):]]})
        if after_step:
            after_step(iteration, messages)

    # ---- stop conditions 2 and 3: out of rounds or out of budget
    rec.emit("loop.stop", "model", f"Loop cut off: {stop.replace('_', ' ')}",
             "A hard limit stopped the loop. Rather than discard the work, one final call is made with no "
             "tools, asking for the best answer from what was gathered.", src="model", status="warn",
             data={"reason": stop, "iterations": iteration, "tokens": gateway.tokens,
                   "max_iterations": options.max_iterations, "token_budget": options.token_budget})
    final = gateway.call(system + LIMIT_NOTE, messages, [], purpose=purpose, iteration=iteration + 1, src="tools", rec=rec)
    messages.append({"role": "assistant", "content": final.text, "tool_calls": []})
    return LoopResult(final.text or "I ran out of steps before I could finish. Try a smaller request.", iteration, stop)
