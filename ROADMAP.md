# Roadmap

Roughly in order.

**Next**
- Scores for real models in the eval table: three trials per model through the Claude Code and API providers.
- A real embedding model behind the existing `embed` seam, with the retrieval eval re-run against it.
- Token streaming for model responses, and the guardrail question that comes with it.
- An OpenTelemetry exporter that maps events to spans with the GenAI semantic conventions.

**Then**
- Held-out eval cases written by someone other than the author.
- A model-based gate eval, to put a number next to the rule-based gate's 83%.
- HTTP transport for the MCP client (stdio only today).
- A scheduled-turn gateway, with the stricter approval policy unattended runs need.
- A reranker stage, measured.

**Not planned**
- A shell or code-execution tool without a real sandbox.
- A hosted multi-user version. The handbook explains what that would take; this project stays a local instrument.
