"""SCHEDULED TASKS: turns that a clock starts instead of a person.

    "Every weekday at 8, tell me what's on my calendar."

A scheduled task is a saved prompt plus a schedule. When it is due, the
scheduler runs an ordinary turn with that prompt. Nothing about the turn is
special except who started it, and that one difference changes the safety
picture, which is why this file is short and its rules are strict:

  nobody is watching   A person approves risky actions in a normal turn. Here
        there is no person, so the approval policy is "deny": any tool with
        outside effects is refused in code. A scheduled task can read, think,
        write notes and report. It cannot send, browse or run commands.
  creating one needs approval   A task is the agent instructing its future
        self. If a poisoned web page could create one, the attack would
        outlive the conversation. So `schedule_task` is an external-effect
        tool: a human approves it, and it is denied in a tainted turn.
  it runs once   The web server and a chat gateway may both be running, each
        with its own scheduler thread. A due task is claimed with a
        compare-and-set in the database, so exactly one of them runs it.
  results go back where the request came from   A task created from Telegram
        is delivered to that Telegram chat; one created in the browser shows
        up on the Automations page and in History.

Schedules, in the forms a person would say them:

    every 30m            every 2h
    daily 08:00          weekdays 08:00       weekends 10:30
    weekly mon 09:00     monthly 1 09:00
    once 2026-11-20 14:00
    0 8 * * 1-5          (five-field cron, for people who think in cron)

A "heartbeat" (wake up every half hour and see if anything needs doing) is
just `every 30m` with a prompt like "check my calendar for the next hour".
"""

from __future__ import annotations

import datetime as dt
import re
import threading
from collections.abc import Callable
from typing import Any

from loopwright.config import Options, env_options
from loopwright.tools.registry import Tool, ToolContext, ToolError

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
TIME = r"(\d{1,2}):(\d{2})"


def _field(spec: str, low: int, high: int) -> set[int]:
    """One cron field: '*', '*/15', '1-5', '1,3,5' or a mix."""
    values: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            step = int(step_text)
        if part == "*":
            start, end = low, high
        elif "-" in part:
            start, end = (int(x) for x in part.split("-", 1))
        else:
            start = end = int(part)
        if not (low <= start <= end <= high) or step < 1:
            raise ValueError(f"'{spec}' is out of range {low}-{high}")
        values.update(range(start, end + 1, step))
    return values


def normalise(schedule: str) -> str:
    """Tidy a schedule and check it. Raises ValueError with a helpful message."""
    text = " ".join(schedule.lower().replace(",", " ").split())
    if len(text.split()) != 5 or not re.fullmatch(r"[\d*/,\- ]+", text):          # leave cron expressions alone
        text = re.sub(r"\b(\d{1,2}):(\d{2})\s?(am|pm)\b",
                      lambda m: f"{(int(m.group(1)) % 12) + (12 if m.group(3) == 'pm' else 0):02d}:{m.group(2)}", text)
        text = re.sub(r"(?<![:\d])(\d{1,2})\s?(am|pm)\b",
                      lambda m: f"{(int(m.group(1)) % 12) + (12 if m.group(2) == 'pm' else 0):02d}:00", text)
        text = re.sub(r"\b(at|on) ", "", text)
        text = re.sub(r"^every (day|morning|evening|night) ", "daily ", text)
        text = re.sub(r"^every weekday ", "weekdays ", text)
        text = re.sub(r"^every weekend ", "weekends ", text)
        text = re.sub(r"^every (\d+) ?(m|min|mins|minutes?)$", r"every \1m", text)
        text = re.sub(r"^every (\d+) ?(h|hr|hrs|hours?)$", r"every \1h", text)
        text = re.sub(r"^(every hour|hourly)$", "every 1h", text)
        text = re.sub(r"^every (mon|tue|wed|thu|fri|sat|sun)[a-z]* ", r"weekly \1 ", text)
        text = re.sub(r"^weekly (mon|tue|wed|thu|fri|sat|sun)[a-z]* ", r"weekly \1 ", text)
        text = re.sub(r"\b(\d):(\d{2})$", r"0\1:\2", text)
    next_after(text, dt.datetime(2026, 1, 1))          # raises if it cannot be understood
    return text


