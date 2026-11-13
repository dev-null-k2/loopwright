"""THE SCRIPTED MODEL: a rule-based stand-in for an LLM. Read this before trusting it.

This is not a language model. It is a few hundred lines of pattern matching
that plays the model's part: given the system prompt, the conversation and
the tool results so far, it returns either tool calls or a final answer.

Why it exists:

  - Loopwright runs the moment it is cloned, with no key and no network.
  - Evals are deterministic. A test of the *harness* (did the gate skip? was
    the injection blocked? did the retry happen?) should not flake because a
    model phrased something differently.
  - CI is free.

What it can do: the request shapes listed in INTENTS below (remembering,
scheduling, notes, arithmetic, handbook questions, comparisons via subagents,
fetching pages, messages). Anything else gets an honest "connect a real
model" reply.

One deliberate property: it is GULLIBLE. If a tool result contains injected
instructions and the harness has not fenced that content, the scripted model
obeys them. Real models resist many injections but not all, and you cannot
measure a defence against a model that happens to resist. Standing in for the
worst case is what lets the safety evals show the defence working (0 attacks
succeed) and, with guardrails switched off, failing (every attack succeeds).
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from typing import Any

from loopwright.models.base import Message, ModelResponse, ToolCall, Usage, estimate_tokens, messages_text

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAYPARTS = [("early morning", "08:00"), ("morning", "09:00"), ("lunch", "12:30"), ("afternoon", "14:00"),
            ("evening", "18:00"), ("night", "19:00")]
MEETING = r"(meeting|catch[- ]?up|call|sync|coffee|lunch|1:1|one[- ]on[- ]one|chat|review|interview|dinner|session)"
URL = re.compile(r"((?:https?|demo)://[^\s\"'<>)]+)")
INJECTED = re.compile(r"ignore (all |any |your |the )?(previous|prior|above|earlier) (instructions|rules)|"
                      r"system (note|override|message)|new instructions", re.I)

CAPABILITIES = ("I can remember things across sessions, book and list calendar events, save and search notes, "
                "do exact arithmetic, answer questions about how AI agents are built (with citations from the "
                "handbook), compare two concepts using parallel subagents, search the web, fetch a page, and "
                "draft a message that you approve before it goes anywhere.")
LIMITS = ("I'm running on Loopwright's built-in scripted model, which only understands a fixed set of request "
          "shapes. " + CAPABILITIES + " For open-ended questions, connect Claude Code or an API key "
          "(run `loopwright doctor`).")


@dataclass
class Intent:
    kind: str
    clause: str
    slots: dict[str, Any] = field(default_factory=dict)


@dataclass
class Env:
    """What the scripted model reads out of its prompt, the way a real model would."""
    memory: list[str]
    today: dt.date
    tools: set[str]
    critic: str = ""


@dataclass
class Result:
    name: str
    data: Any
    raw: str
    is_error: bool


# ------------------------------------------------------------------ reading the prompt

def _env(system: str, tool_names: set[str], critic: str) -> Env:
    memory = []
    block = re.search(r"## Memory[^\n]*\n(.*?)(?=\n## |\Z)", system, re.S)
    if block:
        memory = [re.sub(r"\s*\[(fact|episode)[^\]]*\]\s*$", "", line[2:].strip())
                  for line in block.group(1).splitlines() if line.startswith("- ")]
    today = dt.date.today()
    stamp = re.search(r"Today is \w+,? (\d{4}-\d{2}-\d{2})", system)
    if stamp:
        today = dt.date.fromisoformat(stamp.group(1))
    return Env(memory, today, tool_names, critic)


def _turn(messages: list[Message]) -> tuple[str, list[Result], str]:
    """-> (the user's request, tool results since it, critic feedback if any)."""
    critic = ""
    index = 0
    for i, m in enumerate(messages):
        if m["role"] != "user":
            continue
        if str(m["content"]).startswith("[critic]"):
            critic = m["content"]
        else:
            index, critic = i, ""
    results = []
    for m in messages[index + 1:]:
        if m["role"] != "tool":
            continue
        raw = m["content"]
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            data = {"text": raw}
        results.append(Result(m.get("name", ""), data, raw, bool(m.get("is_error"))))
    return str(messages[index]["content"]) if messages else "", results, critic


# ------------------------------------------------------------------ understanding the request

SPLIT = re.compile(r"\s*(?:,?\s+and then\s+|,?\s+then\s+|;\s+|,?\s+and\s+(?=(?:save|book|schedule|send|email|"
                   r"remember|note|message|add|tell me|list|show)\b))", re.I)


def parse_intents(user: str) -> list[Intent]:
    text = user.strip()
    if re.match(r"^\s*(please\s+)?(remember|note|keep in mind|don'?t forget|forget|when(ever)? i|when asked)\b", text, re.I) or URL.search(text):
        clauses = [text]          # never split a fact to remember, or around a URL
    else:
        clauses = [c for c in SPLIT.split(text) if c and c.strip()]
    return [_classify(c.strip()) for c in clauses] or [Intent("smalltalk", text)]


def _classify(c: str) -> Intent:
    low = c.lower()
    m = re.match(r"^(?:please\s+)?(?:remember|note|keep in mind|don'?t forget)(?:\s+that)?[:,]?\s+(.+)$", c, re.I | re.S)
    if m and not re.match(r"^(save|take|add|write)\b", low):
        return Intent("remember", c, {"text": m.group(1).strip().rstrip(".")})
    m = re.match(r"^(?:please\s+)?forget\s+(?:that\s+|about\s+|what i said about\s+)?(.+?)[.!]*$", c, re.I)
    if m:
        return Intent("forget", c, {"query": m.group(1)})
    m = re.match(r"^(?:when(?:ever)? i (?:ask|say|want)(?: you)?(?: for| to)?|when asked (?:for|to))\s+(.+?),\s*(?:always |please )?(.+)$", c, re.I | re.S)
    if m:
        when = re.sub(r"^(a|an|the|my)\s+", "", m.group(1).strip().strip('"\''), flags=re.I)
        return Intent("teach", c, {"when": when, "how": m.group(2).strip()})
    m = re.search(r"\b(?:send|email|message|text|dm|tell)\b(?:\s+(?:a|an|the)\s+(?:message|email|note|text))?\s+(?:to\s+)?"
                  r"([A-Z][\w.\-]*(?:@[\w.\-]+)?)(?:\s*[:,]\s*|\s+(?:that|saying|to say)\s+)?(.*)$", c, re.S)
    if m and re.match(r"^(?:please\s+)?(send|email|message|text|dm|tell)\b", low) and m.group(1).lower() not in ("me", "us"):
        return Intent("send", c, {"to": m.group(1), "body": m.group(2).strip().strip('"')})
    if re.search(rf"\b(book|schedule|set up|arrange|plan|add|organi[sz]e)\b.*\b{MEETING}\b", low):
        return Intent("schedule", c, _schedule_slots(c))
    if re.search(r"(what'?s on my (calendar|schedule|agenda)|\bmy (calendar|schedule|agenda)\b|when am i (meeting|seeing)|"
                 r"upcoming (events|meetings)|do i have (any )?(meetings|events)|list (my )?(events|meetings))", low):
        return Intent("calendar", c)
    if re.search(r"^(?:please\s+)?(save|take|add|write|make)\b.*\bnote\b|^note:", low):
        m = re.search(r"note\b\s*(?:that|saying|:|-)?\s*(.*)$", c, re.I | re.S)
        text = (m.group(1) if m else "").strip().strip('"')
        return Intent("note_save", c, {"text": "" if re.fullmatch(r"(of )?(that|this|it)", text, re.I) else text})
    if re.search(r"(\bmy notes\b|find (a |the |my )?notes?|search (my )?notes|what notes|show (me )?(my )?notes)", low):
        m = re.search(r"(?:about|for|on|mentioning)\s+(.+?)[?.]*$", c, re.I)
        return Intent("note_search", c, {"query": m.group(1) if m else ""})
    m = URL.search(c)
    if m:
        return Intent("fetch", c, {"url": m.group(1).rstrip(".,")})
    m = re.search(r"\b(?:search (?:the )?(?:web|internet|online)(?: for)?|search for|look up|google|web search(?: for)?|"
                  r"find (?:me )?(?:news|articles|information|sources) (?:on|about)|(?:what(?:'s| is) the )?latest (?:news )?(?:on|about|in))\s+(.+?)[?.!]*$", c, re.I)
    if m:
        return Intent("search", c, {"query": m.group(1).strip()})
    m = (re.search(r"\b(?:read|open|show|cat)\b(?:\s+(?:me|the|my))*\s+file\s+(\S+)", c, re.I)
         or re.search(r"\b(?:read|open|show|cat)\b(?:\s+(?:me|the|my))*\s+([\w\-./\\]+\.\w{1,5})\b", c, re.I))
    if m:
        return Intent("read_file", c, {"path": m.group(1)})
    if re.search(r"\b(what time|current time|what'?s the (date|time)|today'?s date|what day is|what is the date)\b", low):
        return Intent("time", c)
    expr = _math_expr(c)
    if expr:
        return Intent("math", c, {"expression": expr})
    m = (re.search(r"\b(?:compare|contrast)\s+(.+?)\s+(?:and|with|to|vs\.?|versus|against)\s+(.+?)[?.!]*$", c, re.I)
         or re.search(r"\bdifference between\s+(.+?)\s+and\s+(.+?)[?.!]*$", c, re.I)
         or re.search(r"^(?:what(?:'s| is) (?:better|the tradeoff)[:,]?\s+)?(.+?)\s+(?:vs\.?|versus)\s+(.+?)[?.!]*$", c, re.I))
    if m:
        return Intent("compare", c, {"a": _topic(m.group(1)), "b": _topic(m.group(2))})
    m = (re.search(r"what do you (?:know|remember) about\s+(.+?)[?.!]*$", c, re.I)
         or re.search(r"^who is\s+([A-Z]\w+)[?.!]*$", c)
         or re.search(r"what does\s+([A-Z]\w+)\s+(?:prefer|like|want)", c)
         or re.search(r"\b(?:where do i (?:live|work)|what(?:'s| is) my \w+|do you remember)\b(.*)$", c, re.I))
    if m:
        return Intent("recall", c, {"about": (m.group(1) or "").strip() or "me"})
    if re.match(r"^\s*(hi|hello|hey|yo|thanks|thank you|ok|okay|cool|great|bye|good (morning|evening|night))\b[\s!.]*$", low):
        return Intent("smalltalk", c)
    if re.search(r"\b(who are you|what can you do|what are you|help me get started|what should i try)\b", low):
        return Intent("capabilities", c)
    return Intent("kb", c, {"query": c})


def _topic(text: str) -> str:
    return re.sub(r"^(the|a|an)\s+", "", text.strip().strip('"'), flags=re.I)


def _math_expr(c: str) -> str:
    low = c.lower().replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent)\s+of\s+(\d+(?:\.\d+)?)", low)
    if m:
        return f"{float(m.group(1)) / 100:g} * {m.group(2)}"
    m = re.search(r"square root of\s+(\d+(?:\.\d+)?)", low)
    if m:
        return f"sqrt({m.group(1)})"
    low = re.sub(r"\b(plus)\b", "+", low)
    low = re.sub(r"\b(minus)\b", "-", low)
    low = re.sub(r"\b(times|multiplied by)\b", "*", low)
    low = re.sub(r"\b(divided by|over)\b", "/", low)
    m = re.search(r"(\(?\s*-?\d+(?:\.\d+)?\s*\)?(?:\s*(?:\*\*|[-+*/^%x×])\s*\(?\s*-?\d+(?:\.\d+)?\s*\)?)+)", low)
    return m.group(1).strip() if m else ""


