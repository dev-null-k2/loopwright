"""Any OpenAI-compatible chat completions endpoint.

Covers OpenAI itself and everything that copies its API: OpenRouter, Groq,
Together, and local servers such as Ollama, LM Studio and vLLM.

    OPENAI_API_KEY=...            (any non-empty value for a local server)
    OPENAI_BASE_URL=http://localhost:11434/v1     (optional)
    LOOPWRIGHT_MODEL=<model id>   (required: there is no safe default)

STREAMING: with `"stream": true` the answer arrives as chunks, each holding a
`delta`: a few characters of text, or a fragment of a tool call. `fold_stream`
passes the text on and rebuilds the body a non-streaming call would return.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable, Iterator
from typing import Any

from loopwright.models._http import post_json, post_stream
from loopwright.models.base import FatalModelError, Message, ModelResponse, OnText, ToolCall, TransientModelError, Usage


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


def fold_stream(chunks: Iterable[dict[str, Any]], on_text: OnText) -> dict[str, Any]:
    """Read streamed chunks and rebuild the whole chat completion from them.

    Text arrives in `choices[0].delta.content`. A tool call arrives in pieces
    under `delta.tool_calls`, each tagged with an `index`: the first piece has
    the id and the name, the rest are fragments of the arguments string. The
    last chunk before [DONE] carries `usage` when it was asked for."""
    text: list[str] = []
    calls: dict[int, dict[str, Any]] = {}
    data: dict[str, Any] = {}
    finish = None
    for chunk in chunks:
        if chunk.get("error"):
            error = chunk["error"]
            raise TransientModelError(str(error.get("message", error) if isinstance(error, dict) else error))
        if chunk.get("model"):
            data["model"] = chunk["model"]
        if chunk.get("usage"):
            data["usage"] = chunk["usage"]
        choice = (chunk.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}
        if delta.get("content"):
            text.append(delta["content"])
            on_text(delta["content"])
        for piece in delta.get("tool_calls") or []:
            call = calls.setdefault(piece.get("index", len(calls)), {"id": "", "type": "function",
                                                                     "function": {"name": "", "arguments": ""}})
            call["id"] = piece.get("id") or call["id"]
            function = piece.get("function") or {}
            call["function"]["name"] += function.get("name") or ""
            call["function"]["arguments"] += function.get("arguments") or ""
        finish = choice.get("finish_reason") or finish
    if finish is None:
        if not text and not calls:              # nothing at all arrived: the stream was cut off
            raise TransientModelError("the stream ended before the message began")
        finish = "tool_calls" if calls else "stop"      # some servers never send a finish reason
    for index, call in calls.items():
        call["id"] = call["id"] or f"call_{index}"
    data["choices"] = [{"message": {"content": "".join(text), "tool_calls": [calls[i] for i in sorted(calls)]},
                        "finish_reason": finish}]
    return data


def stream_chat(stream: Callable[..., Iterator[dict[str, Any]]], url: str, headers: dict[str, str],
                body: dict[str, Any], on_text: OnText, timeout: float = 120) -> dict[str, Any]:
    """One streamed chat completion. Asks for the token counts too; a server
    that does not know that option gets the request again without it."""
    try:
        return fold_stream(stream(url, headers, {**body, "stream": True, "stream_options": {"include_usage": True}}, timeout), on_text)
    except FatalModelError as exc:
        if "stream_options" not in str(exc):
            raise
        return fold_stream(stream(url, headers, {**body, "stream": True}, timeout), on_text)


class OpenAICompatProvider:
    name = "openai"
    streams = True

    def __init__(self, model: str = "", transport=post_json, stream=post_stream):
        self.model = model or os.environ.get("LOOPWRIGHT_MODEL") or os.environ.get("OPENAI_MODEL", "")
        self._post, self._stream = transport, stream

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]],
                 *, max_tokens: int = 2048, purpose: str = "loop", on_text: OnText | None = None) -> ModelResponse:
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
        url, headers = f"{base}/chat/completions", {"authorization": f"Bearer {key}"}
        data = stream_chat(self._stream, url, headers, body, on_text) if on_text else self._post(url, headers, body)
        return from_wire(data, self.model)
