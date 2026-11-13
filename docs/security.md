# Security model

Loopwright is a single-user program that runs on your machine. This page says what it defends against, how, and
what it does not.

## Prompt injection

Any text the agent reads from outside (a web page, a search result, a file, an MCP server's output) may contain
instructions. The model cannot reliably tell them from yours. Three layers, in increasing strength:

1. **A scan** ([`guardrails.py`](../loopwright/guardrails.py)) flags text that reads like instructions. It is a
   tripwire. It is easy to evade and nothing depends on it.
2. **A fence.** Outside content is wrapped in `<untrusted_content>` with a notice, whether or not the scan found
   anything. This helps a real model; it is still a request, not a guarantee.
3. **A rule in code** ([`toolgate.py`](../loopwright/toolgate.py)). Once a turn has read untrusted content it is
   tainted, and every tool with outside effects is denied for the rest of that turn. This holds no matter what the
   model was persuaded of. `tests/test_harness.py` drives the gate with a model that is fully compromised.

The cost is real: after reading a web page, the agent cannot send a message until the next turn.

## Permissions

| Tool effect | Policy |
|---|---|
| read | allowed |
| write (local, visible in the Memory tab) | allowed |
| external (`send_message`, `connect_mcp_server`, MCP tools by default) | needs your approval; denied outright in a tainted turn |

Also enforced by the gate: argument schemas, a tool-call budget, and a limit of two identical calls per turn.

## Sandboxing inside tools

- `read_file` resolves the path, then refuses anything outside `~/.loopwright/workspace`.
- `fetch_url` allows only `http(s)` and refuses hosts that resolve to loopback, private, link-local or reserved
  addresses (SSRF, including the cloud metadata address).
- `calculator` walks a syntax tree and allows arithmetic only.
- There is no shell or code-execution tool.
- `send_message` writes to a local table. Nothing is transmitted.

## The local web server

A server on localhost can be reached by any web page you visit. [`server.py`](../loopwright/server.py) binds to
`127.0.0.1` only, rejects requests whose `Host` is not localhost (DNS rebinding), rejects state-changing requests
whose `Origin` is another site (CSRF), and serves source files only from inside the package.

## Secrets

Anything shaped like a credential in your message is masked before it reaches the trace, the model or memory, and
again on the way out. Memory consolidation drops any candidate fact that looks like a secret. This is pattern
matching: it catches common key formats, not every secret.

## What you are trusting

- **Files in `~/.loopwright/tools/`** run as your own code. Loopwright loads only from that folder and never
  downloads a tool.
- **MCP servers you connect** are programs you chose to start. Their output is fenced and their tools need
  approval by default, but a malicious server is still a malicious program on your machine.
- **Traces** contain your prompts and tool results in plain text under `~/.loopwright/traces/`.
- **Your model provider** sees everything in the context.

## Not covered

Multi-user isolation, authentication, encryption at rest, rate limiting, and audit retention. Do not expose the
server to a network. See the handbook card on agent identity and authorisation for what a hosted version needs.

## Reporting a vulnerability

See [SECURITY.md](../SECURITY.md).