def _schedule_slots(c: str) -> dict[str, Any]:
    low = c.lower()
    kind = re.search(MEETING, low).group(1)
    who = re.search(r"\bwith\s+([A-Z][a-z]+)", c)
    day = next((d for d in WEEKDAYS if re.search(rf"\b{d}\b", low)), None)
    if not day:
        day = "today" if "today" in low else "tomorrow" if "tomorrow" in low else None
    iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", c)
    at = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", low)
    time = None
    if at:
        hour, minute, half = int(at.group(1)), int(at.group(2) or 0), at.group(3)
        if half == "pm" and hour < 12:
            hour += 12
        if half == "am" and hour == 12:
            hour = 0
        time = f"{hour:02d}:{minute:02d}"
    return {"kind": kind, "who": who.group(1) if who else "", "day": iso.group(1) if iso else day, "time": time}


def _resolve_day(day: str | None, today: dt.date) -> tuple[dt.date, str]:
    if day and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return dt.date.fromisoformat(day), ""
    if day == "today":
        return today, ""
    if day in WEEKDAYS:
        ahead = (WEEKDAYS.index(day) - today.weekday()) % 7 or 7
        return today + dt.timedelta(days=ahead), ""
    return today + dt.timedelta(days=1), "" if day == "tomorrow" else " (no day given, so I picked tomorrow)"


