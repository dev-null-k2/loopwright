"""TOOLS: how a model that can only write text gets to do things.

A tool is three parts, and the model only ever sees the first two:

  1. a name and a description   - how the model decides *when* to use it
  2. a JSON Schema for the args - how the model knows *what* to pass
  3. a Python function          - what actually runs, on our side

The model never executes anything. It emits "call create_event with these
arguments", the harness decides whether that is allowed (toolgate.py), runs
the function, and hands the result back as text. That gap between asking and
doing is where every safety and reliability control in this project lives.

Each tool also declares metadata the model does not see but the harness needs:

  effect     read | write | external   how far the consequences reach
  untrusted  True when the result comes from outside (web pages, files)
  private    True when the result contains the user's own data
  stage      which box on the diagram the tool touches
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loopwright.config import Options
from loopwright.events import Recorder
from loopwright.memory.store import Store


@dataclass
class ToolContext:
    """What a tool function is handed when it runs."""
    store: Store
    home: Path
    rec: Recorder
    options: Options
    now: Callable[[], dt.datetime] = dt.datetime.now
    spawn: Callable[[str, int], dict[str, Any]] | None = None   # set by the harness for `delegate`
    cited: set[str] = field(default_factory=set)                # handbook passages read this turn
    reload: Callable[[], None] | None = None                    # re-read user tools and MCP servers


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[[dict[str, Any], ToolContext], Any]
    effect: str = "read"          # read | write | external
    untrusted: bool = False
    private: bool = False
    stage: str = "tools"

    def schema(self) -> dict[str, Any]:
        """The provider-neutral shape sent to the model."""
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


class ToolError(Exception):
    """A tool failed in a way the model should be told about."""


class TransientToolError(ToolError):
    """A failure worth retrying (timeout, rate limit, upstream hiccup)."""


class Registry:
    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {t.name: t for t in tools or []}

    def add(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    def subset(self, names: list[str]) -> Registry:
        return Registry([self._tools[n] for n in names if n in self._tools])

    def without(self, names: list[str]) -> Registry:
        return Registry([t for n, t in self._tools.items() if n not in names])


def validate(schema: dict[str, Any], args: Any, path: str = "arguments") -> list[str]:
    """A small JSON Schema check: types, required keys, enums. Models do send
    malformed arguments; the fix is to tell them exactly what was wrong so the
    next attempt can correct it, not to crash."""
    errors: list[str] = []
    kind = schema.get("type")
    checks = {"object": dict, "array": list, "string": str, "boolean": bool,
              "integer": int, "number": (int, float)}
    if kind in checks and (not isinstance(args, checks[kind]) or (kind in ("integer", "number") and isinstance(args, bool))):
        return [f"{path} should be {kind}, got {type(args).__name__}"]
    if "enum" in schema and args not in schema["enum"]:
        errors.append(f"{path} must be one of {schema['enum']}")
    if kind == "object":
        for key in schema.get("required", []):
            if key not in args:
                errors.append(f"{path}.{key} is required")
        for key, value in args.items():
            if key in schema.get("properties", {}):
                errors.extend(validate(schema["properties"][key], value, f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{key} is not a known argument")
    if kind == "array" and "items" in schema:
        for i, item in enumerate(args):
            errors.extend(validate(schema["items"], item, f"{path}[{i}]"))
    return errors


def to_text(result: Any) -> str:
    """Tool results travel back to the model as text."""
    return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
