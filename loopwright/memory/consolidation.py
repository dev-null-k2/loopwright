"""CONSOLIDATION: the write path of memory. Deciding *what* is worth keeping.

Reading memory is the easy half. The hard half is writing it without turning
the store into a junk drawer of duplicates, stale facts and things that were
only true for one afternoon. Loopwright writes memory two ways:

  hot path    the model calls the `remember` tool mid-turn. Immediate, costs
              a tool call, and only happens when the model thinks to do it.
  background  THIS FILE. After every few exchanges the recent conversation is
              read once and distilled. Nothing is added to the user's wait.

Background consolidation has two stages, and keeping them separate is the
design decision worth remembering:

  1. EXTRACT   propose candidate facts from the conversation.
               (rules offline; one model call when a real model is connected)
  2. RESOLVE   compare each candidate with what is already stored and choose:
                 ADD     nothing like it exists
                 UPDATE  same subject and attribute, new value: the old row is
                         kept and marked superseded, never overwritten
                 NOOP    already known
                 DROP    a secret, or not durable
               (always plain code, so the decision is inspectable and testable)

One episode (a dated one-line summary) is written per batch as well.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from loopwright.events import Recorder
from loopwright.guardrails import contains_secret
from loopwright.memory.retrieval import cosine, embed
from loopwright.memory.store import Store

# attributes that can only have one value at a time: a new value replaces the old
SINGLE_VALUED = {"name", "employer", "location", "works at", "lives in", "timezone", "manager", "role"}

EXTRACT_PROMPT = """Extract durable facts about the user and the people they mention from this
conversation. Keep stable preferences, relationships, roles, places and plans. Skip one-off
requests, small talk, anything about the assistant itself, and anything secret.

Reply with only a JSON list. Each item: {{"text": "<one sentence, third person>",
"subject": "<user, or the person's first name in lower case>",
"attr": "<a short attribute name when the fact has exactly one value, else empty>"}}