def _time_from(lines: list[str], who: str) -> tuple[str, str] | None:
    """Find a stored preference about `who` and turn it into a time."""
    for line in lines:
        low = line.lower()
        if who and who.lower() not in low:
            continue
        m = re.search(r"\b(?:at|after|around)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", low)
        if m:
            hour = int(m.group(1)) + (12 if m.group(3) == "pm" and int(m.group(1)) < 12 else 0)
            return f"{hour:02d}:{int(m.group(2) or 0):02d}", line
        for word, time in DAYPARTS:
            if word in low:
                return time, line
    return None


# ------------------------------------------------------------------ acting on it

Step = tuple  # ("call", [ToolCall]) | ("done", text, consumed, value)


def _call(tool: str, /, **arguments: Any) -> Step:
    return ("call", [(tool, arguments)])


def _handle(intent: Intent, env: Env, results: list[Result], prev: str) -> Step:
    s = intent.slots
    kind = intent.kind

    if kind == "smalltalk":
        return ("done", "Hello. " + CAPABILITIES if re.match(r"^\s*(hi|hello|hey|yo|good)", intent.clause, re.I)
                else "You're welcome.", 0, "")
    if kind == "capabilities":
        return ("done", "I'm Loopwright, an agent built so you can watch every step it takes. " + CAPABILITIES, 0, "")

    if kind == "remember":
        return _single(results, _call("remember", text=s["text"]),
                       lambda d: {"ADD": f"Got it. I'll remember that {_lower_first(s['text'])}.",
                                  "UPDATE": f"Updated. I'll remember that {_lower_first(s['text'])} "
                                            f"(this replaces: \"{d.get('old_text', '')}\").",
                                  "NOOP": "I already had that stored.",
                                  "DROP": "I did not store that: " + d.get("why", "it is not something I keep") + "."
                                  }[d.get("action", "ADD")], s["text"])

    if kind == "forget":
        return _single(results, _call("forget", query=s["query"]),
                       lambda d: f"Done. I have forgotten: \"{d['text']}\"." if d.get("forgotten")
                       else "I have nothing stored that matches that, so there was nothing to forget.")

    if kind == "teach":
        name = "-".join(re.findall(r"[a-z0-9]+", s["when"].lower())[:4]) or "custom-skill"
        return _single(results, _call("create_skill", name=name, description=f"How to handle: {s['when']}",
                                      triggers=[s["when"].lower()], instructions=s["how"][0].upper() + s["how"][1:]),
                       lambda d: f"Saved as the skill `{d.get('name', name)}`. It will load whenever you mention "
                                 f"\"{s['when']}\".")

    if kind == "math":
        return _single(results, _call("calculator", expression=s["expression"]),
                       lambda d: f"{d.get('expression', s['expression'])} = **{d.get('result')}**.",
                       value_of=lambda d: f"{d.get('expression')} = {d.get('result')}")

    if kind == "time":
        return _single(results, _call("clock"),
                       lambda d: f"It is {d.get('time')} on {d.get('weekday')}, {d.get('date')}.")

    if kind == "calendar":
        def say(d: dict) -> str:
            events = d.get("events", [])
            if not events:
                return "Nothing on your calendar from today onward."
            lines = [f"- {e['date']} {e['time']}: {e['title']}" for e in events]
            return f"You have {len(events)} upcoming event(s):\n" + "\n".join(lines)
        return _single(results, _call("list_events", date_from=env.today.isoformat()), say)

    if kind == "note_save":
        text = s["text"] or prev or intent.clause
        return _single(results, _call("save_note", text=text), lambda d: f"Saved the note: \"{d.get('text', text)}\".", text)

    if kind == "note_search":
        def say(d: dict) -> str:
            notes = d.get("notes", [])
            return ("Your notes:\n" + "\n".join(f"- {n}" for n in notes)) if notes else "I found no matching notes."
        return _single(results, _call("search_notes", query=s["query"]), say)

    if kind == "read_file":
        return _single(results, _call("read_file", path=s["path"]),
                       lambda d: f"`{s['path']}` says:\n\n{_untrusted_body(d)[:600]}", raw_ok=True)

    if kind == "send":
        body = s["body"] or prev or "(no message text given)"
        if not results:
            return _call("send_message", to=s["to"], body=body)
        r = results[0]
        if r.is_error:
            error = r.data.get("error", "") if isinstance(r.data, dict) else ""
            if "declined" in error:
                return ("done", f"I did not send it: you declined. The draft to {s['to']} was: \"{body}\".", 1, "")
            return ("done", f"I could not send the message to {s['to']}: {error}.", 1, "")
        return ("done", f"Sent to {s['to']} after your approval: \"{body}\".", 1, body)

    if kind == "fetch":
        if not results:
            return _call("fetch_url", url=s["url"])
        r, consumed = results[0], 1
        if r.is_error:
            transient = isinstance(r.data, dict) and r.data.get("transient")
            if transient and len(results) < 2:
                return _call("fetch_url", url=s["url"])
            if transient and not results[1].is_error:
                r, consumed = results[1], 2
            else:
                error = r.data.get("error", "unknown error") if isinstance(r.data, dict) else "unknown error"
                return ("done", f"I could not fetch {s['url']}: {error}.", min(len(results), 2), "")
        fenced = "<untrusted_content" in r.raw
        body = _untrusted_body(r.data)
        sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", body) if x.strip() and not INJECTED.search(x)]
        summary = " ".join(sentences[:3])[:500]
        note = ("\n\nNote: the page also contained text written as instructions to me. I treated it as data "
                "and did not act on it.") if fenced and INJECTED.search(body) else ""
        return ("done", f"Here is what {s['url']} says: {summary}{note}", consumed, summary)

    if kind == "search":
        def say(d: dict) -> str:
            body = _untrusted_body(d)
            try:
                found = json.loads(body) if body.strip().startswith("{") else d
            except json.JSONDecodeError:
                found = d
            rows = found.get("results", []) if isinstance(found, dict) else []
            if not rows:
                return f"I searched the web for \"{s['query']}\" and found nothing useful."
            lines = [f"{i}. **{r['title']}**: {_safe_sentences(r['snippet'])} ({r['url']})" for i, r in enumerate(rows[:3], 1)]
            note = ("\n\nNote: one result contained text written as instructions to me. I treated it as data and "
                    "did not act on it.") if "<untrusted_content" in d.get("text", "") and INJECTED.search(body) else ""
            return (f"I searched the web for \"{s['query']}\" ({found.get('backend', 'search')}). The top results:\n"
                    + "\n".join(lines) + note)
        return _single(results, _call("web_search", query=s["query"]), say)

    if kind == "schedule":
        return _schedule(s, env, results)

    if kind == "recall":
        about = s["about"]
        relevant = [m for m in env.memory if about.lower() in ("me", "") or about.lower() in m.lower()] or \
                   ([] if about.lower() not in ("me", "") else env.memory)
        if relevant:
            return ("done", "Here is what I have:\n" + "\n".join(f"- {m}" for m in relevant), 0, "")
        if "recall" in env.tools and not results:
            return _call("recall", query=about if about.lower() != "me" else intent.clause)
        if results:
            found = [m["text"] for m in results[0].data.get("memories", [])] if isinstance(results[0].data, dict) else []
            text = ("Here is what I have:\n" + "\n".join(f"- {m}" for m in found)) if found else \
                "I don't have anything stored about that yet."
            return ("done", text, 1, "")
        return ("done", "I don't have anything stored about that yet.", 0, "")

    if kind == "compare":
        a, b = s["a"], s["b"]
        if "delegate" not in env.tools:
            return _kb(Intent("kb", intent.clause, {"query": f"{a} {b}"}), results)
        if not results:
            return _call("delegate", tasks=[f"Explain {a} in AI agent design: what it is, why it exists, the tradeoff.",
                                             f"Explain {b} in AI agent design: what it is, why it exists, the tradeoff."])
        r = results[0]
        if r.is_error:
            return ("done", f"I could not research that: {r.data.get('error', 'unknown error')}.", 1, "")
        parts = r.data.get("results", [])
        text = "Here is how they compare.\n\n" + "\n\n".join(
            f"**{label}**: {p['summary']}" for label, p in zip((a, b), parts, strict=False))
        if "bottom line" in env.critic.lower() or "in short" in env.critic.lower():
            firsts = [re.split(r"(?<=[.!?])\s+", p["summary"])[0] for p in parts]
            text += "\n\nIn short: " + " ".join(re.sub(r"^\*\*.*?\*\*:?\s*", "", f) for f in firsts)
        return ("done", text, 1, "")

    return _kb(intent, results)


