# Changelog

## 0.2.0

- **Chat gateways**: Telegram (long polling), Discord (gateway WebSocket), Slack (Socket Mode) and WhatsApp (Cloud
  API webhook with signature verification), on one shared bridge with an allowlist, a session per chat, approvals
  asked in words and `/steps`. A standard-library WebSocket client.
- **Workflows**: a graph engine with tool, model, agent and route nodes, parallel rounds and conditional
  branches, defined in TOML. Three bundled workflows (parallelisation, routing, prompt chaining with a gate).
  `/workflow <name>` in any chat, `loopwright workflow`, and a `run_workflow` tool.
- **Scheduled tasks**: readable schedules and five-field cron, unattended runs with outside effects denied,
  exactly-once claiming across processes, delivery back to the chat that created the task. `schedule_task`,
  `list_tasks`, `cancel_task` tools; `loopwright tasks`; `/task <n>` to run one now.
- **A browser agent**: a worker that drives headless Chrome over the DevTools Protocol with numbered-element page
  snapshots, a screenshot per action shown in the inspector, and a filter on every request the page makes.
- **A shell tool** (`run_command`): off by default, approval for every command, OS sandbox where available,
  scrubbed environment, time limit.
- **Model comparison**: `loopwright compare` and Evals, Compare models: the same eight behaviour cases through any
  set of providers, models or settings, with a replayable trace per cell.
- **Your own eval cases**: Good and Bad on every reply, and Save as eval case, which builds a case from the run's
  trace. `loopwright eval --mine`.
- **OpenTelemetry export**: `loopwright export`, and automatic export of every turn when
  `OTEL_EXPORTER_OTLP_ENDPOINT` is set, using the GenAI attribute names.
- Cockpit: an Automations page (scheduled tasks, workflows, chat apps), two new boxes on the board (Scheduler,
  Workflow), six new lessons. Handbook: seven new cards, three cards moved from "explained" to "built".
- Evals: a sixth suite, automation, with 19 cases; 69 cases in total. Tools: 23.

## 0.1.0

First release.

- The engine: a fifteen-step turn where every step emits a typed event; the loop with three stop conditions;
  context assembly with a per-section token budget, compaction and tool-result trimming.
- Memory: working, semantic, episodic and procedural; a retrieval gate (rules or model); hybrid BM25 + vector
  search with reciprocal rank fusion; a two-stage write path that supersedes facts.
- Safety: input, tool-result and output guardrails; a tool gate with schema validation, budgets, repeat
  detection, approvals, idempotency and a taint rule that blocks outside effects after untrusted content.
- Reliability: retries with backoff and jitter, a fallback chain, a circuit breaker, checkpoints and resume, and
  a failure lab that injects model and tool failures.
- Models: an offline scripted model, local open-source models (Ollama, LM Studio, llama.cpp) with automatic
  detection and three levels of tool-calling fallback, Claude Code subscription, Anthropic API,
  OpenAI-compatible endpoints.
- Tools: 17 built in, including web search with five backends and parallel subagents; your own Python tools;
  an MCP client; an MCP server.
- The cockpit: an architecture board that lights up, replay with play, pause, step, scrub and speed, a step
  inspector, a per-reply list of everything the agent did, 18 guided lessons in a scratch classroom, the Lab,
  and Learn, Evals, Memory, Tools and Activity pages.
- Learning: an 84-card handbook, 12 interview scenarios, flashcards.
- Evals: 50 cases in five suites, trials, four ablations, retrieval and gate evals, an optional model judge, and
  a release gate.