Conversation:
{conversation}"""


@dataclass
class Candidate:
    text: str
    subject: str = "user"
    attr: str = ""


_WRAPPER = re.compile(r"^\s*(?:please\s+)?(?:remember|note|keep in mind|don'?t forget)(?:\s+that)?[:,]?\s+(.+)$", re.I | re.S)
_PERSON = re.compile(r"\b([A-Z][a-z]{2,})\s+(prefers|likes|loves|hates|dislikes|works at|lives in|is my|is allergic to|"
                     r"only does|can't do|cannot do|wants|needs)\s+([^.;!?\n]+)")
_USER_RULES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\b(?:my name is|call me)\s+([A-Z][\w\-]+)", re.I), "User's name is {0}", "name"),
    (re.compile(r"\bi(?:'m| am)?(?: currently)? work(?:ing)? (?:at|for)\s+([^.,;!?\n]+?)(?=\s+and\s+(?:i|my|we)\b|[.,;!?\n]|$)", re.I), "User works at {0}", "employer"),
    (re.compile(r"\bi(?: just)? (?:live in|moved to|am based in|'m based in)\s+([^.,;!?\n]+?)(?=\s+and\s+(?:i|my|we)\b|[.,;!?\n]|$)", re.I), "User lives in {0}", "location"),
    (re.compile(r"\bi(?:'m| am) (?:preparing|studying|interviewing|getting ready) for\s+([^.,;!?\n]+?)(?=\s+and\s+(?:i|my|we)\b|[.,;!?\n]|$)", re.I), "User is preparing for {0}", ""),
    (re.compile(r"\bmy (favou?rite [\w ]{2,24}|manager|role|timezone|time zone|editor|team) is\s+([^.,;!?\n]+)", re.I), "User's {0} is {1}", "{0}"),
    (re.compile(r"\bi (prefer|like|love|enjoy|hate|dislike|don'?t like|can'?t stand|always|never)\s+([^.;!?\n]+)", re.I), "User {0}s {1}", ""),
]


def extract_by_rules(user_messages: list[str]) -> list[Candidate]:
    """Offline extraction: a handful of sentence shapes. It misses plenty,
    which is the honest cost of running without a model; the resolve stage
    below is identical either way."""
    out: list[Candidate] = []
    for message in user_messages:
        wrapped = _WRAPPER.match(message)
        text = wrapped.group(1).strip() if wrapped else message
        found = False
        for match in _PERSON.finditer(text):
            name, verb, rest = match.group(1), match.group(2), match.group(3).strip()
            if name.lower() in ("i", "the", "user", "please", "what", "when", "can", "could"):
                continue
            attr = verb if verb in ("works at", "lives in") else ("prefers" if verb == "prefers" else "")
            out.append(Candidate(f"{name} {verb} {rest}", name.lower(), attr))
            found = True
        for pattern, template, attr in _USER_RULES:
            match = pattern.search(text)
            if not match:
                continue
            groups = [g.strip() for g in match.groups()]
            sentence = template.format(*groups).replace("don't likes", "doesn't like").replace("dont likes", "doesn't like")
            sentence = re.sub(r"\b(always|never)s\b", r"\1", sentence)
            out.append(Candidate(sentence, "user", attr.format(*groups).lower() if attr else ""))
            found = True
        if wrapped and not found:
            out.append(Candidate(text[0].upper() + text[1:], _guess_subject(text), ""))
    return out


def _guess_subject(text: str) -> str:
    names = re.findall(r"\b([A-Z][a-z]{2,})\b", text[1:])
    return names[0].lower() if names else "user"


def extract_by_model(conversation: str, small_model) -> list[Candidate]:
    text = small_model(EXTRACT_PROMPT.format(conversation=conversation))
    if "[" not in text:
        return []
    rows = json.loads(text[text.index("["): text.rindex("]") + 1])
    return [Candidate(str(r.get("text", "")).strip(), str(r.get("subject", "user")).lower().strip() or "user",
                      str(r.get("attr", "")).lower().strip())
            for r in rows if isinstance(r, dict) and r.get("text")]


def resolve(store: Store, candidate: Candidate, source: str = "") -> dict[str, Any]:
    """Stage 2: ADD, UPDATE, NOOP or DROP one candidate against the store."""
    text = candidate.text.strip().rstrip(".")
    if len(text) < 6:
        return {"action": "DROP", "text": text, "why": "too short to be a fact"}
    if contains_secret(text):
        return {"action": "DROP", "text": "[redacted]", "why": "looks like a credential; never stored"}
    vec = embed(text)
    same_subject = [f for f in store.facts() if f["subject"] == candidate.subject]
    for fact in same_subject:                                   # already known?
        similarity = cosine(vec, embed(fact["text"]))
        if fact["text"].lower().rstrip(".") == text.lower() or similarity >= 0.9:
            return {"action": "NOOP", "text": text, "why": f"already stored as fact {fact['id']}", "fact_id": fact["id"]}
    if candidate.attr:                                          # a new value for something single-valued?
        for fact in same_subject:
            if fact["attr"] != candidate.attr:
                continue
            similarity = cosine(vec, embed(fact["text"]))
            if candidate.attr in SINGLE_VALUED or similarity >= 0.5:
                new_id = store.add_fact(text, candidate.subject, candidate.attr, source)
                store.supersede(fact["id"], new_id)
                return {"action": "UPDATE", "text": text, "fact_id": new_id, "replaces": fact["id"],
                        "old_text": fact["text"],
                        "why": f"same subject and attribute ({candidate.subject}.{candidate.attr}); "
                               f"the old fact is kept, marked superseded"}
    new_id = store.add_fact(text, candidate.subject, candidate.attr, source)
    return {"action": "ADD", "text": text, "fact_id": new_id, "why": "nothing like it was stored"}


def consolidate(store: Store, session: str, rec: Recorder, *, every: int = 3, force: bool = False,
                small_model=None) -> dict[str, Any]:
    """Run the background write if enough has been said since the last one."""
    pending = store.unconsolidated(session)
    user_messages = [m["content"] for m in pending if m["role"] == "user"]
    if not user_messages or (len(user_messages) < every and not force):
        rec.emit("consolidate.wait", "consolidate", f"Consolidation waits ({len(user_messages)}/{every} exchanges)",
                 "Memory is distilled in batches, not after every message. That keeps the cost off the "
                 "hot path and lets one pass see several messages together.",
                 src="working", status="skip", data={"pending": len(user_messages), "every": every})
        return {"ran": False}

    conversation = "\n".join(f"{m['role']}: {m['content']}" for m in pending)
    try:
        if small_model is not None:
            candidates, how = extract_by_model(conversation, small_model), "model"
        else:
            candidates, how = extract_by_rules(user_messages), "rules"
    except Exception as exc:
        candidates, how = extract_by_rules(user_messages), f"rules (model extraction failed: {type(exc).__name__})"
    rec.emit("consolidate.extract", "consolidate", f"Extracted {len(candidates)} candidate fact(s) by {how}",
             "Stage 1 of the write path: read the recent exchanges once and propose what might be worth "
             "keeping. Nothing is stored yet.",
             src="working", data={"how": how, "messages": len(pending),
                                  "candidates": [c.__dict__ for c in candidates]})

    decisions = []
    for candidate in candidates:
        decision = resolve(store, candidate, source=f"consolidation:{rec.turn_id}")
        decisions.append(decision)
        status = {"ADD": "ok", "UPDATE": "ok", "NOOP": "skip", "DROP": "warn"}[decision["action"]]
        rec.emit("consolidate.resolve", "semantic", f"{decision['action']}: {decision['text'][:70]}",
                 "Stage 2: compare the candidate with what is stored. " + decision["why"] + ".",
                 src="consolidate", status=status, data=decision)

    episode = _episode(pending)
    episode_id = store.add_episode(episode, session)
    rec.emit("consolidate.episode", "episodic", "Wrote one episode",
             "Episodic memory keeps *what happened and when* as a dated summary, separate from the "
             "timeless facts in semantic memory.",
             src="consolidate", data={"episode_id": episode_id, "summary": episode})
    store.mark_consolidated([m["id"] for m in pending])
    counts = {a: sum(1 for d in decisions if d["action"] == a) for a in ("ADD", "UPDATE", "NOOP", "DROP")}
    return {"ran": True, "decisions": decisions, "episode": episode, **counts}


def _episode(messages: list[dict[str, Any]]) -> str:
    """An extractive one-liner: what the user asked for, in their words."""
    asks = [re.sub(r"\s+", " ", m["content"]).strip()[:90] for m in messages if m["role"] == "user"]
    return "User said: " + " | ".join(asks[:4]) + (f" (+{len(asks) - 4} more)" if len(asks) > 4 else "")