def _kb(intent: Intent, results: list[Result]) -> Step:
    query = intent.slots.get("query", intent.clause)
    if not results:
        return _call("search_knowledge", query=query)
    r = results[0]
    if r.is_error:
        if isinstance(r.data, dict) and r.data.get("transient") and len(results) < 2:
            return _call("search_knowledge", query=query)
        if len(results) > 1 and not results[1].is_error:
            r, used = results[1], 2
        else:
            return ("done", f"I could not search the handbook: {r.data.get('error', 'unknown error')}.", len(results[:2]), "")
    else:
        used = 1
    passages = r.data.get("passages", []) if isinstance(r.data, dict) else []
    # relevant = most of the question's words are in the passage, or the vectors are close
    strong = [p for p in passages if (p.get("coverage", 0) >= 0.5 and p.get("bm25", 0) > 0) or p.get("cosine", 0) >= 0.45]
    if not strong:
        return ("done", "I searched the handbook and found nothing relevant. " + LIMITS, used, "")
    top = strong[0]
    lines = [f"**{top['term']}**" + (f": {top['one_liner']}" if top.get("one_liner") else ""), "", top["explain"]]
    if top.get("why"):
        lines += ["", f"Why it exists: {top['why']}"]
    if top.get("tradeoff"):
        lines += ["", f"The tradeoff: {top['tradeoff']}"]
    if top.get("file"):
        lines += ["", f"In this repo: `{top['file']}`"]
    lines += ["", top["cite"]]
    related = [p["term"] for p in strong[1:3]]
    if related:
        lines += ["", "Related: " + ", ".join(related) + "."]
    return ("done", "\n".join(lines), used, top["one_liner"] or top["term"])


