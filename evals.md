# Evals

```bash
loopwright eval              # run everything, print the tables, write results/eval-report.json
loopwright eval --gate       # the same, and exit 1 if the release gate fails (CI runs this)
loopwright eval --judge      # also run the model-graded cases; needs a real model
loopwright eval --mine       # only the cases you saved from your own runs
loopwright compare --targets scripted local claude-code     # the same questions through several models
```

## What runs

| Part | What it is | Where |
|---|---|---|
| Suites | `core` (tool routing, skills, patterns), `memory`, `safety`, `reliability`, `context`, `automation` (workflows, scheduled tasks, the shell, the browser). 69 cases | [`evals/datasets/*.jsonl`](../loopwright/evals/datasets/) |
| Trials | Reliability cases run three times with failures injected at different points. `pass_at_1` is the mean; `pass_all_trials` is the share that passed every time (pass^3) | `trials` in a case |
| Ablations | Every suite again with one mechanism off: `no-memory`, `always-retrieve`, `no-guardrails`, `no-retries` | `VARIANTS` in [`runner.py`](../loopwright/evals/runner.py) |
| Retrieval | 44 labelled questions against the handbook; recall@1 and @3 for BM25, dense and hybrid | `retrieval.jsonl` |
| Gate | 30 labelled messages against the retrieval gate's rules; accuracy, false skips, false retrieves | `gate.jsonl` |
| Judge | 5 rubric-graded cases. Skipped without a real model; there is deliberately no offline judge | [`judge.py`](../loopwright/evals/judge.py) |

The five `automation` cases marked `"needs": "browser"` start a real headless browser. On a machine with no
Chromium-family browser they are left out, so the totals there are 64 cases and four attacks. With a browser the
whole run takes about half a minute; without one, a few seconds.

Each case gets a new state folder and a frozen clock (Thursday 2026-11-12), so cases cannot leak into each other
and "Friday" always means the same date.

## Writing a case

One JSON object per line:

```json
{"id": "mem-04", "title": "A changed preference supersedes the old one", "concept": "consolidation",
 "turns": ["Remember that Alex prefers morning meetings", "Remember that Alex prefers afternoon meetings",
           "Book a catch-up with Alex on Friday"],
 "expect": [{"fact_stored": "afternoon"}, {"fact_superseded": "morning"}, {"fact_count": 1},
            {"tool_args": {"tool": "create_event", "contains": {"time": "14:00"}}}]}
```

`setup` seeds facts, history, notes, calendar events, scheduled tasks, workspace files, workflow files, or
switches on an opt-in tool (`"enable_tools": ["run_command"]`). The turn `"@scheduler"` lets the clock run
whatever is due, unattended. `options` sets any field of `Options`. A turn can be
`{"say": "...", "session": "other"}` to cross sessions. `"attack": true` counts the case towards the attack
success rate, measured by whether the outbox ends up non-empty.

Graders ([`graders.py`](../loopwright/evals/graders.py)) are assertions over the result and the trace:
`tool_called`, `tool_not_called`, `tool_args`, `reply_contains`, `reply_not_contains`, `event`, `no_event`,
`section_present`, `section_absent`, `ok`, `max_model_calls`, `max_tool_calls`, `fact_stored`, `fact_not_stored`,
`fact_superseded`, `fact_count`, `episode_count_min`, `outbox_count`, `calendar_count`, `note_contains`,
`note_not_contains`, `task_count`, `task_schedule`. `event` also takes `"data": {...}` to match fields of the event,
as in `{"event": {"kind": "workflow.route", "data": {"chose": "research"}}}`. Most read the trace, which is what lets a case assert that something was *blocked*.

## The release gate

[`gate.py`](../loopwright/evals/gate.py). Two kinds of rule:

- **The agent is good enough**: every deterministic case passes, no attack succeeds, retrieval and the gate
  clear a floor set a little under today's measured numbers.
- **The evals can still fail**: with guardrails off the attacks must succeed; with memory off a memory case must
  fail; with retries off a reliability case must fail; with the gate forced open the gate case must fail; with
  guardrails off an automation case must fail.

## Comparing models

`loopwright compare`, or Evals, Compare models in the cockpit ([`compare.py`](../loopwright/evals/compare.py)).
Each target runs the eight behaviour cases from `live.jsonl` (the ones `loopwright selftest` uses) in fresh state,
with no fallback, so a failure on a model shows as a failure. A target is a provider, `provider:model`, or either
with `+setting=value`:

```bash
loopwright compare --targets scripted local:qwen2.5:7b local:llama3.2:3b claude-code
loopwright compare --targets claude-code claude-code+pattern=plan      # two configurations of one model
```

Every cell keeps its trace, so a failure on one model can be replayed beside a pass on another. If a provider
cannot be reached, its first case is marked unreachable and the rest are skipped. Eight cases are a smoke test,
not a verdict: for a real decision use your own cases and several trials.

## Your own cases

Under every reply in the cockpit: **Good**, **Bad**, and **Save as eval case**
([`mine.py`](../loopwright/evals/mine.py)). Saving builds a case from that run's trace: the message, plus
assertions on what the run did (each tool that ran, whether the gate retrieved, whether anything was refused). It
is appended to `~/.loopwright/evals/mine.jsonl` in the format above, so you can tighten it by hand.
`loopwright eval --mine` and Evals, Your cases run them on the model you have selected. They are yours, so they
are not part of the release gate. Feedback is kept in `~/.loopwright/feedback.jsonl`.

## Limits, stated plainly

- Everything except `--judge` runs on the scripted model. These evals test the harness. They would pass
  unchanged with a brilliant or a terrible LLM behind the same interface.
- The cases were written by the author of the code. Passing them is a regression bar.
- To evaluate a real model, run `--judge`, or call `run_case(case, {"provider": "claude-code"})` several times
  per case; real models are non-deterministic, so report rates, not single runs.
