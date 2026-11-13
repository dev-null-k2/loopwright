# Long-form handbook notes

## How a turn flows through the architecture
A message enters through a gateway. The input guardrail masks any pasted secret. Working memory loads the session's conversation, compacting it if it has grown too long. Skills whose triggers match are loaded. The retrieval gate decides whether long-term memory is needed, and if so semantic and episodic memory are searched. The context builder assembles the prompt and counts the tokens. The loop then runs: the model is called, each tool call it asks for passes the tool gate, the tool runs, and the result is appended for the next call, until the model answers or a limit is hit. The output guardrail checks the reply, which leaves through the same gateway. The exchange is saved, consolidation distils durable facts every few exchanges, and the turn is scored, costed and written to a trace.

## The four pillars of an agent
Four things sit around every model in a serious agent. The harness is the runtime: context assembly, the tool gate, budgets, guardrails, checkpoints. The loop is the control flow: call the model, run the tools, repeat, stop. Memory is what persists: working memory in the window, and semantic, episodic and procedural memory outside it. LLM-Ops is how it is kept good: traces, evals, a release gate and cost accounting. A model alone is none of these.

## Push and pull retrieval
There are two moments when memory can enter the context. Push: before the loop, the harness searches on the model's behalf and injects what it finds, guarded by the retrieval gate. Pull: during the loop, the model calls a search tool with a query of its own. Push is one cheap step but has to guess what is needed. Pull costs a loop iteration but lets the model ask for exactly what it has discovered it needs. Loopwright does both.
