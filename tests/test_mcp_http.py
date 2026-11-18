"""MCP over HTTP, and the two eras of the protocol: the stateless 2026 revision and the older handshake."""
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from loopwright.config import Options
from loopwright.harness import Agent
from loopwright.mcp_server import serve_http
from loopwright.tools import mcp_client
from loopwright.tools.mcp_client import MCPConnection, MCPHttpConnection, header_params, header_value, pick_version
from loopwright.tools.plugins import add_mcp_server
from loopwright.tools.registry import ToolError, TransientToolError

META = "io.modelcontextprotocol/"
WEATHER = {"name": "get_weather", "description": "Weather for a place.",
           "inputSchema": {"type": "object", "required": ["place"], "properties": {
               "place": {"type": "string"}, "region": {"type": "string", "x-mcp-header": "Region"}}}}
BROKEN = {"name": "bad_marking", "description": "Its header marking is on a number, which is not allowed.",
          "inputSchema": {"type": "object", "properties": {"ratio": {"type": "number", "x-mcp-header": "Ratio"}}}}
ACCENTED = {"name": "météo", "description": "A tool whose name is not plain ASCII.", "inputSchema": {"type": "object", "properties": {}}}


def serve(handler):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}/mcp"


class Recording(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *args):
        pass

    def read(self):
        message = json.loads(self.rfile.read(int(self.headers["content-length"])))
        type(self).seen.append({"headers": {k.lower(): v for k, v in self.headers.items()}, **message})
        return message

    def send(self, status, body=None, headers=None, sse=None):
        raw = b"" if body is None else json.dumps(body).encode()
        if sse is not None:
            raw = "".join(f"event: message\ndata: {json.dumps(m)}\n\n" for m in sse).encode()
        self.send_response(status)
        self.send_header("content-type", "text/event-stream" if sse is not None else "application/json")
        self.send_header("content-length", str(len(raw)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(raw)


class Modern(Recording):
    """A 2026-07-28 server: stateless, strict about its headers, and it answers one tool as a stream."""
    seen: list = []
    versions = ["2026-07-28"]

    def do_POST(self):
        m = self.read()
        meta, rid = m["params"].get("_meta", {}), m["id"]
        if self.headers.get("MCP-Protocol-Version") != meta.get(f"{META}protocolVersion") or self.headers.get("Mcp-Method") != m["method"]:
            return self.send(400, {"jsonrpc": "2.0", "id": rid, "error": {"code": -32020, "message": "Header mismatch"}})
        if meta.get(f"{META}protocolVersion") not in self.versions:
            return self.send(400, {"jsonrpc": "2.0", "id": rid, "error": {"code": -32022, "message": "Unsupported protocol version",
                                                                         "data": {"supported": self.versions, "requested": meta.get(f"{META}protocolVersion")}}})
        done = {"resultType": "complete", "_meta": {f"{META}serverInfo": {"name": "stub", "version": "1"}}}
        if m["method"] == "server/discover":
            return self.send(200, {"jsonrpc": "2.0", "id": rid, "result": {**done, "supportedVersions": self.versions, "capabilities": {"tools": {}}}})
        if m["method"] == "tools/list":
            return self.send(200, {"jsonrpc": "2.0", "id": rid, "result": {**done, "tools": [WEATHER, BROKEN, ACCENTED], "ttlMs": 1000, "cacheScope": "public"}})
        if m["method"] == "tools/call" and m["params"]["name"] == "get_weather":
            if m["params"]["arguments"].get("place") == "ask me":       # the server wants something from the client first
                return self.send(200, {"jsonrpc": "2.0", "id": rid, "result": {"resultType": "input_required", "inputRequests": {}}})
            return self.send(200, sse=[{"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}},
                                       {"jsonrpc": "2.0", "id": rid, "result": {**done, "content": [{"type": "text", "text": "Rain in " + m["params"]["arguments"]["place"]}]}}])
        if m["method"] == "tools/call":
            return self.send(200, {"jsonrpc": "2.0", "id": rid, "result": {**done, "structuredContent": {"ok": True}, "content": []}})
        self.send(404, {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Method not found"}})


class Legacy(Recording):
    """A 2025-11-25 server: it wants `initialize`, hands out a session id, and insists on it afterwards."""
    seen: list = []
    sessions: set = set()
    issued = 0

    def do_POST(self):
        m = self.read()
        if m.get("method") == "initialize":
            Legacy.issued += 1
            session = f"session-{Legacy.issued}"
            Legacy.sessions.add(session)
            return self.send(200, {"jsonrpc": "2.0", "id": m["id"], "result": {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                                                                             "serverInfo": {"name": "old", "version": "1"}}}, {"Mcp-Session-Id": session})
        session = self.headers.get("Mcp-Session-Id")
        if not session:
            self.send_response(400)                         # no JSON-RPC body: this is how an older server says "who are you?"
            self.send_header("content-length", "11")
            self.end_headers()
            return self.wfile.write(b"Bad Request")
        if session not in Legacy.sessions:
            return self.send(404, {"jsonrpc": "2.0", "id": m.get("id"), "error": {"code": -32001, "message": "Session not found"}})
        if "id" not in m:
            self.send_response(202)
            self.send_header("content-length", "0")
            return self.end_headers()
        if m["method"] == "tools/list":
            return self.send(200, {"jsonrpc": "2.0", "id": m["id"], "result": {"tools": [WEATHER]}})
        self.send(200, {"jsonrpc": "2.0", "id": m["id"], "result": {"content": [{"type": "text", "text": "Sun in " + m["params"]["arguments"]["place"]}]}})


@pytest.fixture
def modern():
    Modern.seen, Modern.versions = [], ["2026-07-28"]
    httpd, url = serve(Modern)
    yield url
    httpd.shutdown()


@pytest.fixture
def legacy():
    Legacy.seen, Legacy.sessions, Legacy.issued = [], set(), 0
    httpd, url = serve(Legacy)
    yield url
    httpd.shutdown()


# ---------------------------------------------------------------- the client, against a modern server

def test_a_modern_server_gets_no_handshake_and_every_request_carries_its_own_version(modern):
    connection = MCPHttpConnection("wx", modern)
    assert (connection.era, connection.version, connection.describe()) == ("modern", "2026-07-28", "http, modern protocol (2026-07-28)")
    tools = connection.tools()
    assert [t["name"] for t in tools] == ["get_weather", "météo"]                          # the tool with a bad header marking is left out
    assert connection.skipped == [{"tool": "bad_marking", "why": "x-mcp-header on 'ratio' needs a string, integer or boolean parameter"}]
    assert connection.call("get_weather", {"place": "Oslo", "region": "eu-north"}) == "Rain in Oslo"      # answered as a stream: progress, then the result
    assert [m["method"] for m in Modern.seen] == ["server/discover", "tools/list", "tools/call"]          # no initialize, no session
    call = Modern.seen[-1]
    assert call["headers"]["mcp-protocol-version"] == "2026-07-28" and call["headers"]["mcp-method"] == "tools/call"
    assert call["headers"]["mcp-name"] == "get_weather" and call["headers"]["mcp-param-region"] == "eu-north"
    assert "application/json" in call["headers"]["accept"] and "text/event-stream" in call["headers"]["accept"]
    assert call["params"]["_meta"][f"{META}protocolVersion"] == "2026-07-28" and call["params"]["_meta"][f"{META}clientInfo"]["name"] == "loopwright"
    assert "mcp-session-id" not in call["headers"] and all("mcp-param-region" not in m["headers"] for m in Modern.seen[:2])

    assert connection.call("météo", {}) == '{"ok": true}'                                 # structured content when there is no text
    assert Modern.seen[-1]["headers"]["mcp-name"] == "=?base64?bcOpdMOpbw==?="            # a name that is not plain ASCII travels encoded
    with pytest.raises(ToolError, match="asked this client for input"):
        connection.call("get_weather", {"place": "ask me"})


def test_a_version_the_server_does_not_speak_is_renegotiated_not_abandoned(modern):
    Modern.versions = ["2027-01-15"]                                                       # a later revision only
    connection = MCPHttpConnection("wx", modern)
    assert connection.era == "modern" and connection.version == "2027-01-15"               # read from the error's `supported` list
    Modern.versions = ["2026-07-28"]                                                       # the server is rolled back while we are connected
    assert connection.call("get_weather", {"place": "Oslo"}) == "Rain in Oslo" and connection.version == "2026-07-28"
    assert pick_version(["2025-11-25", "2026-07-28", "2027-01-15"]) == "2026-07-28"
    with pytest.raises(ToolError, match="speaks only 2025-06-18"):
        pick_version(["2025-06-18"])


def test_header_values_and_markings_follow_the_rules():
    assert [header_value(v) for v in ("us-west1", "Hello, 世界", " padded ", "line1\nline2", "=?base64?literal?=", 42, True)] == [
        "us-west1", "=?base64?SGVsbG8sIOS4lueVjA==?=", "=?base64?IHBhZGRlZCA=?=", "=?base64?bGluZTEKbGluZTI=?=",
        "=?base64?PT9iYXNlNjQ/bGl0ZXJhbD89?=", "42", "true"]                              # the specification's own examples
    nested = {"type": "object", "properties": {"where": {"type": "object", "properties": {"zone": {"type": "integer", "x-mcp-header": "Zone"}}}}}
    assert header_params(nested) == {("where", "zone"): "Zone"}
    for bad in ({"properties": {"a": {"type": "array", "items": {"type": "string", "x-mcp-header": "A"}}}},
                {"properties": {"a": {"type": "string", "x-mcp-header": "Not A Token"}}},
                {"properties": {"a": {"type": "string", "x-mcp-header": "X"}, "b": {"type": "string", "x-mcp-header": "x"}}},
                {"oneOf": [{"properties": {"a": {"type": "string", "x-mcp-header": "A"}}}]}):
        with pytest.raises(ValueError):
            header_params(bad)


# ---------------------------------------------------------------- the client, against an older server

def test_an_older_server_gets_the_handshake_and_its_session_is_kept_and_renewed(legacy):
    connection = MCPHttpConnection("old", legacy)
    assert (connection.era, connection.version) == ("legacy", "2025-11-25")
    assert [m.get("method") for m in Legacy.seen] == ["server/discover", "initialize", "notifications/initialized"]      # tried the modern way first
    assert connection.call("get_weather", {"place": "Lima"}) == "Sun in Lima"
    call = Legacy.seen[-1]
    assert call["headers"]["mcp-session-id"] == "session-1" and call["headers"]["mcp-protocol-version"] == "2025-11-25"
    assert "_meta" not in call["params"] and "mcp-method" not in call["headers"]           # the older shape, as that revision defines it
    Legacy.sessions.clear()                                                                # the server restarts and forgets us
    assert connection.call("get_weather", {"place": "Lima"}) == "Sun in Lima" and Legacy.seen[-1]["headers"]["mcp-session-id"] == "session-2"


def test_addresses_and_tokens_are_handled_carefully(modern, monkeypatch):
    with pytest.raises(ToolError, match="plain http is only allowed for a server on this machine"):
        MCPHttpConnection("far", "http://example.com/mcp")
    with pytest.raises(ToolError, match="not an http"):
        MCPHttpConnection("odd", "ftp://example.com/mcp")
    with pytest.raises(ToolError, match="set the environment variable WX_TOKEN"):
        MCPHttpConnection("wx", modern, {"Authorization": "Bearer ${WX_TOKEN}"})
    monkeypatch.setenv("WX_TOKEN", "from-the-environment")
    MCPHttpConnection("wx", modern, {"Authorization": "Bearer ${WX_TOKEN}"})
    assert Modern.seen[-1]["headers"]["authorization"] == "Bearer from-the-environment"    # the file holds the name, never the value
    with pytest.raises(TransientToolError, match="cannot be reached"):
        MCPHttpConnection("gone", "http://127.0.0.1:9/mcp")


# ---------------------------------------------------------------- stdio: the same two eras, without status codes

OLD_STDIO = r'''
import json, sys
strict = "--silent" not in sys.argv
ready = False
for line in sys.stdin:
    m = json.loads(line)
    if "id" not in m:
        continue
    if m["method"] == "initialize":
        ready = True
        reply = {"result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "old", "version": "1"}}}
    elif not ready:
        if not strict:
            continue                      # some older servers say nothing at all before the handshake
        reply = {"error": {"code": -32601, "message": "Method not found"}}
    elif m["method"] == "tools/list":
        reply = {"result": {"tools": [{"name": "echo", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}]}}
    else:
        reply = {"result": {"content": [{"type": "text", "text": "echo: " + m["params"]["arguments"]["text"] + (" (with _meta)" if "_meta" in m["params"] else "")}]}}
    print(json.dumps({"jsonrpc": "2.0", "id": m["id"], **reply}), flush=True)
'''


@pytest.mark.parametrize("silent", [False, True])
def test_over_stdio_an_older_server_is_recognised_by_its_error_or_by_its_silence(tmp_path, monkeypatch, silent):
    script = tmp_path / "old_server.py"
    script.write_text(OLD_STDIO)
    monkeypatch.setattr(mcp_client, "PROBE_S", 0.5)
    connection = MCPConnection("old", sys.executable, [str(script), *(["--silent"] if silent else [])])
    assert (connection.era, connection.version, connection.transport) == ("legacy", "2025-06-18", "stdio")
    assert [t["name"] for t in connection.tools()] == ["echo"] and connection.call("echo", {"text": "hi"}) == "echo: hi"
    connection.close()


def test_over_stdio_loopwrights_own_server_is_modern_and_still_answers_the_handshake():
    connection = MCPConnection("lw", sys.executable, ["-m", "loopwright", "mcp"])
    assert (connection.era, connection.version) == ("modern", "2026-07-28")
    assert "safe to repeat" in connection.call("loopwright_explain", {"term": "idempotency"})
    connection.close()


# ---------------------------------------------------------------- the server, over HTTP

@pytest.fixture
def own(monkeypatch):
    monkeypatch.delenv("LOOPWRIGHT_MCP_TOKEN", raising=False)
    httpd = serve_http(port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/mcp"
    httpd.shutdown()


def post(url, message, headers=None, method="POST"):
    request = urllib.request.Request(url, data=json.dumps(message).encode() if message is not None else None, method=method,
                                     headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, json.loads(raw) if raw else None


def modern_call(method, params=None, version="2026-07-28", rid=1):
    return {"jsonrpc": "2.0", "id": rid, "method": method, "params": {**(params or {}), "_meta": {f"{META}protocolVersion": version}}}


def test_the_server_answers_the_stateless_protocol_over_http(own):
    ok = {"MCP-Protocol-Version": "2026-07-28"}
    status, reply = post(own, modern_call("server/discover"), {**ok, "Mcp-Method": "server/discover"})
    assert status == 200 and reply["result"]["supportedVersions"] == ["2026-07-28"] and reply["result"]["resultType"] == "complete"
    assert reply["result"]["_meta"][f"{META}serverInfo"]["name"] == "loopwright"
    status, reply = post(own, modern_call("tools/list"), {**ok, "Mcp-Method": "tools/list"})
    assert status == 200 and reply["result"]["cacheScope"] == "public" and reply["result"]["ttlMs"] > 0 and len(reply["result"]["tools"]) == 6
    call = modern_call("tools/call", {"name": "loopwright_explain", "arguments": {"term": "harness"}})
    status, reply = post(own, call, {**ok, "Mcp-Method": "tools/call", "Mcp-Name": "loopwright_explain"})
    assert status == 200 and "ordinary code around a model" in reply["result"]["content"][0]["text"] and reply["result"]["resultType"] == "complete"

    # what the headers say must be what the body says
    for headers in ({**ok, "Mcp-Method": "tools/call"}, {**ok, "Mcp-Method": "tools/call", "Mcp-Name": "loopwright_ask"},
                    {**ok, "Mcp-Method": "tools/list", "Mcp-Name": "loopwright_explain"}, {"Mcp-Method": "tools/call", "Mcp-Name": "loopwright_explain"}):
        status, reply = post(own, call, headers)
        assert status == 400 and reply["error"]["code"] == -32020 and reply["id"] == 1
    status, reply = post(own, modern_call("server/discover", version="2031-01-01"), {"MCP-Protocol-Version": "2031-01-01", "Mcp-Method": "server/discover"})
    assert status == 400 and reply["error"]["code"] == -32022 and reply["error"]["data"] == {"supported": ["2026-07-28"], "requested": "2031-01-01"}
    status, reply = post(own, modern_call("ping"), {**ok, "Mcp-Method": "ping"})
    assert status == 404 and reply["error"]["code"] == -32601                              # ping left the protocol in this revision


def test_the_http_server_guards_its_door(own, monkeypatch):
    discover, headers = modern_call("server/discover"), {"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "server/discover"}
    assert post(own, discover, {**headers, "Origin": "https://evil.example"})[0] == 403     # a page elsewhere cannot drive a local server
    assert post(own, discover, {**headers, "Origin": "http://localhost:5173"})[0] == 200
    monkeypatch.setenv("LOOPWRIGHT_MCP_ORIGINS", "https://tools.example")
    assert post(own, discover, {**headers, "Origin": "https://tools.example"})[0] == 200
    assert post(own, None, method="GET")[0] == 405 and post(own, None, method="DELETE")[0] == 405      # no stream to open, no session to end
    assert post(own, {"jsonrpc": "2.0", "method": "notifications/initialized"})[0] == 202
    assert post(own.replace("/mcp", "/elsewhere"), discover, headers)[0] == 404
    monkeypatch.setenv("LOOPWRIGHT_MCP_TOKEN", "t0ken-for-the-test")
    assert post(own, discover, headers)[0] == 401
    assert post(own, discover, {**headers, "Authorization": "Bearer t0ken-for-the-test"})[0] == 200


def test_an_older_client_can_still_open_with_initialize_over_http(own):
    status, reply = post(own, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}}})
    assert status == 200 and reply["result"]["protocolVersion"] == "2025-06-18" and "resultType" not in reply["result"]
    status, reply = post(own, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, {"MCP-Protocol-Version": "2025-06-18"})
    assert status == 200 and len(reply["result"]["tools"]) == 6 and "ttlMs" not in reply["result"]
    assert post(own, {"jsonrpc": "2.0", "id": 3, "method": "ping"})[1]["result"] == {}


# ---------------------------------------------------------------- end to end: the agent borrows a tool over HTTP

def test_the_agent_borrows_a_tool_over_http_asks_first_and_fences_what_comes_back(own, tmp_path, fake_agent):
    add_mcp_server(tmp_path, "Handbook", own, [])                                          # an address instead of a command
    assert json.loads((tmp_path / "mcp.json").read_text())["servers"]["handbook"] == {"url": own}
    asked = []
    agent = Agent(home=tmp_path)
    row = agent.mcp_status[0]
    assert row["ok"] and (row["transport"], row["era"], row["protocol_version"]) == ("http", "modern", "2026-07-28")
    tool = agent.registry.get("handbook__loopwright_explain")
    assert tool.untrusted and tool.effect == "external"                                    # a stranger's tool: approval by default

    def approve(request):
        asked.append(request["tool"])
        return True

    result = agent.run_turn("Use handbook__loopwright_explain for idempotency", options=Options(provider="scripted", fast=True), approver=approve)
    kinds = result.kinds()
    step = next(e for e in result.events if e.kind == "mcp.call")
    assert asked == ["handbook__loopwright_explain"] and step.data == {"server": "handbook", "tool": "loopwright_explain", "transport": "http",
                                                                        "era": "modern", "protocol_version": "2026-07-28"}
    assert kinds.index("human.decision") < kinds.index("mcp.call") < kinds.index("guard.tool_result") and "safe to repeat" in result.reply
    declined = agent.run_turn("Use handbook__loopwright_explain for idempotency", session="b", options=Options(provider="scripted", fast=True),
                              approver=lambda request: False)
    assert "mcp.call" not in declined.kinds()
    missing = agent.run_turn("Use nowhere__nothing for a test", session="c", options=Options(provider="scripted", fast=True))
    assert "no tool named `nowhere__nothing`" in missing.reply
