"""THE RETRIEVAL GATE: decide *whether* to search memory before searching it.

Searching long-term memory on every message has two costs. It is slower, and
it is worse: memories that merely look related get pasted into the prompt and
pull the answer off course ("what's 2+2?" does not need to know where you
live). So before any store is touched, one cheap decision is made:

    does THIS message need what the agent remembers about this person?

Three ways to make that decision, selectable per turn (`Options.gate`):

  rules   a few patterns: personal words, known names, action verbs. Free and
          instant. Wrong on unusual phrasings.
  model   one small-model call that returns JSON. More accurate, costs a call.
  always / never   the two ablations. `always` is what most tutorials do.

Whatever the mode, the gate FAILS OPEN: if deciding goes wrong, retrieve. A
needless search costs a few tokens; a skipped one loses the memory.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from loopwright.memory.retrieval import tokenize

PERSONAL = re.compile(r"\b(my|me|mine|myself|i|i'm|i've|i'll|we|our|us)\b", re.I)
RECALL = re.compile(r"\b(remember|recall|last time|again|usual|as before|prefer|favou?rite|you know|"
                    r"did i|have i|do i|am i|what do you know)\b", re.I)
ACTION = re.compile(r"\b(schedule|book|meeting|meet|catch[- ]up|call with|sync with|lunch with|coffee with|"
                    r"email|message|remind|invite)\b", re.I)
SMALL_TALK = re.compile(r"^\s*(hi|hello|hey|thanks|thank you|ok|okay|cool|bye|good (morning|evening|night))\b[\s!.]*$", re.I)
MATH = re.compile(r"^[\s\d\.\+\-\*/\(\)%\^x,=?]*$|^\s*(what('?s| is)|calculate|compute)\b[^a-z]*[\d\.\s\+\-\*/\(\)%\^]+(of [\d\.]+)?\s*\??$", re.I)
WRITE = re.compile(r"^\s*(please\s+)?(remember|note|keep in mind|don'?t forget|forget)\b", re.I)

GATE_PROMPT = """You are a retrieval gate for an assistant's long-term memory.
Decide whether answering the message below needs stored memories about the
user (people, preferences, plans, past events). General knowledge, maths,
small talk and self-contained requests do not.

Reply with only this JSON:
{{"retrieve": true or false, "query": "<search keywords, or empty>", "reason": "<under 8 words>"}}

Message: {message}"""


@dataclass
class GateDecision:
    retrieve: bool
    query: str
    reason: str
    mode: str


def decide(message: str, subjects: set[str], mode: str = "rules", small_model=None) -> GateDecision:
    """Return the gate's decision. `small_model` is a callable(prompt) -> text,
    used only in 'model' mode."""
    query = " ".join(tokenize(message)) or message
    if mode == "never":
        return GateDecision(False, "", "gate forced off (ablation)", mode)
    if mode == "always":
        return GateDecision(True, query, "always retrieve (no gate)", mode)
    try:
        if mode == "model" and small_model is not None:
            return _by_model(message, query, small_model)
        return _by_rules(message, query, subjects)
    except Exception as exc:  # fail open
        return GateDecision(True, query, f"gate failed open ({type(exc).__name__})", mode)


def _by_rules(message: str, query: str, subjects: set[str]) -> GateDecision:
    words = set(re.findall(r"[a-z][a-z'\-]+", message.lower()))
    known = sorted(s for s in subjects if s in words)
    if SMALL_TALK.match(message):
        return GateDecision(False, "", "small talk", "rules")
    if MATH.match(message):
        return GateDecision(False, "", "self-contained maths", "rules")
    if WRITE.match(message):
        return GateDecision(False, "", "a memory write, not a read", "rules")
    if known:
        return GateDecision(True, query, f"mentions someone I know: {', '.join(known)}", "rules")
    if ACTION.search(message):
        return GateDecision(True, query, "an action that may depend on preferences", "rules")
    if RECALL.search(message):
        return GateDecision(True, query, "asks about something remembered", "rules")
    if PERSONAL.search(message):
        return GateDecision(True, query, "refers to the user's own life", "rules")
    return GateDecision(False, "", "general question, no personal reference", "rules")


def _by_model(message: str, query: str, small_model) -> GateDecision:
    text = small_model(GATE_PROMPT.format(message=message))
    if "{" not in text:
        return GateDecision(True, query, "gate returned no JSON, failing open", "model")
    decision = json.loads(text[text.index("{"): text.rindex("}") + 1])
    return GateDecision(bool(decision.get("retrieve")), str(decision.get("query") or query),
                        str(decision.get("reason", ""))[:80], "model")
