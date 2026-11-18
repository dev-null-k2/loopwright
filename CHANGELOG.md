# Changelog

## 0.10.0

MCP over HTTP, and the protocol's 2026 revision.

- **Borrow tools from a remote MCP server.** An entry in `mcp.json` can be a `url` instead of a `command`. Each
  message is one POST; the answer is read from a JSON body or from a short event stream. Header values can name
  an environment variable (`${TOKEN}`), so a secret is never written in the file, and plain http is refused for
  anything but this machine.
- **Both eras of the protocol, on both transports.** The 2026-07-28 revision is stateless: no `initialize`, no
  session, the version on every request and, over HTTP, the method and tool name mirrored into headers. Older
  servers still open with the handshake. The client tries the new way first (`server/discover`) and falls back
  when the answer is not a modern one; over stdio that includes a server that simply stays silent. An expired
  session is reopened, a version the server stops speaking is renegotiated, and tool parameters marked
  `x-mcp-header` are mirrored and encoded as the revision requires.
- **Loopwright's own server speaks HTTP too.** `loopwright mcp --http` serves the same six tools on
  `http://127.0.0.1:8766/mcp`, to modern and older clients, with `Origin` checking, header validation and an
  optional bearer token. Claude Code 2.1.326 connects to it.
- An `mcp.call` step shows each borrowed call with its transport and protocol version; the Tools page shows the
  same per server. The scripted model can now call a borrowed tool when asked for it by name
  ("Use handbook__loopwright_explain for idempotency"), which is what lets two new eval cases run offline against
  a real server over real HTTP (76 cases). 156 tests.
- Not supported, and said so in `docs/tools.md`: OAuth sign-in, the retired HTTP+SSE transport, and servers that
  ask the client for input. Not yet run: a third-party hosted MCP server.

## 0.9.0

A real embedding model and a reranker, behind one option each.