def next_after(schedule: str, after: dt.datetime) -> dt.datetime | None:
    """The first run strictly after `after`. None when a one-off is in the past."""
    after = after.replace(second=0, microsecond=0)
    m = re.fullmatch(r"every (\d+)(m|h)", schedule)
    if m:
        minutes = int(m.group(1)) * (60 if m.group(2) == "h" else 1)
        if minutes < 1:
            raise ValueError("the shortest interval is one minute")
        return after + dt.timedelta(minutes=minutes)
    m = re.fullmatch(rf"once (\d{{4}}-\d{{2}}-\d{{2}}) {TIME}", schedule)
    if m:
        when = dt.datetime.fromisoformat(f"{m.group(1)}T{int(m.group(2)):02d}:{m.group(3)}")
        return when if when > after else None

    m = re.fullmatch(rf"(daily|weekdays|weekends) {TIME}", schedule)
    w = re.fullmatch(rf"weekly ({'|'.join(DAYS)}) {TIME}", schedule)
    mo = re.fullmatch(rf"monthly (\d{{1,2}}) {TIME}", schedule)
    cron = schedule.split()
    if m:
        days = {"daily": set(range(7)), "weekdays": set(range(5)), "weekends": {5, 6}}[m.group(1)]
        minutes, hours, dom, months = {int(m.group(3))}, {int(m.group(2))}, set(range(1, 32)), set(range(1, 13))
    elif w:
        days, hours, minutes, dom, months = {DAYS.index(w.group(1))}, {int(w.group(2))}, {int(w.group(3))}, set(range(1, 32)), set(range(1, 13))
    elif mo:
        days, dom, hours, minutes, months = set(range(7)), {int(mo.group(1))}, {int(mo.group(2))}, {int(mo.group(3))}, set(range(1, 13))
    elif len(cron) == 5:
        try:
            minutes, hours, dom, months = _field(cron[0], 0, 59), _field(cron[1], 0, 23), _field(cron[2], 1, 31), _field(cron[3], 1, 12)
            days = {(d - 1) % 7 for d in _field(cron[4].replace("7", "0"), 0, 6)}       # cron: 0 = Sunday. Python: 0 = Monday.
        except ValueError as exc:
            raise ValueError(f"bad cron expression: {exc}") from exc
    else:
        raise ValueError("I could not read that schedule. Try: every 30m, daily 08:00, weekdays 08:00, "
                         "weekly mon 09:00, monthly 1 09:00, once 2026-11-20 14:00, or five-field cron")
    if max(hours) > 23 or max(minutes) > 59:
        raise ValueError("that is not a time of day")
    day = after.date()
    for _ in range(366 * 5):
        if day.weekday() in days and day.day in dom and day.month in months:
            for hour in sorted(hours):
                for minute in sorted(minutes):
                    when = dt.datetime.combine(day, dt.time(hour, minute))
                    if when > after:
                        return when
        day += dt.timedelta(days=1)
    return None


def describe(schedule: str) -> str:
    m = re.fullmatch(r"every (\d+)(m|h)", schedule)
    if m:
        return f"every {m.group(1)} {'minute' if m.group(2) == 'm' else 'hour'}{'s' if m.group(1) != '1' else ''}"
    first, _, rest = schedule.partition(" ")
    words = {"daily": "every day at", "weekdays": "Monday to Friday at", "weekends": "Saturday and Sunday at",
             "weekly": "every week on", "monthly": "every month on day", "once": "once, on"}
    return f"{words[first]} {rest}" if first in words else f"cron {schedule}"


