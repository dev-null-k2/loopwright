"""SUBAGENTS: the orchestrator-workers pattern, as a tool.

When a task splits into independent parts, the main agent can hand each part
to a worker. A worker is just another run of the same loop with three
differences, and each one is a design decision:

  fresh context     the worker starts with only its task, not the whole
                    conversation. It can read ten documents and burn 20k
                    tokens; the orchestrator's context never sees them.
  a short summary   the worker returns a few sentences. That compression is
                    the whole point: context isolation.
  fewer tools       workers here are read-only. A worker cannot write memory,
                    book events or send messages, so a confused worker cannot
                    do damage.

Workers run in parallel threads. The cost: more total tokens (every worker
pays for its own prompt) and the orchestrator only knows what the summaries
tell it. Use workers for breadth, not for tasks that need shared state.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from loopwright.tools.registry import Tool, ToolContext, ToolError

MAX_WORKERS = 3
WORKER_TOOLS = ["search_knowledge", "calculator", "clock"]


def delegate(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    tasks = [t.strip() for t in args["tasks"] if t.strip()][:MAX_WORKERS]
    if not tasks:
        raise ToolError("give at least one task")
    if ctx.spawn is None:
        raise ToolError("subagents cannot delegate further")   # depth limit: one level
    ctx.rec.emit("subagent.spawn", "subagent", f"Spawning {len(tasks)} worker(s) in parallel",
                 "Orchestrator-workers. Each worker gets a fresh context holding only its task, a "
                 "read-only toolset, and its own loop. They run at the same time.",
                 src="tools", data={"tasks": tasks, "worker_tools": WORKER_TOOLS})
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(lambda pair: ctx.spawn(pair[1], pair[0] + 1), enumerate(tasks)))
    for result in results:
        ctx.cited.update(result.get("cited", []))
    spent = sum(r["tokens"] for r in results)
    returned = sum(len(r["summary"]) // 4 for r in results)
    ctx.rec.emit("subagent.return", "subagent", f"{len(results)} worker(s) returned summaries",
                 f"The workers used about {spent} tokens between them. The orchestrator receives only "
                 f"their summaries, about {returned} tokens. That gap is context isolation.",
                 src="subagent", data={"results": results, "tokens_spent": spent, "tokens_returned": returned})
    return {"results": [{"task": r["task"], "summary": r["summary"]} for r in results]}


def delegate_tool() -> Tool:
    return Tool(
        "delegate",
        "Hand independent research tasks to parallel worker agents. Each worker has a fresh context and "
        "read-only tools and returns a short summary. Use it when a question splits into 2-3 separate parts.",
        {"type": "object", "properties": {"tasks": {"type": "array", "items": {"type": "string"},
                                                    "description": "one self-contained instruction per worker"}},
         "required": ["tasks"], "additionalProperties": False},
        delegate, stage="subagent")