- **Embedders.** `embedder=ollama:<model>` (Ollama's `/api/embed`), `openai:<model>` (any `/v1/embeddings`), or
  `auto` (the first embedding model on your local server). The built-in hash stand-in stays the default, so
  nothing changes until you choose. Passages are embedded in batches and cached on disk; the question and the
  passages get the instruction prefixes each model family was trained with; the similarity floor is measured for
  the model instead of assumed; a real model is given the user's sentence, not the keyword query; and when the
  model cannot be reached the search falls back to the stand-in and says so.
- **Rerankers.** `rerank=endpoint` sends the best twelve handbook candidates to a cross-encoder behind the shared
  `/v1/rerank` API; `rerank=model` has the chat model order them. Both fail open to the first-stage order.
- **A held-out retrieval set.** 60 questions that ask for handbook cards in other words, written before any result
  on them was seen. With the stand-in embedder the hybrid ranker finds the card in the top three for 98% of the
  original questions and 73% of these. Both sets are in the report, the README and the Evals tab.
- `loopwright eval --retrieval --embedder hash --embedder auto --rerank model` scores any combination side by
  side and writes `results/retrieval.json`. `loopwright doctor` lists local embedding models.
- Three new events (`embed.run`, `embed.fallback`, `rerank.run`); the Embeddings and Reranking cards now describe
  code that runs; two Lab switches; two eval cases (74); `docs/retrieval.md`. 145 tests.
- **Not yet run:** any real embedding model or reranker. Everything above is tested over HTTP against a stand-in
  server that speaks those APIs. The retrieval table has no row for a real model until someone runs the command.

## 0.8.0

The reply arrives while the model is still writing it.

- **Every provider streams.** The Anthropic API and OpenAI-compatible servers through their streaming formats
  (tool calls included, reassembled from fragments), local servers the same way, Claude Code through its partial
  messages, and the scripted model a few words at a time. Each still returns the whole response at the end, so
  the loop, the trace and the evals are the same with streaming on or off.
- **The cockpit types the reply** into the waiting bubble at the step where the board says the first words
  arrived. It works over a held-open stream and over the short requests a phone or a tunnel uses. A recorded run
  types the same text at the same step. In a terminal, `loopwright` prints the reply as it comes.
- **The output guard works inside the stream.** The secret patterns run on the text so far and the last 24
  characters are held back until the words after them arrive, so a credential is masked before any of it is
  shown. If a pattern only completes after part of it was released, the screen is corrected. The full guard still
  runs on the finished reply.
- **Models that answer in JSON** (Claude Code, and the text protocol for small local models) show only the
  answer inside the object, un-escaped as it grows, and nothing for a tool call.
- **A retry starts a new stream**, so a connection that drops half-way shows the answer beginning again instead
  of a spliced one.
- One new event, `model.stream`, with the time to first token; the totals are on the model call, and the receipt
  under each reply shows "first words after". Nothing is streamed when nobody is watching: evals, scheduled tasks
  and a piped `loopwright ask` get the reply whole.
- A Lab switch (Stream the reply), a lesson (Watching the reply arrive; 25 lessons), the Streaming handbook card
  now runs (85 of 95 do), and three eval cases with two new graders that read what was on the reader's screen
  (72 cases). 133 tests.
- Not yet run: streaming against a signed-in Claude Code, the hosted APIs or a real Ollama. Each is tested
  against a stand-in that sends that service's documented stream.

## 0.7.0

Exercises where you write the code.

- **Twelve exercises**, each one small function a real agent needs: a tool, the agent loop, retry with backoff,
  a limit enforced in code, an idempotent action, a token budget, chunking, reciprocal rank fusion, an injection
  filter, a trajectory grader, an eval case with teeth (run by the real eval runner, with and without memory),
  and a bug to find from a trace. Each has a brief, starter code, three hints, a reference solution, the
  interview question it answers, and a pointer to the full version in the repository.
- **A checker that explains.** Every check has a name in plain words and, when it fails, what was expected and
  what came back, with the line of your file if your code raised. What your code printed is shown too.
- **An editor in the cockpit** (Learn, then Exercises), usable from a phone, and `loopwright exercise` for a
  terminal and your own editor. The tool exercise ends with a button that adds your tool to your agent.
- Answers run in a child process with a time limit and, where available, the shell tool's OS sandbox.
  `docs/security.md` says what that does and does not protect.

## 0.6.0

Preparation for forward-deployed engineering interviews.

- **Thirteen new interview scenarios** for the rounds particular to forward-deployed roles: two open case
  studies, the first customer meeting, a failing pilot where the customer wants fine-tuning, the security
  review, webhooks that arrive twice and out of order, a system of record with no API, retrieval over ten
  million documents, documents that disagree, an agent allowed to spend money, the take-home, live coding of the
  agent loop, and the behavioural questions. 25 scenarios in all, grouped by round in the Learn tab.
- **Be interviewed on this.** A button on every scenario opens a chat in which the agent plays the interviewer,
  asks one follow-up at a time, and grades you when you say "grade me". It needs a real model.
- **`docs/fde-interviews.md`**: each round of a loop mapped to the lessons, cards and scenarios that prepare for
  it, a two-week plan, and what this project does not cover.
- **Four handbook cards** on what a customer deployment adds, each labelled as explained and not built here:
  multi-tenancy and permission-aware retrieval, data boundaries (PII, residency, retention), vector indexes at
  scale, and agent-to-agent protocols. 95 cards.

## 0.5.1

- **A foldable opened flat no longer breaks the cockpit.** Between 820 and 1000 px wide the two-pane layout did
  not fit: the header overflowed and the page slid sideways as the architecture followed the active box. That
  range now uses one pane with the whole architecture visible, and the board pans inside its own area instead
  of scrolling the page.
- On phones: suggestions sit in one row that scrolls sideways, tab rows fade at the edge instead of being cut,
  and the tools table is one card per tool.
- **One link for a codespace.** `bash deploy/codespace.sh public` makes the public cockpit page the single way
  in, on any device. The page shows a Wake button when the machine is asleep and connects by itself once it
  answers. `serve --wake-url` puts the wake address in the phone link. Off by default: it opens the port to the
  internet with only the access key in front of it.

## 0.5.0

Your agent in the cloud, for when your computer is off.

- **A dev container for GitHub Codespaces.** Creating a codespace from the repository installs Loopwright, Claude
  Code and a browser, starts the cockpit on every boot, and prints a link for your phone and a link that wakes
  the codespace. `docs/cloud.md`.
- **Turns no longer depend on a stream surviving the trip.** Cloudflare's quick tunnels do not pass Server-Sent
  Events, and a locked phone drops them. Away from its own network the page now runs a turn (or an eval run, or a
  comparison) as a job on the server and collects the messages with short requests, resuming after a gap.
- `loopwright serve --keep-key` keeps one access key across restarts; `--public-url` now also tells the server
  its own public origin, for forwarders that rewrite the Host header; `--page none` lets no outside page in;
  `--log-requests` prints a line per request with the key masked.
- The sign-in cookie is `SameSite=Lax`, so a link tapped on another site still signs in. Requests that change
  anything are still checked against their origin.
- Checked on a real codespace: set-up, the cockpit behind GitHub's port forwarding, a polled turn at phone size,
  an approval, the browser agent. Not checked: Claude Code signed in there, waking from a phone, and whether
  use keeps the codespace awake.

## 0.4.0

Your own agent on the public page.

- **`loopwright serve --tunnel`.** Starts a Cloudflare quick tunnel and prints a link and a QR code. The link opens
  the public cockpit page connected to your computer: the phone is the screen, and the agent, its memory and the
  model (Claude Code, a local model) stay on the computer. `--public-url` does the same for a tunnel you run
  yourself, `LOOPWRIGHT_KEY` fixes the key, and `--page` points the link at your own copy of the page.
- **A Connect button on the public page.** It explains the two steps, accepts a pasted link, remembers the
  connection in the browser, and says which of three things went wrong when the computer does not answer.
- **The server lets one outside page in.** Only while a key exists, only from the public page's origin, and only
  when each request carries the key in a header; a cookie is not accepted from another origin. A request that
  arrives through a tunnel is never treated as local, even if the tunnel rewrites the Host header.
- A QR code is drawn in the terminal for `--lan` too. The key is longer (16 hex characters).
- There is no Claude sign-in on the page, by design: Anthropic does not allow other sites to offer one. Claude
  Code signs in on your computer.

## 0.3.0

A simpler cockpit, and a phone.

- **One clear architecture.** The default drawing is now eighteen boxes in three bands: the turn left to right
  along the top (You, Gateway, Context, Model and Tools in a loop, Reply), memory below it, LLM-Ops at the bottom.
  Related mechanisms share a box and guardrails are drawn as a layer. A switch shows the previous drawing, one box
  per mechanism.
- **Two panes instead of three.** Conversation and architecture. The step list and the full data of a step open
  as a third pane only when asked for: from a step card, a box, the caption, or the Steps button.
- **Step cards under every reply.** One card per thing the agent did, with the kind of step, its name, the
  arguments that went in and the result that came out.
- **Clicking a box** explains what that part is for and steps through what it did in the run on screen.
- **Phones.** One pane at a time with a switch; the architecture pans to follow the active box. `loopwright serve
  --lan` makes the cockpit reachable from a phone on the same Wi-Fi with an access key. The public demo is
  published from a `gh-pages` branch.

## 0.2.1

Found by the first runs on a real machine.

- The local provider no longer uses the first model the server lists. It picks one that can call tools (asking
  Ollama what each model can do, then falling back to the family name) and never prefers a vision or embedding
  model. `loopwright doctor` and `loopwright selftest` say which model was picked.
- The self-test and comparison cases now also require a readable reply: not empty, and no tool-calling markup
  leaking into the text. Before, a case could pass on behaviour while the reply was blank or garbled.
- An empty model reply is replaced with a plain message instead of being sent as a blank.
- Every result of the offline demo search can be opened with `fetch_url`.
- First live result: 8 of 8 self-test cases through a signed-in Claude Code CLI on macOS.

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
