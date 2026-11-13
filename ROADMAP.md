# Roadmap

Roughly in order.

**Next**
- Scores for real models: three trials per model through the local, Claude Code and API providers, published
  from `loopwright compare`.
- A real embedding model behind the existing `embed` seam, with the retrieval eval re-run against it.
- Token streaming for model responses, and the guardrail question that comes with it. (Steps are streamed today;
  model text arrives whole.)
- The chat gateways run against the live services, with the results written up.

**Then**
- Held-out eval cases written by someone other than the author.
- A model-based gate eval, to put a number next to the rule-based gate's 83%.
- HTTP transport for the MCP client (stdio only today).
- Triggers other than time for scheduled work: a webhook, a new file in the workspace.
- A container or micro-VM runner for `run_command`, for machines where the OS sandbox is not available.
- A reranker stage, measured.

**What comparable projects have that this one does not**

Checked against personal-agent harnesses, teaching repositories and observability platforms in November 2026.

- Voice in and out; email, Signal, Matrix and iMessage channels.
- A skill marketplace, installing a skill from a URL, and agents that rewrite their own skills after a task.
- Vision-based computer use (the model looking at screenshots), several browser tabs, file upload and download.
- Prompt versioning, dataset management and human annotation queues, as observability platforms offer.
- Agent-to-agent handoffs between separately defined agents.
- Graph or temporal knowledge-graph memory.
- Spending limits per day across turns.

Some of these belong in a product more than in an instrument for learning; they are listed so the gap is known.

**Not planned**
- A hosted multi-user version. The handbook explains what that would take; this project stays a local instrument.
