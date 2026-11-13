"""Anthropic Messages API, with native tool use and prompt caching.

Needs ANTHROPIC_API_KEY. Plain HTTPS, no SDK.

PROMPT CACHING: the system prompt is marked `cache_control: ephemeral`. Inside
one turn the loop calls the model several times with the same long prefix
(system prompt + tool definitions); from the second call on, that prefix is
read from cache at a fraction of the price and latency. The usage block
reports it as cache_read_input_tokens, which the receipt shows.
"""

from __future__ import annotations

import os
from typing import Any

from loopwright.models._http import post_json
from loopwright.models.base import FatalModelError, Message, ModelResponse, ToolCall, Usage

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"


def to_wire(messages: list[Message]) -> list[dict[str, Any]]:
    """Neutral messages -> Anthropic content blocks. Consecutive tool results
    are merged into one user message, which is what the API expects."""
    wire: list[dict[str, Any]] = []
    for m in messages:
        if m["role"] == "assistant":
            blocks: list[dict[str, Any]] = [{"type": "text", "text": m["content"]}] if m.get("content") else []
            blocks += [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["arguments"]}
                       for c in m.get("tool_calls", []) or []]
            wire.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "(no text)"}]})
        elif m["role"] == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"],
                     "is_error": bool(m.get("is_error"))}
            if wire and wire[-1]["role"] == "user" and isinstance(wire[-1]["content"], list) \
                    and wire[-1]["content"] and wire[-1]["content"][0].get("type") == "tool_result":
                wire[-1]["content"].append(block)
            else:
                wire.append({"role": "user", "content": [block]})
        else:
            wire.append({"role": "user", "content": m["content"]})
    return wire


def from_wire(data: dict[str, Any], model: str) -> ModelResponse:
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    calls = [ToolCall(b["id"], b["name"], b.get("input") or {})
             for b in data.get("content", []) if b.get("type") == "tool_use"]
    usage = data.get("usage", {})
    cached = usage.get("cache_read_input_tokens", 0) or 0
    return ModelResponse(
        text=text, tool_calls=calls, provider="anthropic", model=data.get("model", model),
        stop_reason=data.get("stop_reason", "end_turn"),
        usage=Usage((usage.get("input_tokens", 0) or 0) + cached + (usage.get("cache_creation_input_tokens", 0) or 0),
                    usage.get("output_tokens", 0) or 0, cached))


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str = "", transport=post_json):
        self.model = model or os.environ.get("LOOPWRIGHT_MODEL") or DEFAULT_MODEL
        self._post = transport

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]],
                 *, max_tokens: int = 2048, purpose: str = "loop") -> ModelResponse:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise FatalModelError("ANTHROPIC_API_KEY is not set")
        body: dict[str, Any] = {
            "model": self.model, "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": to_wire(messages),
        }
        if tools:
            body["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                             for t in tools]
        data = self._post(API_URL, {"x-api-key": key, "anthropic-version": "2023-06-01"}, body)
        return from_wire(data, self.model)