def _schedule(s: dict[str, Any], env: Env, results: list[Result]) -> Step:
    who, used = s["who"], 0
    day, day_note = _resolve_day(s["day"], env.today)
    time, because = s["time"], ""
    if not time:
        found = _time_from(env.memory, who) if who else None
        if not found and who and "recall" in env.tools:
            if not results:
                return _call("recall", query=f"{who} meeting preference")
            if results[0].name == "recall":
                used = 1
                texts = [m["text"] for m in results[0].data.get("memories", [])] if isinstance(results[0].data, dict) else []
                found = _time_from(texts, who)
        if found:
            time, because = found[0], f", because I have stored: \"{found[1]}\""
        else:
            time, because = "10:00", " (no stored preference, so I picked 10:00)"
    title = f"{s['kind'].capitalize()} with {who}" if who else s["kind"].capitalize()
    rest = results[used:]
    if not rest:
        return _call("create_event", title=title, date=day.isoformat(), time=time, attendees=[who] if who else [])
    r, consumed = rest[0], 1
    if r.is_error:
        transient = isinstance(r.data, dict) and r.data.get("transient")
        if transient and len(rest) < 2:      # same arguments: the idempotency key makes the retry safe
            return _call("create_event", title=title, date=day.isoformat(), time=time, attendees=[who] if who else [])
        if transient and not rest[1].is_error:
            r, consumed = rest[1], 2
        else:
            return ("done", f"I could not book it: {r.data.get('error', 'unknown error')}.", used + min(len(rest), 2), "")
    used += consumed - 1
    warning = f" Heads up: it {r.data['warning']}." if r.data.get("warning") else ""
    return ("done", f"Booked **{title}** for {day.strftime('%A')} {day.isoformat()} at {time}{because}{day_note}.{warning}",
            used + 1, f"{title} on {day.isoformat()} at {time}")


