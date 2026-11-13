# Running offline, and with open-source models

Loopwright has no runtime dependencies and the cockpit loads nothing from the internet: fonts, scripts and the
handbook are all in the package. Two ways to use it with no connection at all.

## 1. The scripted model (nothing to install)

With no key and no local server, Loopwright uses its built-in scripted model. Every lesson, the whole handbook,
replay, the evals and the Lab work on it. It is a rule-based stand-in, not an LLM, so it only understands the
request shapes the lessons use.

## 2. A real model on your own machine

Any runner that serves the OpenAI chat API on localhost works. Do the download while you still have a network.

```bash
# Ollama
ollama pull qwen2.5:7b        # or any chat model; pick one whose page lists tool support
ollama serve                  # if it is not already running

loopwright doctor             # "ready  local  Ollama at http://127.0.0.1:11434: qwen2.5:7b"
loopwright serve              # the Model menu lists each local model
loopwright ask "Book a catch-up with Alex on Friday" --provider local --model qwen2.5:7b --steps
loopwright selftest --provider local
```

| Runner | Found automatically at | |
|---|---|---|
| Ollama | `http://127.0.0.1:11434` | `ollama serve` |
| LM Studio | `http://127.0.0.1:1234` | start the local server in the app |
| llama.cpp | `http://127.0.0.1:8080` | `llama-server -m model.gguf` |
| anything else (vLLM, Jan, LocalAI, a box on your LAN) | set `LOOPWRIGHT_LOCAL_URL=http://host:port` | |

`LOOPWRIGHT_MODEL` picks the model; otherwise the first one the server lists is used.

**It is also the automatic fallback.** If your normal provider is Claude Code or an API and the network drops,
the model gateway retries, then falls back to a local model if one is running, then to the scripted model. You see
each of those steps on the board.

### Tool calling on local models

Hosted models have a dedicated channel for tool calls. Local models vary, so
[`models/local.py`](../loopwright/models/local.py) degrades in three steps and remembers which one worked for each
model:

1. **Native**: the tool schemas are sent the standard way.
2. **Recovered**: the model accepted the tools but wrote the call as JSON in its reply; that JSON is parsed.
3. **Text protocol**: the server refused the request ("does not support tools"), so the tools are described in the
   system prompt and the model is asked for one JSON object, the same way the Claude Code adapter works.

### What to expect

A 7B model on a laptop is a noticeably weaker agent than a hosted model: it picks the wrong tool more often, loses
track on multi-step requests and takes seconds to minutes per step. That is a good thing to watch. The board shows
exactly where it goes wrong, and the tool gate, budgets and repeat detection are what keep those mistakes harmless.
`loopwright selftest --provider local` tells you which of the eight behaviours your model manages.

## What needs a network, and what happens without one

| Feature | Offline behaviour |
|---|---|
| `web_search` | Fails with a message telling the model to answer without the web. Set the backend to `demo` (Lab, or `LOOPWRIGHT_SEARCH=demo`) for a small bundled corpus |
| `fetch_url` | `demo://` pages work; real URLs fail with a readable error |
| Claude Code, Anthropic, OpenAI providers | Retried, then the fallback chain takes over |
| MCP servers started with `npx`, `uvx` and similar | Need whatever they download to be cached already |
| Everything else | Works |

## Status

The local provider is tested over real HTTP against a stub server that behaves like each of the three kinds of
model above, including full turns through the harness and the offline fallback. It has not been run against every
runner and model; if yours misbehaves, `loopwright ask "..." --provider local --steps` shows the exchange.
