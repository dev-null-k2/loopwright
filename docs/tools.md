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