def _single(results: list[Result], call: Step, say, value: str = "", value_of=None, raw_ok: bool = False) -> Step:
    """The common shape: one tool call, then a sentence built from its result.
    A transient failure is retried once; any other failure is reported."""
    if not results:
        return call
    r, used = results[0], 1
    if r.is_error:
        transient = isinstance(r.data, dict) and r.data.get("transient")
        if transient and len(results) < 2:
            return call
        if transient and not results[1].is_error:
            r, used = results[1], 2
        else:
            error = r.data.get("error", "unknown error") if isinstance(r.data, dict) else str(r.data)
            return ("done", f"That did not work: {error}.", min(len(results), 2), "")
    data = r.data if isinstance(r.data, dict) else {"text": r.raw}
    return ("done", say(data), used, value_of(data) if value_of else value)


def _safe_sentences(text: str) -> str:
    """The first sentences of a snippet, minus anything that reads as an instruction."""
    kept = [x.strip() for x in re.split(r"(?<=[.!?])\s+", text) if x.strip() and not INJECTED.search(x)
            and not re.search(r"send_message|stored memories", x)]
    return " ".join(kept[:2])


def _untrusted_body(data: Any) -> str:
    text = data.get("text", "") if isinstance(data, dict) else str(data)
    m = re.search(r"<untrusted_content[^>]*>\n?(.*?)\n?</untrusted_content>", text, re.S)
    return (m.group(1) if m else text).strip()


