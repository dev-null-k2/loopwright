"""Claude through your Claude Code subscription. No API key.

If the `claude` command is installed and signed in, Loopwright can use it as
its model. Each model call in the loop becomes one headless invocation:

    claude -p --output-format json --system-prompt <ours> --tools "" ...

Two things about this adapter are worth understanding:

1. Claude Code is an agent with its own loop and its own tools. Here every
   built-in tool is switched off (`--tools ""`) and nothing is persisted, so
   it behaves as a plain model: text in, text out. The loop, the tools, the
   memory and the guardrails stay Loopwright's, which is the point of the
   project.

2. A headless CLI has no native tool-calling channel for tools it does not
   own. So the tools are described in the system prompt and the model is
   asked to answer with one JSON object: either tool calls or a final answer.
   This is tool calling as it worked before providers offered it natively,
   and it is still how you add tools to any model that only does text. If the
   model answers in prose instead, the prose is taken as the final answer.

It is slower than the API (each call starts a process) and usage counts
against your subscription's limits.

STREAMING: with `--output-format stream-json --include-partial-messages` the
CLI prints one JSON object per line while it works. Lines of type
`stream_event` wrap the same events the Messages API streams (a `text_delta`
is a piece of the reply) and the last line, of type `result`, is the object
the plain `json` format would have printed. Because the model here answers in
the JSON protocol above, the pieces go through a ReplyFilter, which passes on
only the characters of the "final" answer.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

from loopwright.models.base import (
    FatalModelError,
    Message,
    ModelResponse,
    OnText,
    ToolCall,
    TransientModelError,
    Usage,
    estimate_tokens,
)
from loopwright.models.streaming import ReplyFilter

DEFAULT_MODEL = "sonnet"
TIMEOUT_S = 240

PROTOCOL = """

## How to respond (strict)

You are the reasoning step inside an external agent loop. You cannot run tools yourself:
you ask for them and the loop runs them and shows you the results on the next step.

Reply with exactly one JSON object and nothing else, no code fence:

  to use tools:   {{"thought": "<one short sentence>", "tool_calls": [{{"name": "<tool>", "arguments": {{...}}}}]}}
  to answer:      {{"thought": "<one short sentence>", "final": "<your answer to the user, Markdown allowed>"}}

Ask for several tools at once when they are independent. Only use these tools:

{tools}
"""


def find_cli() -> str | None:
    return os.environ.get("LOOPWRIGHT_CLAUDE_BIN") or shutil.which("claude")


def render_transcript(messages: list[Message]) -> str:
    """The whole conversation as one prompt: a headless call is stateless."""
    lines = ["<conversation>"]
    for m in messages:
        if m["role"] == "user":
            lines.append(f"<user>\n{m['content']}\n</user>")
        elif m["role"] == "assistant":
            calls = [{"id": c["id"], "name": c["name"], "arguments": c["arguments"]} for c in m.get("tool_calls", []) or []]
            body = m.get("content") or ""
            if calls:
                body += ("\n" if body else "") + "tool_calls: " + json.dumps(calls, ensure_ascii=False)
            lines.append(f"<assistant>\n{body}\n</assistant>")
        else:
            flag = ' error="true"' if m.get("is_error") else ""
            lines.append(f"<tool_result tool=\"{m.get('name', '')}\" id=\"{m.get('tool_call_id', '')}\"{flag}>\n"
                         f"{m['content']}\n</tool_result>")
    lines.append("</conversation>")
    lines.append("Reply with the next JSON object.")
    return "\n".join(lines)


def parse_reply(text: str) -> tuple[str, list[ToolCall], str]:
    """-> (final_text, tool_calls, thought). Lenient on purpose: strip a code
    fence, find the outermost JSON object, and fall back to treating the
    whole reply as the final answer."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and ("tool_calls" in data or "final" in data):
            calls = [ToolCall(f"cc_{uuid.uuid4().hex[:8]}", str(c.get("name", "")), c.get("arguments") or {})
                     for c in data.get("tool_calls") or [] if isinstance(c, dict)]
            return str(data.get("final") or ""), calls, str(data.get("thought") or "")
    return text.strip(), [], ""


