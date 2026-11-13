"""The MCP gateway: Loopwright as a tool server for Claude Code and other clients.

    claude mcp add loopwright -- loopwright mcp

MCP (Model Context Protocol) is JSON-RPC 2.0. Over stdio that means one JSON
object per line: requests in on stdin, responses out on stdout, nothing else
on stdout ever. A server needs four methods to be useful:

    initialize     agree a protocol version, say what the server offers
    tools/list     name, description and JSON Schema of each tool
    tools/call     run one and return its result as text
    ping           liveness

This file is that protocol by hand, about 150 lines, with no SDK, so the wire
format is in front of you. It is the third gateway into the same harness: a
turn started here runs the same code as one started in the browser.

By default turns started over MCP use the scripted model, because the client
is usually itself an LLM and a nested model call would be slow and confusing.
Set LOOPWRIGHT_PROVIDER to change that. Tools with outside effects are denied
here: there is no human on this channel to approve them.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from loopwright import __version__
from loopwright.config import Options, home_dir
from loopwright.events import list_traces, read_trace, trace_dir
from loopwright.learn import load_concepts
from loopwright.memory import kb
from loopwright.memory.consolidation import Candidate, extract_by_rules, resolve

SUPPORTED = ["2025-06-18", "2025-03-26", "2024-11-05"]

_S = {"type": "string"}
TOOLS = [
    {"name": "loopwright_ask",
     "description": "Run one turn of the Loopwright agent and get its reply plus the list of steps it took "
                    "(guardrails, memory gate, context, model calls, tool calls). Use it to demonstrate or inspect "
                    "how an agent harness handles a request.",
     "inputSchema": {"type": "object", "properties": {"message": _S, "session": _S}, "required": ["message"]}},
    {"name": "loopwright_explain",
     "description": "Explain an agent-design term (harness, retrieval gate, compaction, prompt injection, evals, "
                    "idempotency ...) from the Loopwright handbook: what it is, why it exists, the tradeoff, where it "
                    "lives in the code, and how it comes up in an interview.",
     "inputSchema": {"type": "object", "properties": {"term": _S}, "required": ["term"]}},
    {"name": "loopwright_list_concepts",
     "description": "List every term in the Loopwright agent-design handbook with a one-line definition.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "loopwright_trace",
     "description": "Show the recorded steps of a past Loopwright turn. Pass a turn id, or 'last' for the most recent.",
     "inputSchema": {"type": "object", "properties": {"turn_id": _S}}},
    {"name": "loopwright_memory_search",
     "description": "Search what the Loopwright agent has stored in long-term memory (facts and episodes).",
     "inputSchema": {"type": "object", "properties": {"query": _S}, "required": ["query"]}},
    {"name": "loopwright_remember",
     "description": "Store one durable fact in the Loopwright agent's long-term memory.",
     "inputSchema": {"type": "object", "properties": {"text": _S}, "required": ["text"]}},
]


class Server:
    def __init__(self) -> None:
        self._agent = None

    @property
    def agent(self):
        if self._agent is None:         # created lazily so `initialize` answers instantly
            from loopwright.harness import Agent
            self._agent = Agent()
        return self._agent

    # ------------------------------------------------ the tools
    def ask(self, args: dict[str, Any]) -> str:
        options = Options(provider=os.environ.get("LOOPWRIGHT_PROVIDER", "scripted"), approvals="deny")
        result = self.agent.run_turn(str(args["message"]), session=str(args.get("session") or "mcp"), options=options)
        steps = "\n".join(f"{e.seq:>3}. [{e.stage}] {e.title}" + (f" ({e.status})" if e.status != "ok" else "")
                          for e in result.events)
        return f"{result.reply}\n\n--- {len(result.events)} steps (turn {result.turn_id}) ---\n{steps}\n\n{result.receipt['line']}"

    def explain(self, args: dict[str, Any]) -> str:
        term = str(args["term"]).strip()
        concepts = load_concepts()
        card = next((c for c in concepts if term.lower() in (c["id"], c["term"].lower(), *[a.lower() for a in c.get("aliases", [])])), None)
        if card is None:
            hits = kb.search(term, k=1)
            card = next((c for c in concepts if hits and hits[0].id == f"concept:{c['id']}"), None)
        if card is None:
            return f"The handbook has no card for {term!r}. Call loopwright_list_concepts to see what it covers."
        return (f"{card['term']}: {card['one_liner']}\n\n{card['explain']}\n\nWhy it exists: {card['why']}\n\n"
                f"The tradeoff: {card['tradeoff']}\n\nIn the code: {card['file']}\n\n"
                f"Interview question: {card['question']}\nA strong answer: {card['answer']}")

    def list_concepts(self, args: dict[str, Any]) -> str:
        return "\n".join(f"- {c['term']}: {c['one_liner']}" for c in load_concepts())

    def trace(self, args: dict[str, Any]) -> str:
        home = home_dir()
        turn_id = str(args.get("turn_id") or "last")
        if turn_id == "last":
            rows = list_traces(home, limit=1)
            if not rows:
                return "No turns have been recorded yet."
            turn_id = rows[0]["turn_id"]
        path = trace_dir(home) / f"{''.join(ch for ch in turn_id if ch.isalnum() or ch == '-')}.jsonl"
        if not path.is_file():
            return f"No trace named {turn_id}."
        trace = read_trace(path)
        lines = [f"Turn {turn_id}: {trace['turn'].get('message', '')}", ""]
        lines += [f"{e['seq']:>3}. [{e['stage']}] {e['title']}\n     {e['detail']}" for e in trace["events"]]
        return "\n".join(lines + ["", f"Reply: {trace['end'].get('reply', '')}"])

    def memory_search(self, args: dict[str, Any]) -> str:
        hits = self.agent.store.search_facts(str(args["query"]), k=5) + self.agent.store.search_episodes(str(args["query"]), k=2)
        return "\n".join(f"- {h.text}" for h in hits) or "Nothing stored matches that."

    def remember(self, args: dict[str, Any]) -> str:
        text = str(args["text"]).strip()
        parsed = extract_by_rules([text])
        decision = resolve(self.agent.store, parsed[0] if parsed else Candidate(text), source="mcp")
        return f"{decision['action']}: {decision['text']} ({decision['why']})"

    # ------------------------------------------------ the protocol
    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method, rid = request.get("method", ""), request.get("id")
        if rid is None:                 # a notification: never answered
            return None
        try:
            if method == "initialize":
                wanted = (request.get("params") or {}).get("protocolVersion", "")
                result: Any = {"protocolVersion": wanted if wanted in SUPPORTED else SUPPORTED[0],
                               "capabilities": {"tools": {}},
                               "serverInfo": {"name": "loopwright", "version": __version__},
                               "instructions": "Loopwright is a glass-box AI agent. Use loopwright_explain for "
                                               "agent-design concepts and loopwright_ask to watch the harness handle a request."}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                params = request.get("params") or {}
                handler = {"loopwright_ask": self.ask, "loopwright_explain": self.explain,
                           "loopwright_list_concepts": self.list_concepts, "loopwright_trace": self.trace,
                           "loopwright_memory_search": self.memory_search, "loopwright_remember": self.remember}.get(params.get("name"))
                if handler is None:
                    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"unknown tool {params.get('name')!r}"}}
                try:
                    text, failed = handler(params.get("arguments") or {}), False
                except KeyError as exc:
                    text, failed = f"missing argument: {exc}", True
                except Exception as exc:    # a tool failure is a result the client can read, not a protocol error
                    text, failed = f"{type(exc).__name__}: {exc}", True
                result = {"content": [{"type": "text", "text": text}], "isError": failed}
            else:
                return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {method}"}}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"}}
        return {"jsonrpc": "2.0", "id": rid, "result": result}


def main() -> int:
    server = Server()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            response: dict[str, Any] | None = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            response = server.handle(request) if isinstance(request, dict) else None
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0