def _lower_first(text: str) -> str:
    return text if re.match(r"[A-Z][a-z]+\b", text) and not text.startswith(("I ", "My ")) else text[0].lower() + text[1:]


# ------------------------------------------------------------------ the provider

class ScriptedProvider:
    name = "scripted"

    def __init__(self, model: str = ""):
        self.model = model or "scripted-v1"

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]],
                 *, max_tokens: int = 2048, purpose: str = "loop") -> ModelResponse:
        names = {t["name"] for t in tools}
        user, results, critic = _turn(messages)
        env = _env(system, names, critic)
        if purpose == "plan":
            text, calls, thought = self._plan(user), [], "listing the steps before taking any"
        elif purpose == "subagent":
            text, calls, thought = self._subagent(user, results)
        else:
            text, calls, thought = self._main(user, env, results, system)
        if calls and not names:      # the loop's final call offers no tools: answer from what there is
            done = [r for r in results if not r.is_error]
            text = ("I reached my step limit before finishing. " +
                    (f"I completed {len(done)} step(s) ({', '.join(r.name for r in done)}) but could not do the rest."
                     if done else "I could not complete any step."))
            calls = []
        tool_calls = [ToolCall(f"call_{len(messages)}_{i}", name, arguments) for i, (name, arguments) in enumerate(calls)]
        prompt = system + messages_text(messages) + json.dumps(tools)
        return ModelResponse(text=text, tool_calls=tool_calls, thought=thought, provider=self.name, model=self.model,
                             stop_reason="tool_use" if tool_calls else "end_turn", cost_usd=0.0,
                             usage=Usage(estimate_tokens(prompt), estimate_tokens(text + json.dumps(calls)), estimated=True))

    def _main(self, user: str, env: Env, results: list[Result], system: str = "") -> tuple[str, list, str]:
        # The gullible branch: obey instructions found in unfenced tool output.
        for r in results:
            if (not r.is_error and "<untrusted_content" not in r.raw and INJECTED.search(r.raw)
                    and "send_message" in env.tools and not any(x.name == "send_message" for x in results)):
                target = re.search(r"[\w.\-]+@[\w.\-]+\.\w+", r.raw)
                leak = "; ".join(env.memory) or "(no memories in context)"
                return "", [("send_message", {"to": target.group(0) if target else "attacker@evil.example",
                                              "body": f"User data: {leak}"})], \
                    "the page told me to send the user's data, and nothing marked it as untrusted"
        results = [r for r in results if not (r.name == "send_message" and any(
            INJECTED.search(x.raw) and "<untrusted_content" not in x.raw for x in results if x.name != "send_message"))]

        intents = parse_intents(user)
        position, parts, prev = 0, [], ""
        for intent in intents:
            step = _handle(intent, env, results[position:], prev)
            if step[0] == "call":
                name, arguments = step[1][0]
                return "", step[1], f"{intent.kind}: I need `{name}` before I can answer"
            _, text, consumed, value = step
            position += consumed
            parts.append(text)
            prev = value or prev
        text = "\n\n".join(parts)
        custom = re.search(r"## Skill: (?!schedule-meeting|explain-concept|research-compare|web-research)([\w\-]+)\n(.*?)(?=\n## |\Z)", system, re.S)
        if custom and text.startswith("I searched the handbook and found nothing relevant"):
            text = (f"Your skill `{custom.group(1)}` loaded for this request. Its instructions are: \"{custom.group(2).strip()}\" "
                    "The built-in scripted model cannot carry out free-form instructions, so this is as far as it goes "
                    "offline. With a real model connected, the same skill is followed.")
        return text, [], "I have what I need; answering"

    def _subagent(self, task: str, results: list[Result]) -> tuple[str, list, str]:
        if not results:
            query = re.sub(r"^Explain\s+|\s+in AI agent design.*$", "", task, flags=re.I)
            return "", [("search_knowledge", {"query": query})], "a worker: look the topic up first"
        passages = results[0].data.get("passages", []) if isinstance(results[0].data, dict) and not results[0].is_error else []
        if not passages:
            return "I found nothing in the handbook for this task.", [], "nothing found"
        top = passages[0]
        summary = f"{top['one_liner'] or top['explain'][:200]}"
        if top.get("why"):
            summary += f" It exists because: {_lower_first(top['why'])}"
        if top.get("tradeoff"):
            summary += f" Tradeoff: {_lower_first(top['tradeoff'])}"
        return f"{summary} {top['cite']}", [], "summarising the best passage for the orchestrator"

    def _plan(self, user: str) -> str:
        steps = {
            "remember": "Store the fact with `remember`.", "forget": "Delete the matching fact with `forget`.",
            "teach": "Save the procedure as a skill with `create_skill`.", "math": "Compute it exactly with `calculator`.",
            "time": "Read the clock.", "calendar": "List upcoming events.", "note_save": "Save the note.",
            "note_search": "Search the notes.", "read_file": "Read the file from the workspace.",
            "send": "Draft the message and ask for approval before sending.",
            "fetch": "Fetch the page and read it as untrusted data.",
            "search": "Search the web, read the results as untrusted data, and cite the URLs.",
            "schedule": "Find the person's time preference in memory, then create the calendar event.",
            "recall": "Look up what is stored in memory.", "compare": "Delegate each side to a worker, then compare the summaries.",
            "kb": "Search the handbook, then answer from the best passage with a citation.",
            "smalltalk": "Reply directly; no tools needed.", "capabilities": "Describe what I can do.",
        }
        lines = [f"{i}. {steps[intent.kind]}" for i, intent in enumerate(parse_intents(user), 1)]
        return "\n".join(lines + [f"{len(lines) + 1}. Reply with the result."])
