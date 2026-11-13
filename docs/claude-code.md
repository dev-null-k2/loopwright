# Loopwright and Claude Code

They connect in both directions.

## Claude Code as Loopwright's model (no API key)

If `claude` is on your PATH and signed in, Loopwright's `auto` provider picks it when no API key is set.

```bash
loopwright doctor                          # shows which providers are ready
loopwright doctor --ping claude-code       # sends one real message
loopwright serve                           # the Model menu shows "Claude Code subscription"
loopwright ask "..." --provider claude-code --model opus --steps
```

What happens on each model call ([`models/claude_code.py`](../loopwright/models/claude_code.py)):

```
claude -p --output-format json --model sonnet --system-prompt <Loopwright's prompt + tool protocol>
       --tools "" --strict-mcp-config --disable-slash-commands --no-session-persistence
```

- `--tools ""` switches off every built-in Claude Code tool, so it behaves as a plain model. The loop, tools,
  memory and guardrails stay Loopwright's.
- The whole conversation is sent on stdin each time; the call is stateless and nothing is saved to your Claude
  Code history.
- It runs in an empty folder under `~/.loopwright/`, so no project `CLAUDE.md` or settings leak in.
- Loopwright's tools are described in the system prompt and the model is asked to reply with one JSON object:
  tool calls or a final answer. A reply in plain prose is taken as the final answer.

Costs to know about: each call starts a process, so expect a few seconds per loop round; usage counts against
your subscription's limits; and the figure Claude Code reports as cost is shown in the receipt as reported.

`web_search` can also go through Claude Code (`search=claude-code`): one headless call with only `WebSearch`
enabled.

**Status.** The adapter is tested in CI against a stubbed `claude` that returns the CLI's real JSON output shape,
including a full turn through the harness, a prose reply, an authentication error and an overload. CI has no
signed-in Claude Code, so check your own install with `loopwright selftest --provider claude-code`: eight
questions end to end, checked on what the agent did, not on how the model worded it. The exact command line was also run against Claude Code
2.1.323 on a machine with no login: the CLI accepted every flag and returned its JSON authentication error, which
Loopwright reports as "not signed in". That is as far as a machine without an account can go. If your CLI version
rejects a flag, the error says which.

## Loopwright as a tool server for Claude Code (MCP)

```bash
claude mcp add loopwright -- loopwright mcp
```

This repository also ships a [`.mcp.json`](../.mcp.json), so opening it in Claude Code offers the server
automatically.

| Tool | Does |
|---|---|
| `loopwright_explain` | A handbook card: what, why, tradeoff, file, interview question |
| `loopwright_list_concepts` | Every term with a one-line definition |
| `loopwright_ask` | Runs one turn and returns the reply plus its step list |
| `loopwright_trace` | The recorded steps of a past turn, with explanations |
| `loopwright_memory_search`, `loopwright_remember` | Loopwright's long-term memory |

Turns started over MCP use the scripted model by default, because the caller is already an LLM; set
`LOOPWRIGHT_PROVIDER` to change that. Tools with outside effects are denied on this channel: there is no human on
it to approve them.

The server is about 150 lines of hand-written JSON-RPC over stdio
([`mcp_server.py`](../loopwright/mcp_server.py)), there to be read. The test suite connects Loopwright's MCP
client to Loopwright's MCP server.
