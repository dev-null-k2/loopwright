"""YOUR OWN TOOLS, and which tools are switched on.

Three ways to give the agent a capability it does not ship with:

  1. A Python file.  Drop `anything.py` into ~/.loopwright/tools/ with a list
     named TOOLS. It is loaded at start-up. Example:

         from loopwright.tools.registry import Tool

         def dice(args, ctx):
             import random
             return {"rolled": random.randint(1, int(args.get("sides", 6)))}

         TOOLS = [Tool("roll_dice", "Roll a die. Use it when the user asks for a random number.",
                       {"type": "object", "properties": {"sides": {"type": "integer"}}}, dice)]

     Set effect="write" or "external" on a tool that changes things, and
     untrusted=True on one that returns text from outside. The tool gate then
     treats it like every built-in tool: validated, budgeted, permissioned.

  2. An MCP server.  `loopwright tools add-mcp <name> -- <command> [args]`,
     or the Tools page in the cockpit, or ask the agent in chat (it will ask
     for your approval, because this starts a program on your machine).

  3. A skill.  No code at all: a procedure in words (memory/skills.py).

Any tool can be switched off in ~/.loopwright/settings.json
(`{"disabled_tools": ["send_message"]}`) or on the Tools page. A disabled tool
is never shown to the model, which is also the cheapest way to cut tokens.

A tool file is your own code running with your own permissions. Loopwright
loads only from your state folder and never downloads one.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from loopwright.tools.registry import Registry, Tool


def load_settings(home: Path) -> dict[str, Any]:
    path = Path(home) / "settings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    return {"disabled_tools": [str(t) for t in data.get("disabled_tools", [])]}


def save_settings(home: Path, settings: dict[str, Any]) -> None:
    (Path(home) / "settings.json").write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def set_tool_enabled(home: Path, name: str, enabled: bool) -> list[str]:
    settings = load_settings(home)
    disabled = set(settings["disabled_tools"])
    disabled.discard(name) if enabled else disabled.add(name)
    settings["disabled_tools"] = sorted(disabled)
    save_settings(home, settings)
    return settings["disabled_tools"]


def load_user_tools(home: Path, registry: Registry) -> list[dict[str, Any]]:
    """Import every *.py in <home>/tools and register its TOOLS. One status row
    per file; a file that fails to import is reported and skipped."""
    status = []
    folder = Path(home) / "tools"
    for path in sorted(folder.glob("*.py")) if folder.is_dir() else []:
        try:
            spec = importlib.util.spec_from_file_location(f"loopwright_user_{path.stem}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            tools = [t for t in getattr(module, "TOOLS", []) if isinstance(t, Tool)]
            for tool in tools:
                registry.add(tool)
            status.append({"file": path.name, "ok": True, "tools": [t.name for t in tools]})
        except Exception as exc:
            status.append({"file": path.name, "ok": False, "error": f"{type(exc).__name__}: {exc}", "tools": []})
    return status


def read_mcp_config(home: Path) -> dict[str, Any]:
    path = Path(home) / "mcp.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"servers": {}}
    except (OSError, json.JSONDecodeError):
        return {"servers": {}}


def add_mcp_server(home: Path, name: str, command: str, args: list[str], trust: str = "ask") -> None:
    slug = "".join(ch for ch in name.lower() if ch.isalnum() or ch in "-_")[:30]
    if not slug or not command.strip():
        raise ValueError("an MCP server needs a name and a command")
    config = read_mcp_config(home)
    config.setdefault("servers", {})[slug] = {"command": command.strip(), "args": [str(a) for a in args],
                                              **({"trust": "read"} if trust == "read" else {})}
    (Path(home) / "mcp.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def remove_mcp_server(home: Path, name: str) -> bool:
    config = read_mcp_config(home)
    if name not in config.get("servers", {}):
        return False
    del config["servers"][name]
    (Path(home) / "mcp.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return True
