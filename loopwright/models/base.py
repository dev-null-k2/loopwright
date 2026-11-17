"""The shapes every model provider speaks, so the loop never knows which one it has.

Messages use one neutral format inside Loopwright; each provider translates
it to its own wire format at the last moment:

    {"role": "user", "content": "text"}
    {"role": "assistant", "content": "text", "tool_calls": [{"id", "name", "arguments"}]}
    {"role": "tool", "tool_call_id": "...", "name": "...", "content": "text", "is_error": False}
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

Message = dict[str, Any]
OnText = Callable[[str], None]      # called with each piece of reply text as the model writes it


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    cached_in: int = 0
    estimated: bool = False      # True when counted as characters / 4, not reported by a provider


@dataclass
class ModelResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    provider: str = ""
    model: str = ""
    stop_reason: str = "end_turn"
    thought: str = ""            # a short visible rationale, when the provider gives one
    cost_usd: float | None = None


class ModelError(Exception):
    """A model call failed."""


class TransientModelError(ModelError):
    """Worth retrying: rate limit, overload, timeout, a dropped connection."""


class FatalModelError(ModelError):
    """Not worth retrying: bad key, bad request, not signed in."""


class Provider(Protocol):
    """A provider that can stream sets `streams = True` and accepts one more
    keyword, `on_text`: when it is given, the provider asks its backend to
    stream and calls it with each piece of reply text as it arrives. It still
    returns the whole ModelResponse at the end, so the loop is the same either
    way. A provider without `streams` is never passed `on_text`."""
    name: str
    model: str

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]],
                 *, max_tokens: int = 2048, purpose: str = "loop") -> ModelResponse: ...


def estimate_tokens(text: str) -> int:
    """About four characters per token for English. Good enough to budget with,
    and labelled as an estimate everywhere it is shown."""
    return math.ceil(len(text) / 4)


def messages_text(messages: list[Message]) -> str:
    parts = []
    for m in messages:
        parts.append(str(m.get("content", "")))
        for call in m.get("tool_calls", []) or []:
            parts.append(f"{call['name']} {call['arguments']}")
    return "\n".join(parts)
