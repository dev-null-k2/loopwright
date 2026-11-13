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
| external (`send_message`, `browse_web`, `run_command`, `schedule_task`, `connect_mcp_server`, MCP tools by default) | needs your approval; denied outright in a tainted turn, and in any unattended (scheduled) run |

Also enforced by the gate: argument schemas, a tool-call budget, and a limit of two identical calls per turn.

## Sandboxing inside tools

- `read_file` resolves the path, then refuses anything outside `~/.loopwright/workspace`.
- `fetch_url` allows only `http(s)` and refuses hosts that resolve to loopback, private, link-local or reserved
  addresses (SSRF, including the cloud metadata address).
- `calculator` walks a syntax tree and allows arithmetic only.
- `run_command` (the shell) ships switched off. Switched on, each command needs approval and runs in the
  workspace with a scrubbed environment, a time limit, and the OS sandbox when one exists (`sandbox-exec`,
  `bubblewrap`). Where none exists the step says so. See [tools.md](tools.md#the-shell-tool).
- `browse_web` drives a headless browser in a throwaway profile. Every request the page makes is intercepted and
  refused if it targets loopback, private or link-local addresses or a local file.
- `send_message` writes to a local table. Nothing is transmitted.

## Unattended runs and standing instructions

A scheduled task runs with nobody to approve anything, so its approval policy is forced to `deny`: every
outside-effect tool is refused in code. Creating a task is itself an outside-effect action, approved by you and
denied in a tainted turn, because a task is an instruction the agent leaves for a later run that no person reads
first. Without that rule one poisoned page could install an instruction that runs every day.

Workflows get no exemption: each tool step in a workflow goes through the same gate.

## Chat gateways

A bot on a public platform can be messaged by anyone, so each gateway answers only the ids on its allowlist and
tells everyone else nothing but their own id. The WhatsApp webhook is a public URL: every delivery's HMAC
signature is checked against your app secret and unsigned requests are dropped. Bot tokens are read from the
environment and are never written to the state folder or a trace. Messages pass through the chat platform itself.
See [gateways.md](gateways.md).

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
- **Traces** contain your prompts and tool results in plain text under `~/.loopwright/traces/`, and browser
  screenshots under `traces/shots/`. With `OTEL_EXPORTER_OTLP_ENDPOINT` set, each turn is also sent to that
  collector.
- **Workflow files in `~/.loopwright/workflows/`** are yours; they can only call tools the gate would allow.
- **Your model provider** sees everything in the context.

## Not covered

Multi-user isolation, authentication, encryption at rest, rate limiting, and audit retention. Do not expose the
server to a network. See the handbook card on agent identity and authorisation for what a hosted version needs.

## Reporting a vulnerability

See [SECURITY.md](../SECURITY.md).
