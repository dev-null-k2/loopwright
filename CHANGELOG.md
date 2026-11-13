# Changelog

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
- Models: an offline scripted model, Claude Code subscription, Anthropic API, OpenAI-compatible endpoints.
- Tools: 17 built in, including web search with five backends and parallel subagents; your own Python tools;
  an MCP client; an MCP server.
- The cockpit: an architecture board that lights up, replay with play, pause, step, scrub and speed, a step
  inspector, a per-reply list of everything the agent did, 18 guided lessons in a scratch classroom, the Lab,
  and Learn, Evals, Memory, Tools and Activity pages.
- Learning: an 83-card handbook, 12 interview scenarios, flashcards.
- Evals: 50 cases in five suites, trials, four ablations, retrieval and gate evals, an optional model judge, and
  a release gate.
