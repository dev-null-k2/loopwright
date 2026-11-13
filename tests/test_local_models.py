"""Local open-source models: a stub server that behaves like Ollama, LM Studio or llama.cpp, spoken to over real HTTP."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from loopwright.config import Options
from loopwright.events import Recorder
from loopwright.harness import Agent
from loopwright.models import local
from loopwright.models.base import FatalModelError, TransientModelError
from loopwright.models.gateway import ModelGateway, available_providers, pick_provider

CALL = {"name": "calculator", "arguments": {"expression": "0.18 * 2450"}}


class Stub(BaseHTTPRequestHandler):
    """Three kinds of local model, chosen by name:
    native-model    supports tool calling properly
    chatty-model    accepts tools but writes the call as JSON in its text
    plain-model     its server rejects any request that carries tools"""
    requests: list = []

    def log_message(self, *args):
        pass

    def _send(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._send(200, {"data": [{"id": "native-model"}, {"id": "nomic-embed-text"}, {"id": "chatty-model"}, {"id": "plain-model"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        Stub.requests.append(body)
        model, text = body["model"], json.dumps(body["messages"])
        done = '"role": "tool"' in text or "<tool_result" in text
        if model == "plain-model" and "tools" in body:
            return self._send(400, {"error": {"message": "registry.ollama.ai/library/plain-model does not support tools"}})
        if model == "native-model" and not done:
            message = {"content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": CALL["name"], "arguments": json.dumps(CALL["arguments"])}}]}
        elif not done:
            message = {"content": "```json\n" + json.dumps({"thought": "compute", "tool_calls": [CALL]}) + "\n```"}
        elif model == "native-model" or "tools" in body:
            message = {"content": "18% of 2450 is 441."}
        else:
            message = {"content": json.dumps({"thought": "done", "final": "18% of 2450 is 441."})}
        self._send(200, {"model": model, "choices": [{"message": message, "finish_reason": "stop"}], "usage": {"prompt_tokens": 40, "completion_tokens": 8}})


@pytest.fixture
def server(monkeypatch):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setenv("LOOPWRIGHT_LOCAL_URL", f"http://127.0.0.1:{httpd.server_address[1]}/v1")
    local._found.update(at=0.0, value=None)
    Stub.requests = []
    yield httpd
    httpd.shutdown()


def test_a_running_local_server_is_found_and_becomes_the_default_with_no_keys(server):
    found = local.detect()
    assert found["models"] == ["native-model", "chatty-model", "plain-model"]          # embedding models are not chat models
    status = available_providers()["local"]
    assert status["ok"] and "native-model" in status["note"]
    assert pick_provider("auto") in ("local", "claude-code")                             # local wins unless a Claude CLI is installed


@pytest.mark.parametrize("model, mode", [("native-model", "native"), ("chatty-model", "recovered"), ("plain-model", "text")])
def test_a_full_turn_on_each_kind_of_local_model(server, tmp_path, model, mode):
    agent = Agent(home=tmp_path)
    result = agent.run_turn("What's 18% of 2450?", options=Options(provider="local", model=model, fast=True, fallback=False))
    assert result.ok and "441" in result.reply and [t["name"] for t in result.tool_log] == ["calculator"]
    assert result.receipt["provider"] == "local" and result.receipt["cost_usd"] == 0.0 and not result.receipt["estimated"]
    if mode == "text":
        assert model in local._text_only
        last = Stub.requests[-1]
        assert "tools" not in last and "calculator:" in last["messages"][0]["content"] and "<tool_result" in last["messages"][1]["content"]
        before = len(Stub.requests)
        agent.run_turn("What's 18% of 2450?", session="again", options=Options(provider="local", model=model, fast=True, fallback=False))
        assert all("tools" not in r for r in Stub.requests[before:])                    # it remembered; no second refused request
    else:
        assert "tools" in Stub.requests[0] and model not in local._text_only


def test_the_first_installed_model_is_used_when_none_is_named(server, tmp_path):
    result = Agent(home=tmp_path).run_turn("What's 18% of 2450?", options=Options(provider="local", fast=True, fallback=False))
    assert result.receipt["model"] == "native-model"


def test_no_local_server_is_a_clear_error_not_a_hang():
    assert local.detect(refresh=True) is None and not available_providers()["local"]["ok"]
    with pytest.raises(FatalModelError, match="no local model server is running"):
        local.LocalProvider().complete("s", [{"role": "user", "content": "hi"}], [])


def test_when_the_network_model_is_unreachable_the_local_model_answers(server, tmp_path):
    """The in-flight case: the cloud provider times out, and the chain falls back to the model on this machine."""
    class Offline:
        name, model = "anthropic", "cloud"

        def complete(self, *args, **kwargs):
            raise TransientModelError("network error: no route to host")

    gateway = ModelGateway(Options(fast=True, retries=1), Recorder(), providers=[Offline(), local.LocalProvider("native-model")])
    response = gateway.call("s", [{"role": "user", "content": "What's 18% of 2450?"}],
                            [{"name": "calculator", "description": "math", "parameters": {"type": "object", "properties": {}}}])
    assert response.provider == "local" and response.tool_calls[0].name == "calculator"
    assert [e.kind for e in gateway.rec.events][:3] == ["model.retry", "model.exhausted", "model.fallback"]
    built = ModelGateway(Options(provider="anthropic", fast=True), Recorder())
    assert [p.name for p in built.chain] == ["anthropic", "local", "scripted"]             # the default chain includes it too
