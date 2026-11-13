"""Model providers (wire formats, the Claude Code adapter) and the tools."""
import json
import subprocess
import sys
import types

import pytest

from loopwright.config import Options
from loopwright.events import Recorder
from loopwright.memory.store import Store
from loopwright.models import anthropic_api, claude_code, openai_compat
from loopwright.models.base import FatalModelError, TransientModelError
from loopwright.models.gateway import pick_provider
from loopwright.tools import builtin, web_search
from loopwright.tools.plugins import add_mcp_server, load_user_tools, remove_mcp_server, set_tool_enabled
from loopwright.tools.registry import Registry, ToolContext, ToolError, validate

CONVERSATION = [
    {"role": "user", "content": "book it"},
    {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "name": "clock", "arguments": {}}, {"id": "t2", "name": "recall", "arguments": {"query": "alex"}}]},
    {"role": "tool", "tool_call_id": "t1", "name": "clock", "content": "{}", "is_error": False},
    {"role": "tool", "tool_call_id": "t2", "name": "recall", "content": "boom", "is_error": True},
]
TOOLS = [{"name": "clock", "description": "time", "parameters": {"type": "object", "properties": {}}}]


def test_anthropic_wire_format_and_prompt_caching(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    sent = {}

    def transport(url, headers, body):
        sent.update(url=url, headers=headers, body=body)
        return {"model": "m", "stop_reason": "tool_use", "usage": {"input_tokens": 100, "output_tokens": 7, "cache_read_input_tokens": 900},
                "content": [{"type": "text", "text": "ok"}, {"type": "tool_use", "id": "x", "name": "clock", "input": {}}]}

    response = anthropic_api.AnthropicProvider("m", transport).complete("SYS", CONVERSATION, TOOLS)
    body = sent["body"]
    assert sent["headers"]["x-api-key"] == "k" and body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]          # two tool results merged into one message
    assert [b["type"] for b in body["messages"][2]["content"]] == ["tool_result", "tool_result"] and body["messages"][2]["content"][1]["is_error"]
    assert body["tools"][0]["input_schema"] == TOOLS[0]["parameters"]
    assert response.tool_calls[0].name == "clock" and (response.usage.tokens_in, response.usage.cached_in) == (1000, 900)
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(FatalModelError):
        anthropic_api.AnthropicProvider("m", transport).complete("SYS", CONVERSATION, TOOLS)


def test_openai_compatible_wire_format(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    sent = {}

    def transport(url, headers, body):
        sent.update(url=url, body=body)
        return {"choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "clock", "arguments": "{}"}},
            {"id": "b", "type": "function", "function": {"name": "clock", "arguments": "{not json"}}]}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3}}

    response = openai_compat.OpenAICompatProvider("local-model", transport).complete("SYS", CONVERSATION, TOOLS)
    assert sent["url"] == "http://localhost:11434/v1/chat/completions" and "max_tokens" in sent["body"]
    assert [m["role"] for m in sent["body"]["messages"]] == ["system", "user", "assistant", "tool", "tool"]
    assert json.loads(sent["body"]["messages"][2]["tool_calls"][1]["function"]["arguments"]) == {"query": "alex"}
    assert response.tool_calls[1].arguments == {"_raw": "{not json"}          # malformed arguments survive for the gate to reject
    with pytest.raises(FatalModelError, match="LOOPWRIGHT_MODEL"):
        openai_compat.OpenAICompatProvider("", transport).complete("SYS", [], [])


def _claude(stdout="", code=0, stderr=""):
    calls = []

    def runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return types.SimpleNamespace(stdout=stdout, stderr=stderr, returncode=code)
    return runner, calls


