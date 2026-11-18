"""MCP CLIENT: borrow tools from any MCP server, started here or reached over HTTP.

mcp_server.py makes Loopwright a server. This is the other side: Loopwright
as a client that connects to someone else's server, asks what tools it has,
and adds them to its own registry. Configure servers in ~/.loopwright/mcp.json:

    {"servers": {
      "files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]},
      "docs":  {"url": "https://example.com/mcp", "headers": {"Authorization": "Bearer ${DOCS_TOKEN}"}, "trust": "read"}
    }}

Each borrowed tool appears to the model as `<server>__<tool>`.

Two defaults follow from one fact: you did not write this server.
  - Its results are UNTRUSTED. They are fenced and they taint the turn, like a
    web page. A tool description is also text a stranger wrote that goes into
    your prompt, which is why you should only connect servers you trust.
  - Its tools are treated as having OUTSIDE EFFECTS, so each call needs
    approval. Mark a server `"trust": "read"` in mcp.json once you know its
    tools only read.

TWO TRANSPORTS, ONE PROTOCOL. MCP is JSON-RPC 2.0. Over stdio the client
starts the server as a child process and each message is one line. Over
Streamable HTTP the server is already running somewhere and each message is
its own POST to one URL; the answer comes back as a JSON body, or as a short
Server-Sent Events stream that ends with the answer.

TWO ERAS. Until 2025 a connection opened with an `initialize` handshake and,
over HTTP, the server handed out a session id. The 2026-07-28 revision made
the protocol stateless: no handshake, no session, and every request carries
its protocol version and the client's identity in `_meta`, mirrored into HTTP
headers (`MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`) so a load balancer
can route without reading the body. Servers of both kinds are in use, so
this client speaks both and works out which one it has:

  stdio   ask `server/discover` first. A modern server answers; an older one
          returns some error or stays silent, and the client falls back to
          `initialize`.
  HTTP    send a modern request first. A modern server answers, or rejects it
          with one of the errors the new revision defines. Anything else on a
          4xx means an older server: fall back to `initialize`.

Not here: OAuth sign-in (pass a token in `headers`), the retired HTTP+SSE
transport from 2024, and answering a server that asks the client for input
(sampling or elicitation); such a result is reported as an error.
"""

from __future__ import annotations

import base64
import json
import os
import queue
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from loopwright import __version__
from loopwright.models._http import read_stream
from loopwright.tools.registry import Registry, Tool, ToolContext, ToolError, TransientToolError

MODERN = "2026-07-28"               # the stateless revision: per-request metadata, no handshake
LEGACY = "2025-11-25"               # the last revision that opens with `initialize`
META = "io.modelcontextprotocol/"
# errors only a modern server sends: header mismatch, missing client capability, unsupported version
MODERN_ERRORS = {-32020, -32021, -32022}
PROBE_S = 4.0                       # how long a stdio server gets to answer server/discover before it is taken for an older one
START_S = 60.0                      # how long a server gets to start (npx may be downloading it)
CALL_S = 120.0
TOKEN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")       # what an HTTP header name may be made of


def request_meta(version: str = MODERN) -> dict[str, Any]:
    """What every modern request carries: the version it speaks and who is asking."""
    return {f"{META}protocolVersion": version, f"{META}clientInfo": {"name": "loopwright", "version": __version__},
            f"{META}clientCapabilities": {}}


def is_modern_error(reply: Any) -> bool:
    return isinstance(reply, dict) and isinstance(reply.get("error"), dict) and reply["error"].get("code") in MODERN_ERRORS


def pick_version(supported: Any) -> str:
    """Choose from the versions a modern server says it speaks."""
    versions = [v for v in supported or [] if isinstance(v, str)]
    modern = sorted(v for v in versions if v >= MODERN)
    if MODERN in versions:
        return MODERN
    if modern:
        return modern[0]            # a later revision: the closest one to what this client was written for
    raise ToolError(f"the server speaks only {', '.join(versions) or 'no version'}; this client needs {MODERN} or an initialize handshake")


def header_value(value: Any) -> str:
    """A value made safe for an HTTP header. Plain visible ASCII goes as it is;
    anything else (other alphabets, line breaks, spaces at the ends) is sent
    as =?base64?...?= so it cannot break or inject a header."""
    text = "true" if value is True else "false" if value is False else str(value)
    plain = text == text.strip() and all(0x20 <= ord(ch) <= 0x7E for ch in text) and bool(text)
    if plain and not (text.startswith("=?base64?") and text.endswith("?=")):
        return text
    return f"=?base64?{base64.b64encode(text.encode()).decode()}?="


