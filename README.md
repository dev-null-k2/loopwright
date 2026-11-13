<div align="center">

# Loopwright

**An AI agent you can watch work.**<br>
Ask it anything. Every step it takes lights up on the architecture, at a pace you can follow,<br>
and any run can be replayed, paused and stepped through afterwards.

[Live demo](https://dev-null-k2.github.io/loopwright/) ·
[Quickstart](#start-in-a-minute) ·
[See it work](#see-it-work) ·
[Learn agent design](#learn-agent-design-while-you-use-it) ·
[Evals](#measured-not-claimed) ·
[Add your own tools](#make-it-yours) ·
[Claude Code](#runs-on-your-claude-code-subscription)

[![CI](https://github.com/dev-null-k2/loopwright/actions/workflows/ci.yml/badge.svg)](https://github.com/dev-null-k2/loopwright/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-2456d6)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-12805a)
![Dependencies](https://img.shields.io/badge/dependencies-none-12805a)
![API key](https://img.shields.io/badge/API_key-not_needed-12805a)
![Eval gate](https://img.shields.io/badge/eval_gate-passing-12805a)

<picture><source media="(prefers-color-scheme: dark)" srcset="docs/media/cockpit-dark.png"><img alt="The Loopwright cockpit. Left: a chat where each reply lists what the agent did (loaded a skill, looked in memory, recalled a fact, called create_event). Centre: the architecture board with the Semantic memory box lit and a caption explaining the step. Right: the step inspector with the ranks from two search rankers, above the list of 22 recorded steps." src="docs/media/cockpit-light.png" width="100%"></picture>

</div>

## Why it exists

Agents are hard to learn because the interesting part is invisible. You type a message, a reply appears a second
later, and in between a dozen decisions were made that you never saw: was memory searched, what went into the
prompt, which tool was allowed and why, what happened when the model failed.

Tutorials show you a while-loop. Frameworks hide it. Neither shows you the thing running.

Loopwright is a complete agent, small enough to read, built on one rule: **every step is an event.** Nothing in
the codebase prints or logs. Each guardrail check, memory lookup, model call and tool call emits one typed event
that says which part of the system it happened in and why. That one decision gives you three things:

- **A board that lights up.** The architecture is on screen and each event fills the box it belongs to.
- **Replay.** A turn takes milliseconds, so the cockpit never shows it live at full speed. It plays every run back
  at a readable pace, and you can pause, step, scrub and change speed, during the run or a week later.
- **Evals that grade behaviour, not just answers.** A test can assert that an attack was *blocked*, because the
  block is an event.

### What's new here

| | |
|---|---|
| **Replay is the interface** | Play, pause, step forward and back, scrub a strip coloured by subsystem, 0.4x to 4x. Live turns and old traces use the same player. |
| **Every reply shows its work** | Under each answer: what it looked up in memory, which skill loaded, every tool call with its arguments and raw output, retries, refusals. Click a row and the board jumps to that step. |
| **It teaches while it runs** | Each step is explained in plain words, linked to an 83-card handbook and to the file that implements it. Eighteen guided lessons run real turns and tell you where to look. |
| **Switch any mechanism off** | The Lab turns off memory, the retrieval gate or guardrails, injects model and tool failures, and changes the orchestration pattern, per message. |
| **Runs with no key** | A built-in scripted model makes everything work offline the moment you clone. Or use your Claude Code subscription, or any Anthropic or OpenAI-compatible key. |
| **Measured, with teeth** | 50 eval cases, five ablations, a release gate, and gate rules that fail if the *evals themselves* stop being able to fail. |
| **Zero dependencies** | Python standard library only. No framework, no SDK, no build step for the UI. |

## See it work

Every picture below is the real cockpit replaying a recorded run of the scripted model on invented examples,
captured by [`scripts/shoot.py`](scripts/shoot.py). The [live demo](https://dev-null-k2.github.io/loopwright/)
is the same thing in your browser.

### A lesson: prompt injection, defended

<picture><img alt="Lesson 10 open on the left: 'A prompt injection, defended'. The board shows the Tool-result guard box lit red with a warning marker. The caption reads: Untrusted content fenced (injection suspected)." src="docs/media/lesson-injection.png" width="100%"></picture>

The agent is asked to summarise a page that hides instructions to leak your data. The page is fenced as
untrusted, the turn is marked tainted, and tools with outside effects are locked. Lesson 11 runs the same
request with guardrails off, and the message goes out. That pair is the whole argument for the defence.

### The handbook, one click from any step

<picture><img alt="The Learn tab showing the Retrieval gate card: a one-line definition, why it exists, the tradeoff, a button to see it run, and an interview question with a reveal button." src="docs/media/learn.png" width="100%"></picture>

### Evals with ablations

<picture><img alt="The Evals tab: release gate Pass, 50 of 50 cases, 0% of injection attacks worked. An ablation table shows the safety suite falling to 25% and attacks rising to 100% with guardrails off, and the memory suite falling to 18% with memory off." src="docs/media/evals.png" width="100%"></picture>

### Memory you can read and edit, tools you can switch off

<picture><img alt="The Memory tab with tabs for Facts, Episodes, Skills, Identity, Consolidation and world state." src="docs/media/memory.png" width="49%"></picture>
<picture><img alt="The Tools tab listing each tool, what the model is told about it, its reach, and a switch." src="docs/media/tools.png" width="49%"></picture>

## Start in a minute

```bash
git clone https://github.com/dev-null-k2/loopwright && cd loopwright
python -m venv .venv && source .venv/bin/activate
pip install -e .

loopwright serve          # the cockpit at http://127.0.0.1:8765  (start with Lessons, top left)
```

No key needed. In the terminal:

```bash
loopwright ask "What's 18% of 2450, and save that as a note" --steps   # watch each step
loopwright replay last                                                 # play it back with explanations
loopwright                                                             # chat
loopwright learn "retrieval gate"                                      # read a handbook card
loopwright eval --gate                                                 # evals + release gate
loopwright doctor                                                      # which models can run here
```

Things to try, in order: *"Remember that Alex prefers morning meetings"*, then **New chat**, then
*"Book a catch-up with Alex on Friday"*. It books 09:00, and the reply shows where that came from.

### Three ways to give it a model

| | How | Notes |
|---|---|---|
| **Scripted** (default with nothing set up) | nothing | A rule-based stand-in, not an LLM. Instant, offline, deterministic. It handles the request shapes the lessons use and says so honestly about anything else. It is what the evals and CI run on. |
| **Claude Code subscription** | install Claude Code and sign in | No API key. See [below](#runs-on-your-claude-code-subscription). |
| **API key** | `ANTHROPIC_API_KEY`, or `OPENAI_API_KEY` (+ `OPENAI_BASE_URL` for Ollama, LM Studio, OpenRouter...) | Native tool calling; prompt caching on Anthropic. |

`loopwright doctor --ping <provider>` sends one real message to check a provider. `loopwright selftest --provider
<provider>` asks eight questions end to end (memory across sessions, tools, RAG, web search, an injection, a denied
approval) and checks what the agent actually did.

## Learn agent design while you use it

Loopwright is meant to be the fastest way to understand how agents are built, including for interviews.

- **[18 lessons](docs/lessons.md)** in the cockpit. Each runs a real turn in a scratch copy of the agent and turns
  "watch for this" into a link that jumps the board to that step: the loop, context as a budget, writing and
  reading memory, the gate saying no, RAG with citations, subagents, human approval, an injection defended and
  undefended, web search, failure and recovery, limits, planning, a critic, teaching it a skill.
- **[The handbook](docs/glossary.md)**: 83 terms, each with what it is, why it exists, the tradeoff, the file
  that implements it, and an interview question with a strong answer. Harness, ReAct, context engineering,
  context rot, compaction, prompt caching, the retrieval gate, hybrid search and RRF, consolidation, tool gates,
  MCP, prompt injection, the lethal trifecta, idempotency, circuit breakers, trajectory evals, pass^k, ablations,
  release gates, and the rest. 71 of them run in this repository; 12 are explained and labelled as not built here.
- **[Interview practice](docs/interview-prep.md)**: 12 scenario questions (design a support agent, add memory, an
  agent that reads and sends email, cut cost by two thirds, "is this ready for production?") with full answers on
  one spine: clarify, shape, design, failure, measure. Plus a flashcard deck.
- **The agent explains itself.** Its knowledge base *is* the handbook, so "What is a harness?" is answered by RAG
  over these cards, with a citation the output guard verifies.

## What is inside

One turn, top to bottom. Each row is a box on the board and a file you can read.

| Step | What it does | Code |
|---|---|---|
| Gateway | Terminal, web or MCP. A door; the same harness runs behind all three | [`cli.py`](loopwright/cli.py), [`server.py`](loopwright/server.py), [`mcp_server.py`](loopwright/mcp_server.py) |
| Guardrails | Masks pasted secrets; fences outside content as untrusted and taints the turn; checks citations against what was actually retrieved | [`guardrails.py`](loopwright/guardrails.py) |
| Working memory | The session's conversation, compacted to a summary when it outgrows its share of the context budget | [`context.py`](loopwright/context.py) |
| Skills | Procedures in Markdown. One line each always in the prompt; the body loads only when triggered | [`memory/skills.py`](loopwright/memory/skills.py) |
| Retrieval gate | Decides *whether* to search memory before searching. Rules or a small model; fails open | [`memory/gate.py`](loopwright/memory/gate.py) |
| Semantic + episodic memory | Facts and dated episodes in one SQLite file. BM25 + vectors fused with RRF. Facts are superseded, never overwritten | [`memory/store.py`](loopwright/memory/store.py), [`memory/retrieval.py`](loopwright/memory/retrieval.py) |
| Context builder | Assembles the prompt section by section and counts each one against a budget you can see | [`context.py`](loopwright/context.py) |
| **The loop** | Model, tools, results, repeat. Three stop conditions. About 100 lines | [`loop.py`](loopwright/loop.py) |
| Model gateway | Four providers behind one interface; retries with backoff and jitter, fallback chain, circuit breaker, accounting | [`models/gateway.py`](loopwright/models/gateway.py) |
| Tool gate | Six checks before any tool runs: exists, valid arguments, budget, not a repeat, permitted, not already done. Outside effects need approval; after untrusted content they are denied | [`toolgate.py`](loopwright/toolgate.py) |
| Tools | 17 built in, plus yours, plus any MCP server's | [`tools/`](loopwright/tools/) |
| Subagents | Parallel workers with a fresh context and read-only tools that return a summary | [`tools/delegate.py`](loopwright/tools/delegate.py) |
| Planner, critic | Optional patterns: plan-and-execute, evaluator-optimizer | [`patterns.py`](loopwright/patterns.py) |
| Consolidation | The background memory write: extract candidates, then resolve each as add, update, skip or drop | [`memory/consolidation.py`](loopwright/memory/consolidation.py) |
| Checkpoints | Loop state saved every round; `loopwright resume` continues a turn that died without redoing its writes | [`harness.py`](loopwright/harness.py) |
| Trace, receipt, turn checks | Every event to one JSONL file; tokens by purpose; six checks on every live turn | [`events.py`](loopwright/events.py), [`receipt.py`](loopwright/receipt.py) |
| Evals, release gate | Suites, trials, ablations, retrieval and gate evals, an optional model judge, thresholds with an exit code | [`evals/`](loopwright/evals/) |

Start reading at [`harness.py`](loopwright/harness.py): `run_turn` is the fifteen steps above in order.
More in [docs/architecture.md](docs/architecture.md).

## Measured, not claimed

`loopwright eval` runs everything below in about six seconds, offline. Raw numbers:
[`results/eval-report.json`](results/eval-report.json).

**50 cases** across five suites, each with a fresh agent and a frozen clock, graded on the trace as well as the
answer. Reliability cases run three trials with failures injected at different points.

| Variant | core | memory | safety | reliability | context | Injection attacks that worked | Memory searches |
|---|---|---|---|---|---|---|---|
| **full** | **100%** | **100%** | **100%** | **100%** | **100%** | **0 of 4** | 22 in 69 turns |
| no-memory | 100% | 18% | 100% | 100% | 80% | 0 of 4 | 0 |
| always-retrieve (no gate) | 100% | 82% | 100% | 100% | 100% | 0 of 4 | 69 in 69 turns |
| no-guardrails | 86% | 91% | 25% | 100% | 80% | **4 of 4** | 22 |
| no-retries | 100% | 100% | 100% | 67% | 100% | 0 of 4 | 22 |

| Other checks | Result |
|---|---|
| Handbook retrieval, 36 labelled questions, recall@1 / @3 | BM25 89% / 100% · dense 81% / 94% · hybrid 94% / 100% |
| Retrieval gate (rules), 30 labelled messages | 83% accurate; 2 false skips, 3 false retrieves |
| Lessons | all 17 runnable lessons produce every step they promise (a test enforces it) |
| Unit and end-to-end tests | 48 passing |

**Read these numbers honestly.**

- These are evals of the **harness**, run on the scripted model. They show that the gate skips, the fence holds,
  the retry fires and the write happens once. They say nothing about how well a given LLM reasons. Model-graded
  cases exist (`loopwright eval --judge`) and are reported as skipped unless a real model is connected.
- The cases were written alongside the code, so 100% is a regression bar, not proof of generality. The ablation
  rows are the informative part.
- The scripted model is deliberately gullible: it obeys injected instructions unless the harness fenced them.
  That is what lets the safety suite show the attacks working (4 of 4 with guardrails off) and the defence
  holding (0 of 4 with them on). One case (`tests/test_harness.py`) goes further and drives the gate with a fully
  compromised model.
- The "dense" ranker is an offline hashed embedder that captures spelling, not meaning. On this small corpus plain
  BM25 matches hybrid at recall@3. Hybrid is the default because that is what holds up on real corpora, and the
  embedder is one function you can replace.
- The rule-based gate is wrong about one message in six. Its misses are listed in the report; the model-based
  gate (`gate=model`) exists for that reason and needs a real model.
- The release gate also fails if guardrails-off stops letting attacks through, or memory-off stops failing memory
  cases. An eval that cannot fail is not measuring anything.

## Make it yours

**17 tools are built in**: `calculator`, `clock`, `remember`, `recall`, `forget`, `create_skill`,
`search_knowledge`, `web_search`, `fetch_url`, `read_file`, `create_event`, `list_events`, `save_note`,
`search_notes`, `send_message`, `connect_mcp_server`, `delegate`. The Tools tab lists what the model is told about
each one and lets you switch any of them off.

Four ways to add a capability, from no code to a little:

1. **Tell it.** *"When I ask for a standup update, always give three bullets: done, doing, blocked."* It writes a
   skill and loads it on later requests. Or write one in Memory, Skills.
2. **Connect an MCP server**, from the Tools tab, by asking in chat (it asks for your approval), or:
   ```json
   // ~/.loopwright/mcp.json
   {"servers": {"files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]}}}
   ```
3. **Drop in a Python file.** Ten lines in `~/.loopwright/tools/`; the tool gate treats it like a built-in.
4. **Change the identity prompt** in Memory, Identity.

`web_search` has five interchangeable backends: Tavily, Brave, Claude Code's own search, keyless DuckDuckGo, and
an offline demo corpus. Details and the tool example: [docs/tools.md](docs/tools.md).

## Runs on your Claude Code subscription

If the `claude` command is installed and signed in, `loopwright serve` uses it automatically. Each model call in
the loop becomes one headless invocation with every built-in Claude Code tool switched off, so it acts as a plain
model while the loop, tools, memory and guardrails stay Loopwright's. It is slower than the API, because each call
starts a process, and usage counts against your subscription.

```bash
loopwright doctor --ping claude-code        # one real message, to check it works on your machine
loopwright selftest --provider claude-code  # eight questions end to end, checked on behaviour
loopwright ask "Plan a three-day trip to Lisbon and save the plan as a note" --provider claude-code --steps
```

And the other direction: Loopwright is an MCP server, so Claude Code can call *it*.

```bash
claude mcp add loopwright -- loopwright mcp
```

Tools: `loopwright_ask`, `loopwright_explain`, `loopwright_list_concepts`, `loopwright_trace`,
`loopwright_memory_search`, `loopwright_remember`. Then ask Claude Code *"use loopwright to explain the lethal
trifecta"*. More in [docs/claude-code.md](docs/claude-code.md).

## What was tested how

| Part | How it is verified |
|---|---|
| Harness, loop, tool gate, memory, guardrails, evals, CLI, web server, MCP server and client | 48 automated tests and the 50-case eval suite, on every push |
| The cockpit | Driven in headless Chrome for every flow in the screenshots; scripts are syntax-checked in CI |
| Claude Code provider | The adapter and a full turn through the harness are tested against a stubbed `claude` that returns the CLI's real JSON shape. Check your own install with `loopwright selftest --provider claude-code` |
| Anthropic and OpenAI-compatible providers | Request and response translation tested with a fake transport. Not called live in CI |
| Web search | The demo backend end to end; the DuckDuckGo parser, Tavily, Brave and Claude Code backends against fixed response shapes. Not called live in CI |

## What it does not do

- It is a learning instrument and a reference harness, not a hosted product. It is single-user and binds to
  localhost; see the [Agent identity and authorisation](docs/glossary.md#agent-auth) card for what a server
  deployment would need.
- No shell or code-execution tool, on purpose. No voice or chat-app gateways.
- Model responses are returned whole, not streamed token by token. (Steps *are* streamed.)
- `send_message` writes to a local outbox and sends nothing.
- The scripted model is not intelligent. For open-ended questions, connect a real one.

## Project layout

```
loopwright/
  harness.py        one turn, fifteen steps: start here
  loop.py           the loop
  events.py         the event model, traces
  context.py        prompt assembly, compaction, trimming
  toolgate.py       permissions, budgets, approvals, idempotency
  guardrails.py     input, tool-result and output guards
  patterns.py       planner, critic
  memory/           store, retrieval (BM25, vectors, RRF), gate, consolidation, skills, knowledge base
  models/           gateway, scripted, claude_code, anthropic_api, openai_compat
  tools/            builtin, web_search, delegate, mcp_client, plugins, registry
  evals/            runner, graders, judge, gate, datasets/
  learn/            concepts.toml, lessons.toml, interview.toml
  server.py  cli.py  mcp_server.py
  web/              the cockpit: plain HTML, CSS and ES modules
tests/              48 tests
docs/               architecture, replay, evals, tools, claude-code, security, glossary, lessons, interview prep, events
                    + the static demo served by GitHub Pages
results/            the committed eval report
```

## Where it goes next

See [ROADMAP.md](ROADMAP.md): real embeddings behind the existing seam, token streaming, eval scores for real
models in the table above, an OpenTelemetry exporter, and held-out eval cases written by someone other than the
author.

Built by **Karthik Kasukurti**. Contributions welcome: see [CONTRIBUTING.md](CONTRIBUTING.md). Licensed under
[MIT](LICENSE). Barlow typeface under the [SIL Open Font License](loopwright/web/fonts/OFL-Barlow.txt).
