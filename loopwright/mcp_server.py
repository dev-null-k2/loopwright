"""The MCP gateway: Loopwright as a tool server for Claude Code and other clients.

    claude mcp add loopwright -- loopwright mcp                                   # as a child process (stdio)
    loopwright mcp --http                                                         # or as a local web service
    claude mcp add --transport http loopwright http://127.0.0.1:8766/mcp

MCP (Model Context Protocol) is JSON-RPC 2.0. A server needs three methods to
be useful:

    server/discover   which protocol versions it speaks and what it offers
    tools/list        name, description and JSON Schema of each tool
    tools/call        run one and return its result as text

TWO TRANSPORTS. Over stdio a message is one JSON object per line: requests in
on stdin, responses out on stdout, nothing else on stdout ever. Over
Streamable HTTP a message is one POST to /mcp and the response is its answer.
`Server.handle` is the protocol and does not know which of the two it is on.

TWO ERAS, and this server answers both. Since the 2026-07-28 revision a
request stands on its own: it names its protocol version in `_meta` and there
is no handshake and no session, so any copy of the server can answer any
request. Older clients open with `initialize` instead, and are answered the
old way for as long as they keep talking.

Over HTTP three extra duties come with being reachable by more than one
program: the `Origin` header is checked, because a web page in your browser
can reach localhost too; the method and tool name in the headers must match
the body, because a proxy may have routed on the headers; and it listens on
127.0.0.1 only. Set LOOPWRIGHT_MCP_TOKEN to also require a bearer token.

This file is that protocol by hand, with no SDK, so the wire format is in
front of you. It is the third gateway into the same harness: a turn started
here runs the same code as one started in the browser.

By default turns started over MCP use the scripted model, because the client
is usually itself an LLM and a nested model call would be slow and confusing.
Set LOOPWRIGHT_PROVIDER to change that. Tools with outside effects are denied
here: there is no human on this channel to approve them.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from loopwright import __version__
from loopwright.config import Options, home_dir
from loopwright.events import list_traces, read_trace, trace_dir
from loopwright.learn import load_concepts
from loopwright.memory import kb
from loopwright.memory.consolidation import Candidate, extract_by_rules, resolve

MODERN = ["2026-07-28"]                                              # stateless: the version rides on every request
SUPPORTED = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]   # handshake-based versions still answered
META = "io.modelcontextprotocol/"
HEADER_MISMATCH, UNSUPPORTED_VERSION = -32020, -32022
INSTRUCTIONS = ("Loopwright is a glass-box AI agent. Use loopwright_explain for agent-design concepts and "
                "loopwright_ask to watch the harness handle a request.")

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
        """One JSON-RPC message in, one out (None for a notification). The same on every transport."""
        method, rid = request.get("method", ""), request.get("id")
        if rid is None:                 # a notification: never answered
            return None
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        version = (params.get("_meta") or {}).get(f"{META}protocolVersion") if isinstance(params.get("_meta"), dict) else None
        modern = version is not None    # a request that names its own version is a stateless, modern one

        def fail(code: int, message: str, data: Any = None) -> dict[str, Any]:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message, **({"data": data} if data else {})}}

        if modern and version not in MODERN:
            return fail(UNSUPPORTED_VERSION, "Unsupported protocol version", {"supported": MODERN, "requested": version})
        try:
            if method == "server/discover":
                result: Any = {"supportedVersions": MODERN, "capabilities": {"tools": {}}, "instructions": INSTRUCTIONS,
                               "ttlMs": 3_600_000, "cacheScope": "public"}
            elif method == "initialize" and not modern:
                wanted = params.get("protocolVersion", "")
                result = {"protocolVersion": wanted if wanted in SUPPORTED else SUPPORTED[0],
                          "capabilities": {"tools": {}},
                          "serverInfo": {"name": "loopwright", "version": __version__}, "instructions": INSTRUCTIONS}
            elif method == "ping" and not modern:           # removed from the protocol in 2026; older clients still send it
                result = {}
            elif method == "tools/list":
                # the same list for everyone, in a fixed order, so a client may cache it
                result = {"tools": TOOLS, **({"ttlMs": 3_600_000, "cacheScope": "public"} if modern else {})}
            elif method == "tools/call":
                handler = {"loopwright_ask": self.ask, "loopwright_explain": self.explain,
                           "loopwright_list_concepts": self.list_concepts, "loopwright_trace": self.trace,
                           "loopwright_memory_search": self.memory_search, "loopwright_remember": self.remember}.get(params.get("name"))
                if handler is None:
                    return fail(-32602, f"unknown tool {params.get('name')!r}")
                try:
                    text, failed = handler(params.get("arguments") or {}), False
                except KeyError as exc:
                    text, failed = f"missing argument: {exc}", True
                except Exception as exc:    # a tool failure is a result the client can read, not a protocol error
                    text, failed = f"{type(exc).__name__}: {exc}", True
                result = {"content": [{"type": "text", "text": text}], "isError": failed}
            else:
                return fail(-32601, f"method not found: {method}")
        except Exception as exc:
            return fail(-32603, f"{type(exc).__name__}: {exc}")
        if modern:                      # every modern result says it is complete, and who answered
            result = {"resultType": "complete", **result,
                      "_meta": {f"{META}serverInfo": {"name": "loopwright", "version": __version__}}}
        return {"jsonrpc": "2.0", "id": rid, "result": result}


def main() -> int:
    """The stdio transport: one message per line."""
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


# ---------------------------------------------------------------- the Streamable HTTP transport

def header_text(value: str) -> str:
    """Undo the =?base64?...?= wrapping a client uses for a header value that is not plain ASCII."""
    if value.startswith("=?base64?") and value.endswith("?="):
        try:
            return base64.b64decode(value[9:-2]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return value
    return value


def local_origin(origin: str) -> bool:
    """Is this page served from this machine? Anything else must not be able to drive a local server."""
    allowed = {o.strip().rstrip("/") for o in os.environ.get("LOOPWRIGHT_MCP_ORIGINS", "").split(",") if o.strip()}
    if origin.rstrip("/") in allowed:
        return True
    return (urllib.parse.urlsplit(origin).hostname or "") in ("localhost", "127.0.0.1", "::1")


def make_http_handler(server: Server) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:
            pass

        def _reply(self, status: int, body: dict[str, Any] | None = None) -> None:
            raw = json.dumps(body, ensure_ascii=False).encode() if body is not None else b""
            self.send_response(status)
            if raw:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _error(self, status: int, code: int, message: str, rid: Any = None) -> None:
            self._reply(status, {"jsonrpc": "2.0", **({"id": rid} if rid is not None else {}), "error": {"code": code, "message": message}})

        def do_GET(self) -> None:           # the 2025 transport opened a stream with GET; this revision has none
            self._reply(405)

        do_DELETE = do_GET                  # and ended a session with DELETE; there are no sessions to end

        def do_POST(self) -> None:
            if urllib.parse.urlsplit(self.path).path.rstrip("/") != "/mcp":
                return self._reply(404)
            origin = self.headers.get("Origin")
            if origin and not local_origin(origin):         # a web page elsewhere must not reach a server on this machine
                return self._error(403, -32600, "this origin may not use this server")
            token = os.environ.get("LOOPWRIGHT_MCP_TOKEN", "")
            if token and not hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {token}"):
                return self._reply(401)
            try:
                request = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            except (ValueError, json.JSONDecodeError):
                return self._error(400, -32700, "parse error")
            if not isinstance(request, dict):
                return self._error(400, -32600, "one JSON-RPC message per request")
            if request.get("id") is None:
                return self._reply(202)                     # a notification: accepted, nothing to say
            params = request.get("params") if isinstance(request.get("params"), dict) else {}
            meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
            version = meta.get(f"{META}protocolVersion")
            if version is not None:
                # a modern request: what the headers say must be what the body says, since a proxy may have routed on them
                wanted = {"MCP-Protocol-Version": version, "Mcp-Method": request.get("method")}
                if request.get("method") == "tools/call":
                    wanted["Mcp-Name"] = params.get("name")
                for name, value in wanted.items():
                    got = self.headers.get(name)
                    if got is None or header_text(got) != value:
                        return self._error(400, HEADER_MISMATCH, f"Header mismatch: {name} is "
                                           f"{'missing' if got is None else repr(got)}, the body says {value!r}", request["id"])
            response = server.handle(request)
            code = (response or {}).get("error", {}).get("code")
            status = 400 if code == UNSUPPORTED_VERSION else 404 if code == -32601 and version is not None else 200
            self._reply(status, response)

    return Handler


def serve_http(port: int = 8766, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """Start the HTTP transport and return the server (the caller decides whether to block on it)."""
    return ThreadingHTTPServer((host, port), make_http_handler(Server()))


def main_http(port: int = 8766, host: str = "127.0.0.1") -> int:
    httpd = serve_http(port, host)
    address = f"http://{host}:{httpd.server_address[1]}/mcp"
    print(f"Loopwright MCP server on {address}  (Streamable HTTP; protocol {MODERN[0]}, and older clients that send initialize)", file=sys.stderr)
    print(f"Add it to Claude Code with:  claude mcp add --transport http loopwright {address}", file=sys.stderr)
    if host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get("LOOPWRIGHT_MCP_TOKEN"):
        print("It is listening beyond this machine with no token. Set LOOPWRIGHT_MCP_TOKEN.", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
