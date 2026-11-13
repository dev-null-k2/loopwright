"""LOCAL MODELS: any open-source model on your own machine. No internet, no key.

Works with anything that serves the OpenAI chat-completions API on localhost,
which is nearly every local runner:

    Ollama        http://localhost:11434    ollama pull qwen2.5:7b ; ollama serve
    LM Studio     http://localhost:1234     start the local server in the app
    llama.cpp     http://localhost:8080     llama-server -m model.gguf
    vLLM, Jan, LocalAI, ...                 set LOOPWRIGHT_LOCAL_URL

Loopwright finds a running server by itself, lists the models it has, and
uses LOOPWRIGHT_MODEL or the first one. Download the model before you lose
the network; after that nothing here needs it.

TOOL CALLING ON SMALL MODELS is the part worth reading. Hosted models have a
native tool-calling channel. Local models vary: some support it, some reject
the request outright, some accept it and write the call as text in the reply.
So this provider degrades in three steps and remembers which one worked:

  1. native    send the tool schemas the standard way
  2. recover   the model answered with JSON in its text: parse that
  3. text      the server refused tools: describe them in the system prompt
               and ask for one JSON object back (the same protocol the Claude
               Code adapter uses)

Expect a small model to be a weaker agent than a hosted one: it will pick the
wrong tool more often and lose the thread on long tasks. The harness around it
(budgets, the tool gate, repeat detection) is what keeps that safe, and the
board shows you exactly where it went wrong.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from loopwright.models import openai_compat
from loopwright.models._http import post_json
from loopwright.models.base import FatalModelError, Message, ModelResponse, TransientModelError, Usage, estimate_tokens
from loopwright.models.claude_code import PROTOCOL, parse_reply, render_transcript

KNOWN_SERVERS = [("Ollama", "http://127.0.0.1:11434"), ("LM Studio", "http://127.0.0.1:1234"),
                 ("llama.cpp", "http://127.0.0.1:8080")]
TIMEOUT_S = 600                 # a laptop can take minutes on a long prompt
_found: dict[str, Any] = {"at": 0.0, "value": None}
_text_only: set[str] = set()    # models whose server refused native tools


def _get(url: str, timeout: float = 0.6) -> dict[str, Any]:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=timeout) as response:
        return json.loads(response.read().decode())


def detect(refresh: bool = False) -> dict[str, Any] | None:
    """-> {"name", "url", "models"} for the first local server that answers,
    or None. Cached for 20 seconds so the cockpit can ask often."""
    if not refresh and time.time() - _found["at"] < 20:
        return _found["value"]
    custom = os.environ.get("LOOPWRIGHT_LOCAL_URL", "").rstrip("/")
    candidates = [("custom", custom[:-3] if custom.endswith("/v1") else custom)] if custom else KNOWN_SERVERS
    value = None
    for name, base in candidates:
        try:
            models = [m.get("id", "") for m in _get(f"{base}/v1/models").get("data", [])]
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            continue
        value = {"name": name, "url": base, "models": [m for m in models if m and "embed" not in m.lower()]}
        break
    _found.update(at=time.time(), value=value)
    return value


class LocalProvider:
    name = "local"

    def __init__(self, model: str = "", transport=post_json):
        self._post = transport
        self._wanted = model or os.environ.get("LOOPWRIGHT_MODEL", "")
        self.model = self._wanted or "local"

    def _server(self) -> dict[str, Any]:
        server = detect()
        if server is None:
            raise FatalModelError("no local model server is running. Start one (for example `ollama serve`), "
                                  "or set LOOPWRIGHT_LOCAL_URL")
        if not self._wanted:
            if not server["models"]:
                raise FatalModelError(f"{server['name']} is running but has no models. Download one first, "
                                      "for example `ollama pull qwen2.5:7b`")
            self.model = server["models"][0]
        return server

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]],
                 *, max_tokens: int = 2048, purpose: str = "loop") -> ModelResponse:
        server = self._server()
        url = f"{server['url']}/v1/chat/completions"
        if tools and self.model not in _text_only:
            body = {"model": self.model, "messages": openai_compat.to_wire(system, messages), "max_tokens": max_tokens,
                    "tools": [{"type": "function", "function": t} for t in tools]}
            try:
                response = openai_compat.from_wire(self._call(url, body), self.model)
            except FatalModelError as exc:
                if "tool" not in str(exc).lower():          # some other 4xx: a real error
                    raise
                _text_only.add(self.model)                  # step 3 from now on, for this model
            else:
                if not response.tool_calls and response.text.lstrip().startswith(("{", "```")):
                    final, calls, thought = parse_reply(response.text)      # step 2: the call arrived as text
                    known = {t["name"] for t in tools}
                    if calls and all(c.name in known for c in calls):
                        response.text, response.tool_calls, response.thought = final, calls, thought
                        response.stop_reason = "tool_use"
                return self._label(response)
        # step 3, and every call with no tools: plain text in, plain text out
        protocol = PROTOCOL.format(tools="\n".join(
            f"- {t['name']}: {t['description']}\n  arguments schema: {json.dumps(t['parameters'])}" for t in tools)) if tools else ""
        prompt = render_transcript(messages) if tools else None
        wire = ([{"role": "system", "content": system + protocol}, {"role": "user", "content": prompt}] if tools
                else openai_compat.to_wire(system, messages))
        data = self._call(url, {"model": self.model, "messages": wire, "max_tokens": max_tokens})
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        final, calls, thought = parse_reply(text) if tools else (text, [], "")
        usage = data.get("usage") or {}
        return self._label(ModelResponse(
            text=final, tool_calls=calls, thought=thought, model=data.get("model", self.model),
            stop_reason="tool_use" if calls else "end_turn",
            usage=Usage(usage.get("prompt_tokens") or estimate_tokens(system + str(prompt or "")),
                        usage.get("completion_tokens") or estimate_tokens(text), estimated=not usage)))

    def _call(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._post(url, {"authorization": "Bearer local"}, body, TIMEOUT_S)
        except TransientModelError as exc:
            if "network error" in str(exc):
                _found["at"] = 0.0                          # the server went away: look again next time
            raise

    def _label(self, response: ModelResponse) -> ModelResponse:
        response.provider, response.cost_usd = self.name, 0.0       # your own hardware: no per-token bill
        return response
