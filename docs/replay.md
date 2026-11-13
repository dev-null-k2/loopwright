# Replay

A turn on the scripted model finishes in a few milliseconds, and one on a real model still emits its steps faster
than anyone can read. So the cockpit never shows a turn "live" at full speed. It always plays it.

## How it works

1. Every step emits one event ([`events.py`](../loopwright/events.py)).
2. `POST /api/chat` streams those events to the browser as Server-Sent Events while the turn runs.
3. The browser appends them to a list. The **player** ([`web/js/player.js`](../loopwright/web/js/player.js))
   walks that list at about one step per 0.9 seconds at 1x, slower on warnings and errors. If it reaches the end
   while the turn is still running, it waits for the next event.
4. When the turn ends, the same events are already on disk as `~/.loopwright/traces/<turn>.jsonl`. Loading an old
   run fetches that file and hands it to the same player.

Live and recorded runs are therefore the same code path. There is no separate replay mode to drift out of date.

## Controls

| Control | Key | |
|---|---|---|
| Play / pause | `Space` | Playing from the end starts over |
| Step back / forward | `Left` `Right` | Pauses first |
| First / last step | `Home` `End` | |
| Scrub | drag the strip | One cell per step, coloured by subsystem; hatched cells are warnings, refusals or errors; short cells are worker steps |
| Speed | 0.4x to 4x | Remembered between visits |
| Hide worker steps | checkbox | Shows a subagent run the way the orchestrator saw it |
| Show every box | checkbox | Off by default: boxes this run never touched are dimmed |

Three more ways to move: click a row in the step list, click a row in the list of actions under a chat reply, or
click a "Show step N" link in a lesson.

If a turn stops for your approval, the player jumps to that step and waits there.

## What replay is not

A replay shows the record. It does not call the model again. To find out whether a change fixes a failure, add
the case to an eval and run it several times: see [evals.md](evals.md).

## In the terminal

```bash
loopwright runs                    # list traces
loopwright replay last             # step through the latest, with explanations
loopwright replay <turn-id> --speed 0 --brief
```