class Scheduler:
    def __init__(self, agent, options: Options | None = None, on_result: Callable[[dict[str, Any], Any], None] | None = None):
        self.agent, self.options, self.on_result = agent, options, on_result
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_task(self, task: dict[str, Any], sinks: list | None = None, turn_id: str | None = None):
        """One unattended turn. approvals=deny is the whole safety story."""
        base = (self.options or env_options()).to_dict()
        options = Options.from_dict({**base, "approvals": "deny"})
        result = self.agent.run_turn(task["prompt"], session=f"task-{task['id']}", options=options, channel="schedule",
                                     origin=task.get("origin", ""), sinks=sinks, turn_id=turn_id,
                                     trigger={"task_id": task["id"], "schedule": task["schedule"], "runs": task.get("runs", 0) + 1})
        self.agent.store.finish_task(task["id"], "ok" if result.ok else "failed", result.reply, result.turn_id, self.agent.now().timestamp())
        if task.get("origin") and ":" in task["origin"] and not task["origin"].startswith(("web:", "local:")):
            self.agent.store.add_delivery(task["origin"], f"Scheduled task #{task['id']} ({task['prompt'][:60]}):\n\n{result.reply}")
        if self.on_result:
            self.on_result(task, result)
        return result

    def tick(self, now: dt.datetime | None = None) -> list[Any]:
        """Run everything that is due. Safe to call from several processes."""
        now = now or self.agent.now()
        results = []
        for task in self.agent.store.due_tasks(now.timestamp()):
            upcoming = next_after(task["schedule"], now)
            if self.agent.store.claim_task(task["id"], task["next_run"], upcoming.timestamp() if upcoming else None):
                results.append(self.run_task(task))
        return results

    def start(self, every: float = 20) -> None:
        def loop() -> None:
            while not self._stop.wait(every):
                try:
                    self.tick()
                except Exception:        # one bad task must not stop the clock
                    continue
        self._thread = threading.Thread(target=loop, daemon=True, name="loopwright-scheduler")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


# ------------------------------------------------------------------ the tools

def add_task(store, prompt: str, schedule: str, now: dt.datetime, origin: str = "") -> dict[str, Any]:
    schedule = normalise(schedule)
    upcoming = next_after(schedule, now)
    if upcoming is None:
        raise ValueError("that time is already in the past")
    task_id = store.add_task(prompt.strip(), schedule, upcoming.timestamp(), origin)
    return {"id": task_id, "prompt": prompt.strip(), "schedule": schedule, "runs": describe(schedule),
            "next_run": upcoming.strftime("%A %Y-%m-%d %H:%M")}


def schedule_task(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    try:
        task = add_task(ctx.store, args["prompt"], args["schedule"], ctx.now(), ctx.origin)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    ctx.rec.emit("schedule.create", "scheduler", f"Task #{task['id']} scheduled: {task['runs']}",
                 "A prompt was saved with a schedule. From now on a clock, not a person, will start this turn. "
                 "Those runs are unattended, so tools with outside effects are denied in them.",
                 src="tools", data=task)
    return {**task, "note": "runs unattended: it can read, write notes and report, but not send, browse or run commands"}


def list_tasks(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    rows = ctx.store.tasks()
    return {"tasks": [{"id": r["id"], "prompt": r["prompt"], "schedule": r["schedule"], "enabled": bool(r["enabled"]),
                       "next_run": dt.datetime.fromtimestamp(r["next_run"]).strftime("%Y-%m-%d %H:%M") if r["next_run"] else "never",
                       "runs": r["runs"], "last_status": r["last_status"]} for r in rows], "count": len(rows)}


def cancel_task(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    if not ctx.store.delete_task(int(args["id"])):
        raise ToolError(f"there is no task #{args['id']}")
    return {"cancelled": int(args["id"])}


def scheduler_tools() -> list[Tool]:
    obj = lambda props, req: {"type": "object", "properties": props, "required": req, "additionalProperties": False}  # noqa: E731
    return [
        Tool("schedule_task", "Schedule a prompt to run automatically later or on a repeating schedule, when the user "
             "asks for something every day, every hour, each Monday, or at a set time. The user must approve it.",
             obj({"prompt": {"type": "string", "description": "what to do each time, written as an instruction to yourself"},
                  "schedule": {"type": "string", "description": "every 30m | daily 08:00 | weekdays 08:00 | weekly mon 09:00 | "
                                                                "monthly 1 09:00 | once YYYY-MM-DD HH:MM | five-field cron"}},
                 ["prompt", "schedule"]), schedule_task, effect="external", stage="scheduler"),
        Tool("list_tasks", "List the user's scheduled tasks.", obj({}, []), list_tasks, stage="scheduler"),
        Tool("cancel_task", "Cancel a scheduled task by its number.", obj({"id": {"type": "integer"}}, ["id"]),
             cancel_task, effect="write", stage="scheduler"),
    ]