def header_params(schema: Any) -> dict[tuple[str, ...], str]:
    """Tool parameters a server wants mirrored into `Mcp-Param-<Name>` headers
    (marked `x-mcp-header` in the tool's schema) -> {path to the parameter: header name}.
    Raises ValueError when a marking breaks the rules, and the tool is then left out."""
    found: dict[tuple[str, ...], str] = {}

    def walk(node: Any, path: tuple[str, ...], reachable: bool) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, path, False)
            return
        if not isinstance(node, dict):
            return
        if "x-mcp-header" in node:
            name = node["x-mcp-header"]
            if not reachable or not path:           # inside a list, a oneOf, a $ref: a client cannot find the value statically
                raise ValueError("x-mcp-header is only allowed on plain nested properties")
            if not isinstance(name, str) or not TOKEN.match(name):
                raise ValueError(f"x-mcp-header on {path[-1]!r} is not a valid header name")
            if node.get("type") not in ("string", "integer", "boolean"):
                raise ValueError(f"x-mcp-header on {path[-1]!r} needs a string, integer or boolean parameter")
            if name.lower() in {n.lower() for n in found.values()}:
                raise ValueError(f"x-mcp-header {name!r} is used twice")
            found[path] = name
        for key, child in node.items():
            if key == "properties" and isinstance(child, dict):
                for name, sub in child.items():
                    walk(sub, (*path, name), reachable)
            elif key != "x-mcp-header":
                walk(child, path, False)

    walk(schema, (), True)
    return found


def expand_env(headers: dict[str, str] | None) -> dict[str, str]:
    """Headers from mcp.json, with ${NAME} replaced from the environment, so a token is never written in the file."""
    out = {}
    for name, value in (headers or {}).items():
        def lookup(match: re.Match[str], name: str = name) -> str:
            if match.group(1) not in os.environ:
                raise ToolError(f"set the environment variable {match.group(1)} (used by the {name!r} header)")
            return os.environ[match.group(1)]
        out[str(name)] = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", lookup, str(value))
    return out


class _Connection:
    """What both transports share: reading a result, listing tools, calling one."""
    name = ""
    transport = ""
    era = ""                        # "modern" | "legacy", once known
    version = ""

    def request(self, method: str, params: dict[str, Any] | None = None, tool: str = "") -> Any:
        raise NotImplementedError

    def _result(self, reply: dict[str, Any]) -> Any:
        if "error" in reply:
            error = reply["error"] if isinstance(reply["error"], dict) else {"message": str(reply["error"])}
            raise ToolError(f"{self.name}: {error.get('message', 'error')}")
        result = reply.get("result")
        if isinstance(result, dict) and result.get("resultType") == "input_required":
            raise ToolError(f"{self.name}: the server asked this client for input (a question for the user, or a model "
                            "call) before it would finish. Loopwright does not answer those")
        return result

    def tools(self) -> list[dict[str, Any]]:
        return (self.request("tools/list") or {}).get("tools", [])

    def call(self, tool: str, arguments: dict[str, Any]) -> str:
        result = self.request("tools/call", {"name": tool, "arguments": arguments}, tool=tool) or {}
        text = "\n".join(part.get("text", "") for part in result.get("content", []) if part.get("type") == "text")
        if not text and result.get("structuredContent") is not None:
            text = json.dumps(result["structuredContent"], ensure_ascii=False)
        if result.get("isError"):
            raise ToolError(text or "the MCP tool reported an error")
        return text

    def describe(self) -> str:
        return f"{self.transport}, {self.era} protocol ({self.version})"

    def close(self) -> None:
        pass


