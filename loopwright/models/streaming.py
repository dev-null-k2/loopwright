"""STREAMING: show the reply while the model is still writing it.

A model produces its answer one token at a time. Without streaming the user
looks at nothing until the last token; with it, the first words appear after
the *time to first token*, which is usually a fraction of the whole wait.
Nothing gets faster. The wait just stops being blank, and that is most of
what "feels fast" means.

How it runs here:

  1. a provider that can stream is handed `on_text` and calls it with each
     piece of text as it arrives (see `Provider` in base.py)
  2. the gateway points `on_text` at a `Streamer` (below)
  3. the Streamer passes the text on to whoever is watching, as `delta`
     messages on the recorder's live channel
  4. the provider still returns the whole response at the end, so the loop,
     the trace and the evals are the same with streaming on or off

Three problems come with it, and each has its answer in this file.

THE OUTPUT GUARD. The output guardrail reads a finished reply. A stream shows
text before it is finished, so a secret could be on screen before the guard
has seen it. The Streamer therefore runs the same secret patterns over the
text so far and keeps the last few characters back (HOLDBACK): a credential
is only recognisable once it is whole, and a half-written one must not be
shown while that is undecided. The price is that the visible text runs a few
words behind the model. When a pattern only matches after part of it was
already released, the Streamer sends a `replace` and the reader's screen is
corrected. The full guard still runs on the finished reply.

RETRIES. If the connection drops half-way the gateway retries, and the second
attempt starts from the beginning. Every attempt that produces text opens its
own stream (its own `model.stream` event), and the reader shows the newest.

MODELS THAT ANSWER IN JSON. Claude Code and the text protocol for small local
models answer with one JSON object. Its raw characters are not for the user,
so `ReplyFilter` reads the object as it grows and lets through only the
characters of its "final" string.

Deltas are not events. A reply can be a thousand pieces, and a thousand
events would bury the trace. The trace records one `model.stream` event when
the first words arrive and the totals on the `model.call` event that follows.
"""

from __future__ import annotations

import re
import time
from typing import Any

from loopwright.events import Recorder
from loopwright.guardrails import redact_secrets
from loopwright.models.base import OnText

HOLDBACK = 24           # characters kept back while the guard is on: longer than a card number
SHOWN_LIMIT = 2000      # how much of the visible text the trace keeps when it differs from the model's


class Streamer:
    """One streamed model call. `feed` takes the model's text as it arrives;
    `finish` reconciles with the complete reply and returns the totals."""

    def __init__(self, rec: Recorder, provider: str, model: str, iteration: int, *, guard: bool, pace_s: float = 0.0):
        self.rec, self.provider, self.model, self.iteration = rec, provider, model, iteration
        self.guard, self.pace_s = guard, pace_s
        self.raw = ""               # everything the model has written
        self.shown = ""             # everything released to the reader
        self.pieces = 0
        self.seq = 0                # the model.stream event these deltas belong to
        self.first_ms: int | None = None
        self.replaced = 0
        self._t0 = time.perf_counter()

    def feed(self, text: str) -> None:
        if not text:
            return
        if self.first_ms is None:
            self.first_ms = int((time.perf_counter() - self._t0) * 1000)
            event = self.rec.emit(
                "model.stream", "reply", f"The reply starts to arrive after {self.first_ms} ms",
                "The model is still writing, and its words are passed on as they come instead of being kept until "
                "the end. This moment is the time to first token: how long the user looked at nothing. "
                + (f"The output guard is on, so the visible text runs {HOLDBACK} characters behind the model: a "
                   "secret has to be whole before it can be recognised and masked."
                   if self.guard else "The output guard is off, so every character is shown the moment it arrives."),
                src="model", data={"first_token_ms": self.first_ms, "provider": self.provider, "model": self.model,
                                   "iteration": self.iteration, "holdback": HOLDBACK if self.guard else 0})
            self.seq = event.seq
        self.raw += text
        self.pieces += 1
        self._release(self._safe(final=False))
        if self.pace_s:
            time.sleep(self.pace_s)

    def finish(self, reply: str) -> dict[str, Any] | None:
        """The call is over and `reply` is the model's complete text. Show whatever
        was held back, correct the screen if it differs from what was shown, and
        return the totals for the model.call event (None when nothing streamed)."""
        if not self.seq:
            return None
        self.raw = reply
        visible = self._safe(final=True)
        self._release(visible)
        stats: dict[str, Any] = {"first_token_ms": self.first_ms, "pieces": self.pieces, "chars": len(visible),
                                 "ms": int((time.perf_counter() - self._t0) * 1000), "replaced": self.replaced,
                                 "event": self.seq}
        if visible != reply:                 # the reader saw a masked version: keep it, so a replay shows the same
            stats["shown"] = visible[:SHOWN_LIMIT]
        return stats

    # ------------------------------------------------------------ internals
    def _safe(self, final: bool) -> str:
        """The part of the text that may be shown now."""
        if not self.guard:
            return self.raw
        text = redact_secrets(self.raw).text
        if final:
            return text
        # release whole words only, and never the last HOLDBACK characters
        cut = max((m.end() for m in re.finditer(r"\s", text[:max(0, len(text) - HOLDBACK)])), default=0)
        return text[:cut]

    def _release(self, visible: str) -> None:
        if visible == self.shown:
            return
        if visible.startswith(self.shown):
            self.rec.say({"type": "delta", "stream": self.seq, "text": visible[len(self.shown):]})
        else:                                # something already shown turned out to be part of a secret
            self.replaced += 1
            self.rec.say({"type": "delta", "stream": self.seq, "replace": visible})
        self.shown = visible


