"""Anthropic Messages API, with native tool use and prompt caching.

Needs ANTHROPIC_API_KEY. Plain HTTPS, no SDK.

PROMPT CACHING: the system prompt is marked `cache_control: ephemeral`. Inside
one turn the loop calls the model several times with the same long prefix
(system prompt + tool definitions); from the second call on, that prefix is
read from cache at a fraction of the price and latency. The usage block
reports it as cache_read_input_tokens, which the receipt shows.

STREAMING: with `"stream": true` the same answer arrives as a series of small
events instead of one JSON body. `fold_stream` reads them, passes text on as
it comes, and puts the pieces back together into the body the non-streaming
call would have returned, so everything after it is shared.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from typing import Any

from loopwright.models._http import post_json, post_stream
from loopwright.models.base import FatalModelError, Message, ModelResponse, OnText, ToolCall, TransientModelError, Usage

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


def fold_stream(events: Iterable[dict[str, Any]], on_text: OnText) -> dict[str, Any]:
    """Read a streamed answer and rebuild the whole message from it.

    The stream describes the message block by block:
        message_start            the model name and the input token count
        content_block_start      a block opens: text, or a tool call (id and name)
        content_block_delta      a piece of it: text_delta, or input_json_delta
                                 (a fragment of the tool's arguments, as JSON text)
        content_block_stop
        message_delta            the stop reason and the output token count
        message_stop
    Tool arguments arrive as fragments of JSON that only parse once the block
    is complete, so they are collected as text and parsed at the end."""
    data: dict[str, Any] = {"content": [], "usage": {}}
    blocks: dict[int, dict[str, Any]] = {}
    partial: dict[int, str] = {}
    for event in events:
        kind = event.get("type")
        if kind == "message_start":
            message = event.get("message") or {}
            data["model"] = message.get("model")
            data["usage"].update(message.get("usage") or {})
        elif kind == "content_block_start":
            blocks[event["index"]] = dict(event.get("content_block") or {})
        elif kind == "content_block_delta":
            block, delta = blocks.setdefault(event["index"], {"type": "text", "text": ""}), event.get("delta") or {}
            if delta.get("type") == "text_delta":
                block["text"] = block.get("text", "") + delta.get("text", "")
                on_text(delta.get("text", ""))
            elif delta.get("type") == "input_json_delta":
                partial[event["index"]] = partial.get(event["index"], "") + delta.get("partial_json", "")
        elif kind == "message_delta":
            data["stop_reason"] = (event.get("delta") or {}).get("stop_reason") or data.get("stop_reason")
            data["usage"].update(event.get("usage") or {})
        elif kind == "error":
            error = event.get("error") or {}
            message = f"{error.get('type', 'error')}: {error.get('message', '')}"
            if error.get("type") in ("overloaded_error", "api_error", "rate_limit_error", "timeout_error"):
                raise TransientModelError(message)
            raise FatalModelError(message)
    for index in sorted(blocks):
        block = blocks[index]
        if block.get("type") == "tool_use":
            try:
                block["input"] = json.loads(partial.get(index) or "{}")
            except json.JSONDecodeError:
                block["input"] = {"_raw": partial.get(index)}     # the tool gate will reject it with a clear error
        data["content"].append(block)
    if not data.get("model"):
        data.pop("model", None)
    if "stop_reason" not in data:              # the stream ended without saying why: it was cut off
        raise TransientModelError("the stream ended before the message was complete")
    return data


class AnthropicProvider:
    name = "anthropic"
    streams = True

    def __init__(self, model: str = "", transport=post_json, stream=post_stream):
        self.model = model or os.environ.get("LOOPWRIGHT_MODEL") or DEFAULT_MODEL
        self._post, self._stream = transport, stream

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]],
                 *, max_tokens: int = 2048, purpose: str = "loop", on_text: OnText | None = None) -> ModelResponse:
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
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        if on_text:
            data = fold_stream(self._stream(API_URL, headers, {**body, "stream": True}), on_text)
        else:
            data = self._post(API_URL, headers, body)
        return from_wire(data, self.model)