class MCPConnection(_Connection):
    """A server started here as a child process, spoken to over its standard streams."""
    transport = "stdio"

    def __init__(self, name: str, command: str, args: list[str], env: dict[str, str] | None = None):
        self.name = name
        self._lock = threading.Lock()
        self._id = 0
        self._proc = subprocess.Popen([command, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, text=True, env=env)
        self._inbox: queue.Queue[dict[str, Any] | None] = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self._open()

    def _read(self) -> None:
        """Runs in its own thread, so a request can give up waiting without blocking on the pipe."""
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict):
                self._inbox.put(message)
        self._inbox.put(None)                       # the server closed its end

    def _send(self, message: dict[str, Any]) -> None:
        assert self._proc.stdin is not None
        try:
            self._proc.stdin.write(json.dumps(message) + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise TransientToolError(f"MCP server {self.name!r} is not responding: {exc}") from exc

    def _wait(self, ids: set[int], timeout: float) -> dict[str, Any] | None:
        """The next reply to one of these requests, or None when the time is up. Notifications and stray replies are skipped."""
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            try:
                message = self._inbox.get(timeout=left)
            except queue.Empty:
                return None
            if message is None:
                self._inbox.put(None)               # leave the marker for the next caller
                raise TransientToolError(f"MCP server {self.name!r} closed the connection")
            if message.get("id") in ids:
                return message

    def _open(self) -> None:
        """Work out which era the server is from (see the top of this file)."""
        self._id += 1
        probe = self._id
        self._send({"jsonrpc": "2.0", "id": probe, "method": "server/discover", "params": {"_meta": request_meta()}})
        reply = self._wait({probe}, PROBE_S)
        if reply is not None and self._modern(reply):
            return
        # An error that is not one of the modern ones, or silence: an older server. It may also just be slow to
        # start, so the handshake is sent and whichever of the two answers arrives first decides.
        self._id += 1
        hello = self._id
        self._send({"jsonrpc": "2.0", "id": hello, "method": "initialize",
                    "params": {"protocolVersion": LEGACY, "capabilities": {}, "clientInfo": {"name": "loopwright", "version": __version__}}})
        deadline = time.monotonic() + START_S
        while True:
            reply = self._wait({probe, hello}, deadline - time.monotonic())
            if reply is None:
                raise TransientToolError(f"MCP server {self.name!r} did not answer within {START_S:.0f}s")
            if reply["id"] == probe:
                if self._modern(reply):
                    return                          # it was modern after all, only slow
                continue                            # an older server's "what is server/discover?": expected
            self.era, self.version = "legacy", str((self._result(reply) or {}).get("protocolVersion", LEGACY))
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            return

    def _modern(self, reply: dict[str, Any]) -> bool:
        result = reply.get("result")
        if isinstance(result, dict) and "supportedVersions" in result:
            self.era, self.version = "modern", pick_version(result["supportedVersions"])
            return True
        if is_modern_error(reply):
            self.era, self.version = "modern", pick_version((reply["error"].get("data") or {}).get("supported"))
            return True
        return False

    def request(self, method: str, params: dict[str, Any] | None = None, tool: str = "") -> Any:
        with self._lock:                         # one request at a time over one pipe
            self._id += 1
            body = dict(params or {})
            if self.era == "modern":
                body["_meta"] = request_meta(self.version)
            self._send({"jsonrpc": "2.0", "id": self._id, "method": method, "params": body})
            reply = self._wait({self._id}, CALL_S)
        if reply is None:
            raise TransientToolError(f"MCP server {self.name!r} did not answer {method} within {CALL_S:.0f}s")
        return self._result(reply)

    def close(self) -> None:
        try:
            if self._proc.stdin:
                self._proc.stdin.close()            # the polite way: a server exits when its input ends
            self._proc.terminate()
        except OSError:
            pass


class MCPHttpConnection(_Connection):
    """A server that is already running, reached over Streamable HTTP: one POST per message."""
    transport = "http"

    def __init__(self, name: str, url: str, headers: dict[str, str] | None = None, allow_http: bool = False,
                 timeout: float = 30.0, opener: Any = urllib.request.urlopen):
        parts = urllib.parse.urlsplit(url)
        local = (parts.hostname or "") in ("localhost", "127.0.0.1", "::1")
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ToolError(f"{url!r} is not an http(s) address")
        if parts.scheme == "http" and not local and not allow_http:
            raise ToolError("plain http is only allowed for a server on this machine: its headers would cross the network "
                            'unencrypted. Use https, or add "allow_http": true if you are sure')
        self.name, self.url, self._headers, self._timeout, self._open_url = name, url, expand_env(headers), timeout, opener
        self._lock = threading.Lock()
        self._id = 0
        self._session = ""                      # legacy servers only: the Mcp-Session-Id they handed out
        self._mirrored: dict[str, dict[tuple[str, ...], str]] = {}     # tool -> parameters to mirror into headers
        self.skipped: list[dict[str, str]] = []
        self._discover()

    # ------------------------------------------------------------ one POST
    def _post(self, message: dict[str, Any], extra: dict[str, str]) -> tuple[int, dict[str, Any] | None, Any]:
        """-> (HTTP status, the JSON-RPC reply if there was one, the response headers)."""
        request = urllib.request.Request(self.url, data=json.dumps(message).encode(), method="POST", headers={
            "Content-Type": "application/json", "Accept": "application/json, text/event-stream", **self._headers, **extra})
        try:
            response = self._open_url(request, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                reply = json.loads(raw.decode("utf-8", "replace")) if raw.strip() else None
            except json.JSONDecodeError:
                reply = None
            return exc.code, reply if isinstance(reply, dict) else None, exc.headers
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise TransientToolError(f"MCP server {self.name!r} cannot be reached: {exc}") from exc
        try:
            with response:
                kind = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if kind == "text/event-stream":
                    # a stream scoped to this request: progress notes may come first, the answer comes last
                    for event in read_stream(response):
                        if isinstance(event, dict) and event.get("id") == message.get("id") and ("result" in event or "error" in event):
                            return response.status, event, response.headers
                    raise TransientToolError(f"MCP server {self.name!r} closed the stream before answering")
                raw = response.read()
                reply = json.loads(raw.decode("utf-8", "replace")) if raw.strip() else None
                return response.status, reply if isinstance(reply, dict) else None, response.headers
        except (json.JSONDecodeError, OSError) as exc:
            raise TransientToolError(f"MCP server {self.name!r} sent something unreadable: {exc}") from exc

    # ------------------------------------------------------------ which era?
    def _discover(self) -> None:
        self._id += 1
        message = {"jsonrpc": "2.0", "id": self._id, "method": "server/discover", "params": {"_meta": request_meta()}}
        status, reply, _ = self._post(message, {"MCP-Protocol-Version": MODERN, "Mcp-Method": "server/discover"})
        if status in (401, 403):
            raise ToolError(f"{self.name}: the server refused the connection (HTTP {status}). If it needs a token, add "
                            '"headers": {"Authorization": "Bearer ${YOUR_TOKEN}"} to its entry in mcp.json')
        result = (reply or {}).get("result")
        if status < 300 and isinstance(result, dict) and "supportedVersions" in result:
            self.era, self.version = "modern", pick_version(result["supportedVersions"])
        elif is_modern_error(reply):
            self.era, self.version = "modern", pick_version((reply["error"].get("data") or {}).get("supported"))
        else:
            self._initialize()                  # not a modern answer: an older server, which wants a handshake

    def _initialize(self) -> None:
        self._id += 1
        hello = {"jsonrpc": "2.0", "id": self._id, "method": "initialize",
                 "params": {"protocolVersion": LEGACY, "capabilities": {}, "clientInfo": {"name": "loopwright", "version": __version__}}}
        status, reply, headers = self._post(hello, {})
        if reply is None or "result" not in reply:
            detail = ((reply or {}).get("error") or {}).get("message") or f"HTTP {status}"
            raise ToolError(f"{self.name}: no MCP server answered at {self.url} ({detail}). If it is a server from 2024 that "
                            "only speaks the retired HTTP+SSE transport, that transport is not supported here")
        self.era, self.version = "legacy", str(reply["result"].get("protocolVersion", LEGACY))
        self._session = headers.get("Mcp-Session-Id") or ""
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, self._legacy_headers())

    def _legacy_headers(self) -> dict[str, str]:
        return {"MCP-Protocol-Version": self.version, **({"Mcp-Session-Id": self._session} if self._session else {})}

    # ------------------------------------------------------------ requests
    def request(self, method: str, params: dict[str, Any] | None = None, tool: str = "") -> Any:
        with self._lock:
            for attempt in (1, 2):
                self._id += 1
                body = dict(params or {})
                if self.era == "modern":
                    body["_meta"] = request_meta(self.version)
                    extra = {"MCP-Protocol-Version": self.version, "Mcp-Method": method}
                    if tool:
                        extra["Mcp-Name"] = header_value(tool)
                        extra.update(self._param_headers(tool, body.get("arguments") or {}))
                else:
                    extra = self._legacy_headers()
                status, reply, _ = self._post({"jsonrpc": "2.0", "id": self._id, "method": method, "params": body}, extra)
                code = ((reply or {}).get("error") or {}).get("code") if isinstance((reply or {}).get("error"), dict) else None
                if attempt == 1 and self.era == "modern" and code == -32022:
                    self.version = pick_version((reply["error"].get("data") or {}).get("supported"))      # it changed its mind: use what it offers
                    continue
                if attempt == 1 and self.era == "modern" and code == -32020 and tool:
                    self._learn(self._list())           # the tool's schema changed under us: read it again
                    continue
                if attempt == 1 and self.era == "legacy" and status == 404 and self._session:
                    self._initialize()                  # the session expired: open a new one
                    continue
                break
        if reply is None:
            if status in (401, 403):
                raise ToolError(f"{self.name}: the server refused the request (HTTP {status})")
            raise TransientToolError(f"MCP server {self.name!r} answered HTTP {status} with no message")
        return self._result(reply)

    def _list(self) -> list[dict[str, Any]]:
        """tools/list without taking the lock (the caller holds it)."""
        self._id += 1
        body = {"_meta": request_meta(self.version)}
        _, reply, _ = self._post({"jsonrpc": "2.0", "id": self._id, "method": "tools/list", "params": body},
                                 {"MCP-Protocol-Version": self.version, "Mcp-Method": "tools/list"})
        return ((reply or {}).get("result") or {}).get("tools", [])

    def _learn(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remember which parameters each tool wants mirrored into headers; leave out a tool whose markings are invalid."""
        usable, self._mirrored, self.skipped = [], {}, []
        for tool in tools:
            try:
                self._mirrored[tool["name"]] = header_params(tool.get("inputSchema") or {})
            except ValueError as exc:
                self.skipped.append({"tool": str(tool.get("name")), "why": str(exc)})
                continue
            usable.append(tool)
        return usable

    def _param_headers(self, tool: str, arguments: dict[str, Any]) -> dict[str, str]:
        out = {}
        for path, name in self._mirrored.get(tool, {}).items():
            value: Any = arguments
            for key in path:
                value = value.get(key) if isinstance(value, dict) else None
            if value is not None:
                out[f"Mcp-Param-{name}"] = header_value(value)
        return out

    def tools(self) -> list[dict[str, Any]]:
        return self._learn(super().tools())


def _say(ctx: ToolContext, connection: _Connection, server: str, tool: str, ms: int) -> None:
    """The step on the board for a call that left this process."""
    ctx.rec.emit("mcp.call", "tools", f"MCP: {tool} on '{server}' over {connection.transport}",
                 "The tool belongs to another program. The call left this process as a JSON-RPC `tools/call` message: "
                 + ("one POST to the server's address, with the method and tool name copied into headers so a proxy "
                    "can route it without reading the body." if connection.transport == "http"
                    else "one line written to the server's standard input.")
                 + " What comes back was written by that server, so it is treated as untrusted.",
                 src="toolgate", dur_ms=ms,
                 data={"server": server, "tool": tool, "transport": connection.transport, "era": connection.era,
                       "protocol_version": connection.version})


def connect(name: str, spec: dict[str, Any]) -> _Connection:
    """An entry of mcp.json -> a live connection. `url` means HTTP; `command` means a child process."""
    if spec.get("url"):
        return MCPHttpConnection(name, str(spec["url"]), spec.get("headers"), bool(spec.get("allow_http")))
    return MCPConnection(name, spec["command"], spec.get("args", []), spec.get("env"))


def load_mcp_tools(home: Path, registry: Registry) -> list[dict[str, Any]]:
    """Connect every server in <home>/mcp.json and add its tools to `registry`.
    Returns one status row per server; a server that fails to start is
    reported and skipped, never fatal."""
    config = Path(home) / "mcp.json"
    if not config.is_file():
        return []
    try:
        servers = json.loads(config.read_text(encoding="utf-8")).get("servers", {})
    except (OSError, json.JSONDecodeError) as exc:
        return [{"server": "mcp.json", "ok": False, "error": f"could not read it: {exc}", "tools": []}]
    status = []
    for name, spec in servers.items():
        try:
            connection = connect(name, spec)
            added = []
            for remote in connection.tools():
                local = f"{name}__{remote['name']}"

                def run(args: dict[str, Any], ctx: ToolContext, _c=connection, _t=remote["name"], _s=name) -> str:
                    began = time.perf_counter()
                    try:
                        return _c.call(_t, args)
                    finally:
                        if ctx is not None:                     # None when a test calls the function outside a turn
                            _say(ctx, _c, _s, _t, int((time.perf_counter() - began) * 1000))

                registry.add(Tool(local, f"[from MCP server '{name}'] {remote.get('description', '')}"[:600],
                                  remote.get("inputSchema") or {"type": "object", "properties": {}}, run,
                                  effect="read" if spec.get("trust") == "read" else "external", untrusted=True))
                added.append(local)
            status.append({"server": name, "ok": True, "tools": added, "trust": spec.get("trust", "ask"),
                           "transport": connection.transport, "era": connection.era, "protocol_version": connection.version,
                           "skipped": getattr(connection, "skipped", [])})
        except Exception as exc:
            status.append({"server": name, "ok": False, "error": f"{type(exc).__name__}: {exc}", "tools": []})
    return status
