# Tools, skills and MCP

Four ways to extend the agent, from no code to a little. All of them show up in the cockpit's Tools and Memory tabs.

## 1. Teach it a skill (no code)

In chat: *"When I ask for a standup update, always give three bullets: done, doing, blocked."* The agent calls
`create_skill` and writes `~/.loopwright/skills/standup-update/SKILL.md`:

```markdown
---
name: standup-update
description: How to handle: standup update
triggers: standup update
---
Give three bullets: done, doing, blocked
```

Or write the file yourself, or use Memory, Skills in the cockpit. A skill's one-line description is always in the
prompt; its body is loaded only on a turn whose message contains a trigger. A skill with the same name as a
bundled one replaces it.

## 2. Connect an MCP server

```bash
# ~/.loopwright/mcp.json
{"servers": {
  "files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]},
  "docs":  {"command": "python", "args": ["my_server.py"], "trust": "read"}
}}
```

Or use Tools, Connect a server. Or ask in chat; that calls `connect_mcp_server`, which the tool gate treats as an
outside effect, so you are asked to approve it. Each borrowed tool appears as `server__tool`.

Two defaults follow from the fact that you did not write the server: its **results are fenced as untrusted**
(and taint the turn), and its **tools need approval** unless you set `"trust": "read"`. Only stdio servers are
supported.

## 3. Write a tool in Python

Put a file in `~/.loopwright/tools/` and restart:

```python
# ~/.loopwright/tools/dice.py
import random
from loopwright.tools.registry import Tool

def roll(args, ctx):
    return {"rolled": random.randint(1, int(args.get("sides", 6)))}

TOOLS = [Tool(
    name="roll_dice",
    description="Roll a die. Use it when the user asks for a random number.",
    parameters={"type": "object", "properties": {"sides": {"type": "integer"}}},
    fn=roll,
)]
```

Set `effect="write"` if it changes local state and `effect="external"` if it reaches outside (it will then need
approval). Set `untrusted=True` if it returns text from outside, and `private=True` if it returns the user's own
data. `ctx` gives you `ctx.store`, `ctx.home`, `ctx.options` and `ctx.rec.emit(...)` if you want your tool to
light up a box. Raise `ToolError` for a failure the model should read, `TransientToolError` for one worth retrying.

The description is the only thing the model has to decide when to use the tool. Write it like a prompt.

## 4. Switch tools off

Tools tab, or `~/.loopwright/settings.json`: `{"disabled_tools": ["send_message"]}`. A disabled tool is never
shown to the model, which also removes its schema from every prompt.

## Web search backends

`web_search` returns `{title, url, snippet}` rows from whichever backend is configured (`Options.search`, the Lab,
or `LOOPWRIGHT_SEARCH`):

| Backend | Needs | Notes |
|---|---|---|
| `tavily` | `TAVILY_API_KEY` | Picked first by `auto` when the key is set |
| `brave` | `BRAVE_API_KEY` | |
| `claude-code` | the `claude` CLI, signed in | Uses your subscription's own web search; no key |
| `duckduckgo` | nothing | The keyless HTML endpoint. Best effort; it may rate-limit or change its markup |
| `demo` | nothing, no network | A six-page bundled corpus, one page poisoned, so lessons and evals are repeatable |

Results are always fenced as untrusted. After a search, tools with outside effects are locked for the rest of the
turn.

## The built-in tools

| Tool | Reach | |
|---|---|---|
| `calculator`, `clock` | read | The calculator walks a syntax tree; it never calls `eval` |
| `search_knowledge` | read | RAG over the handbook |
| `remember`, `forget`, `recall` | write, write, read (private) | Long-term memory |
| `create_skill` | write | Procedural memory |
| `create_event`, `list_events`, `save_note`, `search_notes` | write / read (private) | Local calendar and notes |
| `web_search`, `fetch_url`, `read_file` | read, untrusted | `fetch_url` refuses non-public addresses; `read_file` cannot leave the workspace folder |
| `send_message` | external | Writes to a local outbox; nothing is transmitted |
| `connect_mcp_server` | external | Starts a program, so it needs approval |
| `delegate` | read | Up to three parallel workers with read-only tools |

## The shell tool

`run_command` runs one shell command in `~/.loopwright/workspace`. It ships **switched off**. Turn it on from the
Tools tab, or:

```json
// ~/.loopwright/settings.json
{"enabled_tools": ["run_command"]}
```

What stands around it ([`tools/shell.py`](../loopwright/tools/shell.py)):

| Control | What it does |
|---|---|
| Approval | It is an outside-effect tool: every command is shown to you before it runs. Denied in a turn that read untrusted content, and in scheduled runs. |
| OS sandbox | `sandbox-exec` on macOS, `bubblewrap` on Linux when installed: no network, writes only inside the workspace. Each candidate is tried once with a harmless command; if none works the step says `sandbox: none` in plain words. `loopwright doctor` shows which applies on your machine. |
| Clean environment | `PATH` and locale only. No API keys or tokens reach the command. `HOME` is the workspace. |
| Limits | 20 seconds, output capped, and the output is fenced as untrusted like a web page. |

There is deliberately no list of forbidden commands. A string denylist is easy to walk around, so it would only
look like safety. On a machine with no OS sandbox, approval is the real control: read the command before you
say yes.

## The browser agent

`browse_web(task)` hands a task to a worker that drives a real headless browser: open, look, click, type, look
again ([`tools/browser.py`](../loopwright/tools/browser.py)). It needs Chrome, Chromium, Edge or Brave installed
(`loopwright doctor` says whether one was found; `LOOPWRIGHT_CHROME` points at another). Nothing else: the
browser is driven over the Chrome DevTools Protocol with the WebSocket client in `ws.py`.

```
Use the browser: open demo://site, follow the Pricing link and tell me what the Team plan costs
In the browser, open https://example.com and search for refunds
```

- **How the model sees a page.** After each action the page becomes text: title, address, a numbered list of what
  can be clicked or typed into, and the visible text. The model answers with a number. A screenshot of every step
  is saved for you and shown in the inspector; the model never sees pixels.
- **It is a worker.** The main agent has one tool. The four `browser_*` tools exist only inside the worker, whose
  context is fresh. The main agent gets a short summary back, fenced as untrusted.
- **Approval.** Clicking and submitting forms on the web is an outside effect, so it needs your approval and is
  denied in a tainted turn and in scheduled runs.
- **Request filter.** Every request the page makes is intercepted. Loopback, private and link-local addresses and
  local files are refused, for navigations and for sub-resources.
- **`demo://site`** is a four-page site bundled for offline use, including a page with a hidden injection.

Limits: one tab, no file upload or download, no drag and drop, nothing that needs pixels (canvas apps). It is the
slowest tool there is, a model call per action. If a page can be read with `fetch_url`, use that.

## Scheduling and workflows

`schedule_task`, `list_tasks`, `cancel_task` and `run_workflow` are covered in
[automations.md](automations.md).