class ClaudeCodeProvider:
    name = "claude-code"
    streams = True

    def __init__(self, model: str = "", runner=subprocess.run, home: Path | None = None, popen=subprocess.Popen):
        self.model = model or os.environ.get("LOOPWRIGHT_MODEL") or DEFAULT_MODEL
        self._run, self._popen = runner, popen
        self._cwd = Path(home or Path.home() / ".loopwright") / "claude-code-cwd"

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]],
                 *, max_tokens: int = 2048, purpose: str = "loop", on_text: OnText | None = None) -> ModelResponse:
        cli = find_cli()
        if not cli:
            raise FatalModelError("the `claude` command was not found. Install Claude Code, or pick another provider")
        tool_text = "\n".join(f"- {t['name']}: {t['description']}\n  arguments schema: {json.dumps(t['parameters'])}"
                              for t in tools) or "(no tools available: answer directly)"
        full_system = system + PROTOCOL.format(tools=tool_text)
        prompt = render_transcript(messages)
        # an empty working directory, so no project CLAUDE.md or settings leak into the call
        self._cwd.mkdir(parents=True, exist_ok=True)
        cmd = [cli, "-p", "--model", self.model, "--system-prompt", full_system,
               "--tools", "", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"]
        if on_text:
            data, code, stderr = self._streamed(cmd, prompt, on_text)
        else:
            data, code, stderr = self._whole(cmd, prompt)
        result = str(data.get("result", ""))
        if code != 0 or data.get("is_error") or not data:
            message = result or stderr.strip()[:300] or f"claude exited with code {code}"
            if re.search(r"authenticat|log ?in|sign ?in|credential|unauthori[sz]ed|invalid api key", message, re.I):
                raise FatalModelError("Claude Code is not signed in. Run `claude` once in a terminal and sign in. "
                                      f"({message})")
            if re.search(r"unknown option|usage:", message, re.I):
                raise FatalModelError(f"this version of Claude Code rejected a flag: {message}. Try `claude update`")
            raise TransientModelError(message)
        final, calls, thought = parse_reply(result)
        usage = data.get("usage") or {}
        cached = (usage.get("cache_read_input_tokens") or 0)
        tokens_in = (usage.get("input_tokens") or 0) + cached + (usage.get("cache_creation_input_tokens") or 0)
        return ModelResponse(
            text=final, tool_calls=calls, thought=thought, provider=self.name, model=self.model,
            stop_reason="tool_use" if calls else "end_turn", cost_usd=data.get("total_cost_usd"),
            usage=Usage(tokens_in or estimate_tokens(full_system + prompt),
                        usage.get("output_tokens") or estimate_tokens(result), cached, estimated=not usage))

    def _whole(self, cmd: list[str], prompt: str) -> tuple[dict[str, Any], int, str]:
        """Run to the end, then read the one JSON object it printed."""
        try:
            proc = self._run([*cmd, "--output-format", "json"], input=prompt, capture_output=True, text=True,
                             timeout=TIMEOUT_S, cwd=str(self._cwd))
        except subprocess.TimeoutExpired as exc:
            raise TransientModelError(f"claude did not answer within {TIMEOUT_S}s") from exc
        except OSError as exc:
            raise FatalModelError(f"could not start claude: {exc}") from exc
        try:
            data = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
        except json.JSONDecodeError:
            data = {}
        return (data if isinstance(data, dict) else {}), proc.returncode, proc.stderr or ""

    def _streamed(self, cmd: list[str], prompt: str, on_text: OnText) -> tuple[dict[str, Any], int, str]:
        """Read the CLI's output a line at a time while it runs, passing reply text on as it appears."""
        feed = ReplyFilter(on_text).feed
        data: dict[str, Any] = {}
        with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as errors:     # a file, so a chatty stderr cannot block the pipe
            try:
                proc = self._popen([*cmd, "--output-format", "stream-json", "--verbose", "--include-partial-messages"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, text=True,
                                   encoding="utf-8", errors="replace", cwd=str(self._cwd))
            except OSError as exc:
                raise FatalModelError(f"could not start claude: {exc}") from exc
            timed_out = threading.Event()

            def stop() -> None:
                timed_out.set()
                proc.kill()

            watchdog = threading.Timer(TIMEOUT_S, stop)
            watchdog.start()
            try:
                try:
                    proc.stdin.write(prompt)
                    proc.stdin.close()
                except (BrokenPipeError, OSError):
                    pass                                # it exited before reading: the exit code will say why
                for line in proc.stdout:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    if row.get("type") == "stream_event":
                        delta = (row.get("event") or {}).get("delta") or {}
                        if delta.get("type") == "text_delta":
                            feed(delta.get("text", ""))
                    elif row.get("type") == "result":
                        data = row
                code = proc.wait()
            finally:
                watchdog.cancel()
                if proc.poll() is None:
                    proc.kill()
            if timed_out.is_set():
                raise TransientModelError(f"claude did not answer within {TIMEOUT_S}s")
            errors.seek(0)
            return data, code, errors.read(4000)
