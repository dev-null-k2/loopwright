"""THE SHELL TOOL: the most useful tool an agent can have, and the most dangerous.

`run_command` lets the model run a shell command. One tool replaces a hundred
(grep, git, curl, python...), which is why coding agents are built around it.
It is also arbitrary code execution chosen by a language model, so it is the
tool where every control in this project applies at once:

  off by default   It is not in the toolset until you switch it on (Tools page,
                   or `"enabled_tools": ["run_command"]` in settings.json).
  approval         It is an "external" tool: every command is shown to a human
                   before it runs. In a turn that has read untrusted content it
                   is denied outright. In unattended runs (scheduled tasks) it
                   is denied, because nobody is there to approve.
  a sandbox        The command runs inside the operating system's own sandbox
                   when one is available: sandbox-exec on macOS, bubblewrap on
                   Linux. No network; writes only inside the workspace folder.
                   When neither exists the event says so in plain words.
  a clean room     Working directory = the workspace. Environment = PATH and a
                   few harmless variables: no API keys, no tokens.
  limits           A timeout, and output capped and treated as untrusted (a
                   command can print a web page or a file someone else wrote).

What is deliberately NOT here: a list of forbidden commands. A denylist of
strings ("rm -rf", "sudo") is easy to write and easy to walk around
(`r''m`, a script file, base64), so it buys a false sense of safety. The real
boundaries are the three above that do not depend on reading the command.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from loopwright.tools.registry import Tool, ToolContext, ToolError

TIMEOUT = 20
MAX_OUTPUT = 4000
_probe: dict[str, tuple[list[str], str, str]] = {}

SEATBELT = """(version 1)
(allow default)
(deny network*)
(deny file-write*)
(allow file-write* (subpath "{workspace}") (literal "/dev/null") (literal "/dev/tty") (subpath "/dev/fd"))
"""


def sandbox_for(workspace: Path) -> tuple[list[str], str, str]:
    """-> (command prefix, sandbox name, what it enforces). Each candidate is
    tried once with a harmless command; one that does not work here is skipped."""
    key = str(workspace)
    if key in _probe:
        return _probe[key]
    candidates: list[tuple[list[str], str, str]] = []
    if platform.system() == "Darwin" and Path("/usr/bin/sandbox-exec").exists():
        candidates.append((["/usr/bin/sandbox-exec", "-p", SEATBELT.format(workspace=workspace)], "sandbox-exec",
                           "macOS sandbox: no network, writes only inside the workspace"))
    if shutil.which("bwrap"):
        candidates.append((["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
                            "--bind", key, key, "--unshare-net", "--unshare-pid", "--die-with-parent", "--chdir", key],
                           "bubblewrap", "Linux namespaces: no network, the filesystem is read-only except the workspace"))
    for prefix, name, note in candidates:
        try:
            if subprocess.run([*prefix, "/bin/sh", "-c", "true"], capture_output=True, timeout=5).returncode == 0:
                _probe[key] = (prefix, name, note)
                return _probe[key]
        except (OSError, subprocess.SubprocessError):
            continue
    _probe[key] = ([], "none", "no OS sandbox is available here, so the command runs with your own permissions; "
                                "only the approval, the timeout and the scrubbed environment stand in its way")
    return _probe[key]


def clean_env(workspace: Path) -> dict[str, str]:
    keep = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "TZ", "SYSTEMROOT", "COMSPEC") if k in os.environ}
    return {**keep, "HOME": str(workspace), "TERM": "dumb"}


def run_command(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    command = args["command"].strip()
    if not command:
        raise ToolError("give a command to run")
    workspace = (ctx.home / "workspace").resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    prefix, sandbox, note = sandbox_for(workspace)
    shell = ["cmd", "/c", command] if platform.system() == "Windows" else ["/bin/sh", "-c", command]
    started = time.perf_counter()
    try:
        done = subprocess.run([*prefix, *shell], cwd=workspace, env=clean_env(workspace), capture_output=True,
                              text=True, errors="replace", timeout=TIMEOUT, stdin=subprocess.DEVNULL)
        code, out, err, timed_out = done.returncode, done.stdout, done.stderr, False
    except subprocess.TimeoutExpired as exc:
        code, timed_out = -1, True
        out = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = f"stopped after {TIMEOUT} seconds"
    except OSError as exc:
        raise ToolError(f"could not start the command: {exc}") from exc
    dur = int((time.perf_counter() - started) * 1000)
    ctx.rec.emit("shell.run", "tools", f"Ran `{command[:60]}` (exit {code}, sandbox: {sandbox})",
                 "A shell command the model wrote ran on this machine. It ran in the workspace folder with a "
                 f"scrubbed environment (no API keys) and a {TIMEOUT}-second limit. Isolation: {note}.",
                 src="toolgate", status="warn" if code or timed_out else "ok", dur_ms=dur,
                 data={"command": command, "exit_code": code, "sandbox": sandbox, "sandbox_note": note,
                       "cwd": "workspace", "timed_out": timed_out, "stdout": out[:1500], "stderr": err[:800]})
    return {"exit_code": code, "stdout": out[:MAX_OUTPUT], "stderr": err[:1000], "sandbox": sandbox,
            **({"timed_out": True} if timed_out else {})}


def shell_tool() -> Tool:
    return Tool(
        "run_command",
        "Run a shell command in the agent's workspace folder and return its output. Use it for things no other "
        "tool does (listing or searching files, running a script). The user must approve every command.",
        {"type": "object", "properties": {"command": {"type": "string", "description": "one shell command, e.g. ls -la"}},
         "required": ["command"], "additionalProperties": False},
        run_command, effect="external", untrusted=True)