def test_claude_code_adapter_turns_the_cli_into_a_plain_model(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOPWRIGHT_CLAUDE_BIN", "/fake/claude")
    reply = {"result": '```json\n{"thought": "need the time", "tool_calls": [{"name": "clock", "arguments": {}}]}\n```',
             "is_error": False, "total_cost_usd": 0.01, "usage": {"input_tokens": 50, "output_tokens": 9, "cache_read_input_tokens": 400}}
    runner, calls = _claude(json.dumps(reply))
    response = claude_code.ClaudeCodeProvider("sonnet", runner, tmp_path).complete("SYS", CONVERSATION, TOOLS)
    cmd, kwargs = calls[0]
    assert cmd[:2] == ["/fake/claude", "-p"] and cmd[cmd.index("--tools") + 1] == "" and "--no-session-persistence" in cmd
    system = cmd[cmd.index("--system-prompt") + 1]
    assert system.startswith("SYS") and "clock: time" in system and '"tool_calls"' in system
    assert "<tool_result tool=\"recall\" id=\"t2\" error=\"true\">" in kwargs["input"] and kwargs["cwd"].endswith("claude-code-cwd")
    assert response.tool_calls[0].name == "clock" and response.thought == "need the time"
    assert (response.usage.tokens_in, response.usage.tokens_out, response.cost_usd) == (450, 9, 0.01)


def test_claude_code_adapter_handles_prose_auth_errors_and_missing_cli(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOPWRIGHT_CLAUDE_BIN", "/fake/claude")
    prose, _ = _claude(json.dumps({"result": "Paris is the capital of France.", "is_error": False}))
    assert claude_code.ClaudeCodeProvider("sonnet", prose, tmp_path).complete("S", [{"role": "user", "content": "q"}], []).text == "Paris is the capital of France."
    final = claude_code.parse_reply('{"thought": "t", "final": "The answer is **4**."}')
    assert final == ("The answer is **4**.", [], "t")
    unauth, _ = _claude(json.dumps({"result": "Authentication error", "is_error": True}))
    with pytest.raises(FatalModelError, match="not signed in"):
        claude_code.ClaudeCodeProvider("sonnet", unauth, tmp_path).complete("S", [], [])
    overloaded, _ = _claude(json.dumps({"result": "API Error: 529 overloaded", "is_error": True}))
    with pytest.raises(TransientModelError):
        claude_code.ClaudeCodeProvider("sonnet", overloaded, tmp_path).complete("S", [], [])
    monkeypatch.setenv("LOOPWRIGHT_CLAUDE_BIN", "")
    monkeypatch.setattr(claude_code.shutil, "which", lambda name: None)
    with pytest.raises(FatalModelError, match="not found"):
        claude_code.ClaudeCodeProvider("sonnet", prose, tmp_path).complete("S", [], [])
    assert pick_provider("auto") == "scripted"                                  # nothing set up: fall back to the built-in model


def test_a_claude_code_turn_end_to_end_through_the_harness(monkeypatch, tmp_path):
    """The whole agent on the Claude Code provider, with the CLI replaced by a script of JSON replies."""
    from loopwright.harness import Agent
    monkeypatch.setenv("LOOPWRIGHT_CLAUDE_BIN", "/fake/claude")
    replies = iter(['{"thought": "compute", "tool_calls": [{"name": "calculator", "arguments": {"expression": "0.18 * 2450"}}]}',
                    '{"thought": "done", "final": "18% of 2450 is **441**."}'])
    runner = lambda cmd, **kw: types.SimpleNamespace(stdout=json.dumps({"result": next(replies), "is_error": False}), stderr="", returncode=0)   # noqa: E731
    agent = Agent(home=tmp_path, providers=[claude_code.ClaudeCodeProvider("sonnet", runner, tmp_path)])
    result = agent.run_turn("What's 18% of 2450?", options=Options(fast=True))
    assert result.reply == "18% of 2450 is **441**." and [t["name"] for t in result.tool_log] == ["calculator"]
    assert result.receipt["provider"] == "claude-code" and result.receipt["model_calls"] == 2


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(Store(tmp_path), tmp_path, Recorder(), Options(search="demo"))


def test_calculator_evaluates_arithmetic_and_nothing_else(ctx):
    assert builtin.calculator({"expression": "0.18 * 2450"}, ctx)["result"] == 441
    assert builtin.calculator({"expression": "sqrt(16) + 2^3"}, ctx)["result"] == 12
    for attack in ("__import__('os').system('id')", "open('/etc/passwd').read()", "(1).__class__", "9**9**9"):
        with pytest.raises(ToolError):
            builtin.calculator({"expression": attack}, ctx)


def test_read_file_cannot_leave_the_workspace_and_fetch_url_refuses_private_addresses(ctx, tmp_path):
    (tmp_path / "secret.txt").write_text("outside")
    assert "only files read_file can open" in builtin.read_file({"path": "welcome.txt"}, ctx)
    for path in ("../secret.txt", "/etc/passwd", "a/../../secret.txt"):
        with pytest.raises(ToolError, match="outside the workspace"):
            builtin.read_file({"path": path}, ctx)
    for url in ("http://127.0.0.1/admin", "http://169.254.169.254/latest/meta-data/", "http://localhost:8765/api/memory", "http://10.0.0.5/"):
        with pytest.raises(ToolError, match="non-public"):
            builtin.fetch_url({"url": url}, ctx)
    with pytest.raises(ToolError):
        builtin.fetch_url({"url": "file:///etc/passwd"}, ctx)
    assert "idempotency key" in builtin.fetch_url({"url": "demo://release-notes"}, ctx)


def test_schema_validation_reports_each_problem():
    schema = {"type": "object", "required": ["a"], "additionalProperties": False,
              "properties": {"a": {"type": "string"}, "n": {"type": "integer"}, "e": {"enum": ["x", "y"]}, "l": {"type": "array", "items": {"type": "string"}}}}
    assert validate(schema, {"a": "ok", "n": 3, "e": "x", "l": ["s"]}) == []
    errors = validate(schema, {"n": "3", "e": "z", "l": [1], "zzz": 1})
    assert len(errors) == 5 and any("a is required" in e for e in errors) and any("zzz is not a known" in e for e in errors)
    assert validate({"type": "integer"}, True)                                 # a boolean is not an integer


DDG_PAGE = '''<div class="result"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fevals&amp;rut=1">Agent <b>evals</b> guide</a>
<a class="result__snippet" href="x">Start with <b>twenty</b> cases &amp; grow.</a></div>
<div class="result"><a rel="nofollow" class="result__a" href="https://example.com/b">Second</a></div>'''


def test_web_search_backends_share_one_shape(ctx, monkeypatch):
    assert web_search.parse_duckduckgo(DDG_PAGE) == [
        {"title": "Agent evals guide", "url": "https://example.org/evals", "snippet": "Start with twenty cases & grow."},
        {"title": "Second", "url": "https://example.com/b", "snippet": ""}]
    monkeypatch.setenv("TAVILY_API_KEY", "t")
    tavily = web_search.search_tavily("q", get=lambda url, **kw: json.dumps({"results": [{"title": "T", "url": "https://t.example", "content": "c"}]}))
    monkeypatch.setenv("BRAVE_API_KEY", "b")
    brave = web_search.search_brave("q", get=lambda url, **kw: json.dumps({"web": {"results": [{"title": "B", "url": "https://b.example", "description": "<b>d</b>"}]}}))
    assert tavily == [{"title": "T", "url": "https://t.example", "snippet": "c"}] and brave[0]["snippet"] == "d"
    assert web_search.pick_backend("auto") == "tavily" and web_search.pick_backend("demo") == "demo"
    monkeypatch.setenv("LOOPWRIGHT_CLAUDE_BIN", "/fake/claude")
    runner = lambda cmd, **kw: types.SimpleNamespace(stdout=json.dumps({"result": 'Here: [{"title": "C", "url": "https://c.example", "snippet": "s"}]'}), returncode=0)   # noqa: E731
    assert web_search.search_claude_code("q", runner=runner)[0]["url"] == "https://c.example"
    found = json.loads(web_search.web_search({"query": "agent evaluation"}, ctx))
    assert found["backend"] == "demo" and found["results"][0]["url"] == "demo://agent-evals-guide"
    assert web_search.web_search_tool().untrusted is True


def test_your_own_tools_load_from_the_state_folder_and_can_be_switched_off(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "dice.py").write_text("from loopwright.tools.registry import Tool\nTOOLS=[Tool('roll_dice','Roll.',{'type':'object','properties':{}},lambda a,c:{'rolled':4})]\n")
    (tmp_path / "tools" / "broken.py").write_text("raise RuntimeError('nope')\n")
    registry = Registry()
    status = {row["file"]: row for row in load_user_tools(tmp_path, registry)}
    assert status["dice.py"]["tools"] == ["roll_dice"] and not status["broken.py"]["ok"] and registry.get("roll_dice")
    from loopwright.harness import Agent
    set_tool_enabled(tmp_path, "send_message", False)
    agent = Agent(home=tmp_path)
    result = agent.run_turn("Send a message to Priya: hi", options=Options(provider="scripted", fast=True, approvals="approve"))
    assert "no tool named 'send_message'" in result.reply and agent.store.outbox() == []


def test_mcp_client_borrows_tools_from_a_server_here_loopwrights_own(tmp_path):
    from loopwright.harness import Agent
    add_mcp_server(tmp_path, "Lw!", sys.executable, ["-m", "loopwright", "mcp"], trust="read")
    agent = Agent(home=tmp_path)
    assert agent.mcp_status[0]["ok"] and "lw__loopwright_explain" in agent.registry.names()
    tool = agent.registry.get("lw__loopwright_explain")
    assert tool.untrusted and tool.effect == "read"                              # borrowed output is always fenced
    assert "safe to repeat" in tool.fn({"term": "idempotency"}, None)
    with pytest.raises(ValueError):
        add_mcp_server(tmp_path, "", "", [])
    assert remove_mcp_server(tmp_path, "lw") and not remove_mcp_server(tmp_path, "lw")
    add_mcp_server(tmp_path, "dead", "/no/such/binary", [])
    assert Agent(home=tmp_path / "x").mcp_status == [] and not Agent(home=tmp_path).mcp_status[0]["ok"]   # a dead server is reported, not fatal


def test_mcp_server_speaks_the_protocol_over_stdio():
    requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2099-01-01"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "loopwright_ask", "arguments": {"message": "What's 2 + 2?"}}},
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "nope", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 5, "method": "resources/list"}]
    out = subprocess.run([sys.executable, "-m", "loopwright", "mcp"], input="\n".join(json.dumps(r) for r in requests) + "\nnot json\n",
                         capture_output=True, text=True, timeout=60).stdout
    replies = {r["id"]: r for r in map(json.loads, out.strip().splitlines())}
    assert len(replies) == 6                                                    # the notification got no reply; the bad line got an error
    assert replies[1]["result"]["protocolVersion"] == "2025-06-18" and replies[1]["result"]["serverInfo"]["name"] == "loopwright"
    assert {t["name"] for t in replies[2]["result"]["tools"]} >= {"loopwright_ask", "loopwright_explain", "loopwright_trace"}
    assert "2 + 2 = **4**" in replies[3]["result"]["content"][0]["text"] and "[tools] Ran calculator" in replies[3]["result"]["content"][0]["text"]
    assert replies[4]["error"]["code"] == -32602 and replies[5]["error"]["code"] == -32601 and replies[None]["error"]["code"] == -32700
