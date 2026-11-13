"""Any OpenAI-compatible chat completions endpoint.

Covers OpenAI itself and everything that copies its API: OpenRouter, Groq,
Together, and local servers such as Ollama, LM Studio and vLLM.

    OPENAI_API_KEY=...            (any non-empty value for a local server)
    OPENAI_BASE_URL=http://localhost:11434/v1     (optional)
    LOOPWRIGHT_MODEL=<model id>   (required: there is no safe default)
"""

from __future__ import annotations

import json
import os
from typing import Any

from loopwright.models._http import post_json
from loopwright.models.base import FatalModelError, Message, ModelResponse, ToolCall, Usage


def to_wire(system: str, messages: list[Message]) -> list[dict[str, Any]]:
    wire: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        if m["role"] == "assistant":
            row: dict[str, Any] = {"role": "assistant", "content": m.get("content") or None}
            if m.get("tool_calls"):
                row["tool_calls"] = [{"id": c["id"], "type": "function",
                                      "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                                     for c in m["tool_calls"]]
            wire.append(row)
        elif m["role"] == "tool":
            wire.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
        else:
            wire.append({"role": "user", "content": m["content"]})
    return wire


def from_wire(data: dict[str, Any], model: str) -> ModelResponse:
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message", {})
    calls = []
    for c in message.get("tool_calls") or []:
        try:
            arguments = json.loads(c["function"].get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {"_raw": c["function"].get("arguments")}   # the tool gate will reject it with a clear error
        calls.append(ToolCall(c.get("id", f"call_{len(calls)}"), c["function"]["name"], arguments))
    usage = data.get("usage", {})
    return ModelResponse(text=message.get("content") or "", tool_calls=calls, provider="openai",
                         model=data.get("model", model), stop_reason=choice.get("finish_reason", "stop"),
                         usage=Usage(usage.get("prompt_tokens", 0) or 0, usage.get("completion_tokens", 0) or 0))


class OpenAICompatProvider:
    name = "openai"

    def __init__(self, model: str = "", transport=post_json):
        self.model = model or os.environ.get("LOOPWRIGHT_MODEL") or os.environ.get("OPENAI_MODEL", "")
        self._post = transport

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]],
                 *, max_tokens: int = 2048, purpose: str = "loop") -> ModelResponse:
        key = os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise FatalModelError("OPENAI_API_KEY is not set")
        if not self.model:
            raise FatalModelError("set LOOPWRIGHT_MODEL to the model id your endpoint serves")
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        # OpenAI's own API renamed the limit; most compatible servers kept the old name
        limit = "max_completion_tokens" if "api.openai.com" in base else "max_tokens"
        body: dict[str, Any] = {"model": self.model, "messages": to_wire(system, messages), limit: max_tokens}
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
        data = self._post(f"{base}/chat/completions", {"authorization": f"Bearer {key}"}, body)
        return from_wire(data, self.model)
