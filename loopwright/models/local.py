"""LOCAL MODELS: any open-source model on your own machine. No internet, no key.

Works with anything that serves the OpenAI chat-completions API on localhost,
which is nearly every local runner:

    Ollama        http://localhost:11434    ollama pull qwen2.5:7b ; ollama serve
    LM Studio     http://localhost:1234     start the local server in the app
    llama.cpp     http://localhost:8080     llama-server -m model.gguf
    vLLM, Jan, LocalAI, ...                 set LOOPWRIGHT_LOCAL_URL

Loopwright finds a running server by itself, lists the models it has, and
uses LOOPWRIGHT_MODEL or picks one for you (see WHICH MODEL below). Download the model before you lose
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


# WHICH MODEL. A machine often has several models downloaded, and they are not
# interchangeable: an agent needs one that can call tools. A vision model or a
# small code-completion model listed first would fail every task. So the pick
# is ranked, not "the first one":
#   1. ask the server what each model can do (Ollama answers this at /api/show
#      with a list such as ["completion", "tools"]) and prefer "tools"
#   2. where the server cannot say, go by the model's family name
#   3. ties keep the server's own order
# `loopwright doctor` shows the pick. Name a model yourself to override it.
TOOL_FAMILIES = ("qwen2.5", "qwen3", "llama3.1", "llama3.2", "llama3.3", "llama4", "mistral", "mixtral", "hermes",
                 "command-r", "firefunction", "granite", "gpt-oss", "nemotron", "devstral", "magistral", "cogito")
NOT_AGENTS = ("minicpm-v", "llava", "bakllava", "moondream", "vision", "embed", "ocr", "whisper", "rerank", "guard")
_caps: dict[str, list[str] | None] = {}     # model -> capabilities; None = the server could not say


def _capabilities(base: str, model: str) -> list[str] | None:
    if model not in _caps:
        try:
            request = urllib.request.Request(f"{base}/api/show", data=json.dumps({"model": model}).encode(),
                                             headers={"content-type": "application/json"})
            with urllib.request.urlopen(request, timeout=1.5) as response:
                found = json.loads(response.read().decode()).get("capabilities")
            _caps[model] = [str(c) for c in found] if isinstance(found, list) else None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            _caps[model] = None
    return _caps[model]


def rank(base: str, models: list[str]) -> tuple[str, str]:
    """-> (the model to use, why). Empty strings when there are no models."""
    best, best_score, why = "", -10 ** 6, ""
    for index, model in enumerate(models[:24]):
        caps, low = _capabilities(base, model), model.lower()
        if caps is not None:
            score = (100 if "tools" in caps else 0) + (0 if "completion" in caps else -200)
            reason = "the server says it supports tool calling" if "tools" in caps else "no model here reports tool calling"
        else:
            score = 50 if any(f in low for f in TOOL_FAMILIES) else 0
            reason = "its family is known to call tools" if score else "nothing better was found"
        if any(marker in low for marker in NOT_AGENTS):
            score -= 150
        score -= index / 100                                   # ties: the server's own order
        if score > best_score:
            best, best_score, why = model, score, reason
    return best, why


def detect(refresh: bool = False) -> dict[str, Any] | None:
    """-> {"name", "url", "models", "preferred", "why"} for the first local server that
    answers, or None. Cached for 20 seconds so the cockpit can ask often."""
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
        models = [m for m in models if m and "embed" not in m.lower()]
        preferred, why = rank(base, models)
        value = {"name": name, "url": base, "models": models, "preferred": preferred, "why": why}
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
            self.model = server["preferred"]
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
