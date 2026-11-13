# Workflows and scheduled tasks

Two ways the agent works without a person steering each step: along a path fixed in advance, and on a clock.
Both run through the same gateway, tool gate, guardrails and trace as a normal turn, so both can be replayed.

## Workflow or agent?

| | Agent | Workflow |
|---|---|---|
| Who decides the next step | the model, each round | your code, in advance |
| Good for | tasks whose steps you cannot predict | tasks you can draw |
| Cost and speed | a model call per step | model calls only where judgement is needed |
| Testing | on behaviour, with trials | step by step, deterministically |
| Failure | can wander, loop or stop early | does only what was drawn |

Type the same sentence both ways and compare the traces:

```
/workflow triage: what is a harness?      5 steps, the route picks a branch, 4 model calls
what is a harness?                        the agent loop decides to search the handbook
```

## Running one

```bash
loopwright workflow                              # list them
loopwright workflow morning-brief                # run, printing each step
loopwright workflow triage "buy milk tomorrow"
```

In any chat (terminal, cockpit, Telegram...): `/workflow research-digest: agent evaluation`. In the cockpit:
Automations, Workflows, **Run and watch**. The agent can also pick one itself through the `run_workflow` tool
("run the morning brief workflow").

## The three that ship

| Workflow | Pattern | What it shows |
|---|---|---|
| `morning-brief` | parallelisation, then synthesis | Three lookups run in one round; the model is called once, to write. |
| `triage` | routing | One classification call picks a branch: research (a small agent loop), capture (a tool call) or reply. The other branches are skipped. |
| `research-digest` | prompt chaining with a gate | Search, write a digest, then plain code checks that it cites a source before saving it. |

## Writing your own

A workflow is a TOML file in `~/.loopwright/workflows/`. Copy one from
[`loopwright/flows/`](../loopwright/flows/) and change it. `loopwright workflow --check file.toml`
validates a file and prints its rounds.

```toml
name = "standup"
description = "Yesterday's notes and today's calendar as three bullets."

[[node]]
id = "notes"
kind = "tool"                      # call a tool directly; no model involved
tool = "search_notes"
args = { query = "" }

[[node]]
id = "agenda"
kind = "tool"
tool = "list_events"
args = { date_from = "{{today}}", date_to = "{{today}}" }

[[node]]
id = "write"
kind = "model"                     # one model call, fixed prompt, no tools
after = ["notes", "agenda"]        # runs when both are done; they run in parallel
output = true
prompt = """
Write a three-bullet standup (done, doing, blocked) from:

## Notes
{{notes}}

## Calendar
{{agenda}}
"""
```

Four kinds of node:

| kind | What it does | Fields |
|---|---|---|
| `tool` | Calls one tool with fixed arguments, through the tool gate | `tool`, `args` |
| `model` | One model call with a fixed prompt and no tools | `prompt` |
| `agent` | A bounded agent loop with a narrow prompt and a short tool list | `prompt`, `tools` |
| `route` | Picks which node runs next | `value`, `choice` list, `default`, optional `by = "model"` |

- `after = [...]` draws the edges. Nodes with all inputs ready run in the same round, in parallel threads.
- `{{node_id}}` is replaced by that node's output. Also available: `{{input}}`, `{{today}}`, `{{tomorrow}}`,
  `{{now}}`. `{{input|fallback}}` supplies a default.
- A route with `contains = [...]` choices is plain code. With `by = "model"` and `when = "..."` on each choice,
  one small model call picks from the list.
- A node directly after a route runs only if the route chose it. A node is skipped when everything it depends on
  was skipped, so a join after alternative branches runs once one of them has.
- The reply is the output of the node marked `output = true`, or of the last node that ran.

**A workflow does not bypass the tool gate.** A hard-coded `send_message` step after a `web_search` step is
refused exactly as it would be for an agent, and the step is reported as blocked. There is an eval case for it
(`auto-05`).

## Scheduled tasks

A scheduled task is a saved prompt plus a schedule. When it is due, a clock starts an ordinary turn with it.

```bash
loopwright tasks add "weekdays 08:00" "What is on my calendar today, and what are my recent notes?"
loopwright tasks add "every 30m" "/workflow morning-brief"       # a workflow on a schedule
loopwright tasks                    # list
loopwright tasks run 1              # run it now
loopwright tasks pause 1            # and resume, rm
```

Or ask in any chat: *"Every weekday at 8am, tell me what's on my calendar"*. You will be asked to approve it. Or
use Automations, Scheduled tasks in the cockpit.

Schedules:

```
every 30m          every 2h            hourly
daily 08:00        weekdays 08:00      weekends 10:30
weekly mon 09:00   monthly 1 09:00
once 2026-11-20 14:00
0 8 * * 1-5        five-field cron (minute hour day month weekday; 0 = Sunday)
```

Plain phrases are understood too: "every weekday at 8am", "every monday at 9am", "daily 7:30pm".

**When tasks run.** While `loopwright serve` or any chat gateway is running: each checks the clock every twenty
seconds or so. For a machine where neither is up, call `loopwright tasks tick` from your system's own scheduler.
If two of them are running, a due task is claimed in the database with a compare-and-set, so it runs once.

**What a task may do.** Nobody is watching a scheduled run, so its approval policy is `deny`: any tool with
outside effects (`send_message`, `browse_web`, `run_command`, `schedule_task`, MCP tools that need approval) is
refused in code. A task can read, calculate, search, take notes, book calendar entries and report.

**Why creating one needs approval.** A task is the agent leaving an instruction for its future self. If text on a
web page could get one created, the injection would run again tomorrow with nobody watching. So `schedule_task`
is an outside-effect tool: you approve it, and it is denied outright in a turn that has read untrusted content.

**Where results go.** To the chat the task was created from (Telegram, Discord, Slack, WhatsApp), to Automations
in the cockpit, and to History as the session `task-<n>`. Every run is a trace you can replay.

A "heartbeat" (wake up periodically and see whether anything needs doing) is `every 30m` with a prompt such as
"Check my calendar for the next hour and tell me only if something starts soon."
