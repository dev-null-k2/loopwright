"""MCP CLIENT: borrow tools from any MCP server.

mcp_server.py makes Loopwright a server. This is the other side: Loopwright
as a client that starts someone else's server, asks what tools it has, and
adds them to its own registry. Configure servers in ~/.loopwright/mcp.json:

    {"servers": {"files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]}}}

Each borrowed tool appears to the model as `<server>__<tool>`.

Two defaults follow from one fact: you did not write this server.
  - Its results are UNTRUSTED. They are fenced and they taint the turn, like a
    web page. A tool description is also text a stranger wrote that goes into
    your prompt, which is why you should only connect servers you trust.
  - Its tools are treated as having OUTSIDE EFFECTS, so each call needs
    approval. Mark a server `"trust": "read"` in mcp.json once you know its
    tools only read.

The protocol is the same newline-delimited JSON-RPC that mcp_server.py speaks.
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any

from loopwright import __version__
from loopwright.tools.registry import Registry, Tool, ToolContext, ToolError, TransientToolError


class MCPConnection:
    def __init__(self, name: str, command: str, args: list[str], env: dict[str, str] | None = None):
        self.name = name
        self._lock = threading.Lock()
        self._id = 0
        self._proc = subprocess.Popen([command, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, text=True, env=env)
        self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "loopwright", "version": __version__}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, message: dict[str, Any]) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(message) + "\n")
        self._proc.stdin.flush()

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        with self._lock:                         # one request at a time over one pipe
            self._id += 1
            try:
                self._send({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})
                assert self._proc.stdout is not None
                while True:
                    line = self._proc.stdout.readline()
                    if not line:
                        raise TransientToolError(f"MCP server {self.name!r} closed the connection")
                    reply = json.loads(line)
                    if reply.get("id") == self._id:      # skip notifications and anything unrelated
                        break
            except (BrokenPipeError, OSError, json.JSONDecodeError) as exc:
                raise TransientToolError(f"MCP server {self.name!r} is not responding: {exc}") from exc
        if "error" in reply:
            raise ToolError(f"{self.name}: {reply['error'].get('message', 'error')}")
        return reply.get("result")

    def tools(self) -> list[dict[str, Any]]:
        return (self.request("tools/list") or {}).get("tools", [])

    def call(self, tool: str, arguments: dict[str, Any]) -> str:
        result = self.request("tools/call", {"name": tool, "arguments": arguments}) or {}
        text = "\n".join(part.get("text", "") for part in result.get("content", []) if part.get("type") == "text")
        if result.get("isError"):
            raise ToolError(text or "the MCP tool reported an error")
        return text

    def close(self) -> None:
        try:
            self._proc.terminate()
        except OSError:
            pass


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
            connection = MCPConnection(name, spec["command"], spec.get("args", []), spec.get("env"))
            added = []
            for remote in connection.tools():
                local = f"{name}__{remote['name']}"

                def run(args: dict[str, Any], ctx: ToolContext, _c=connection, _t=remote["name"]) -> str:
                    return _c.call(_t, args)

                registry.add(Tool(local, f"[from MCP server '{name}'] {remote.get('description', '')}"[:600],
                                  remote.get("inputSchema") or {"type": "object", "properties": {}}, run,
                                  effect="read" if spec.get("trust") == "read" else "external", untrusted=True))
                added.append(local)
            status.append({"server": name, "ok": True, "tools": added, "trust": spec.get("trust", "ask")})
        except Exception as exc:
            status.append({"server": name, "ok": False, "error": f"{type(exc).__name__}: {exc}", "tools": []})
    return status