class ReplyFilter:
    """Sits between a text-protocol model and `on_text`.

    Such a model writes either plain prose (pass it straight through) or one
    JSON object, {"thought": ..., "final": "the answer"} or {"tool_calls": ...}.
    From the JSON only the characters inside "final" are for the reader, and
    they have to be un-escaped as they arrive: \\n is a line break, \\" a quote,
    \\u00e9 a letter. A tool-call object lets nothing through."""

    KEY = re.compile(r'"final"\s*:\s*"')

    def __init__(self, on_text: OnText):
        self.on_text = on_text
        self.raw = ""
        self.mode = ""          # "" (undecided) | "prose" | "json"
        self.at = -1            # index in raw of the next unread character of the final string
        self.done = False
        self._high = ""         # the first half of a \\uD83D\\uDE00 pair, waiting for the second

    def feed(self, chunk: str) -> None:
        if not chunk or self.done:
            return
        self.raw += chunk
        if not self.mode and not self._decide():
            return
        if self.mode == "prose":
            self.on_text(chunk)
            return
        if self.at < 0:
            found = self.KEY.search(self.raw)
            if not found:
                return
            self.at = found.end()
        out = self._read_string()
        if out:
            self.on_text(out)

    def _decide(self) -> bool:
        head = self.raw.lstrip()
        if not head:
            return False
        if head.startswith("```"):                  # a fenced reply: is there an object inside?
            line_end = head.find("\n")
            inside = head[line_end + 1:].lstrip() if line_end != -1 else ""
            if not inside:
                return False
            self.mode = "json" if inside.startswith("{") else "prose"
        else:
            self.mode = "json" if head.startswith("{") else "prose"
        if self.mode == "prose":
            self.on_text(self.raw)                  # everything held while deciding
            self.raw = ""
            return False                            # this chunk has been passed on already
        return True

    def _read_string(self) -> str:
        """Decode as much of the JSON string as has arrived. Stops at the closing
        quote, or early when an escape is cut in half by the end of the chunk."""
        out, raw, i = [], self.raw, self.at
        while i < len(raw):
            c = raw[i]
            if c == '"':
                self.done = True
                i += 1
                break
            if c != "\\":
                out.append(c)
                i += 1
                continue
            if i + 1 >= len(raw):
                break                               # the escape's second character has not arrived
            e = raw[i + 1]
            if e == "u":
                if i + 6 > len(raw):
                    break
                try:
                    code = int(raw[i + 2:i + 6], 16)
                except ValueError:
                    code = 0xFFFD
                i += 6
                if 0xD800 <= code < 0xDC00:         # half of a pair: wait for the other half
                    self._high = chr(code)
                    continue
                if self._high and 0xDC00 <= code < 0xE000:
                    pair = (self._high + chr(code)).encode("utf-16", "surrogatepass").decode("utf-16", "replace")
                    out.append(pair)
                else:
                    out.append(chr(code))
                self._high = ""
                continue
            out.append({"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f"}.get(e, e))
            i += 2
        self.at = i
        return "".join(out)
