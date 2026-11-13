"""THE HARNESS: everything around the model that turns it into an agent.

A model is a function from text to text. It has no memory, no tools, no
budget, no sense of who it is talking to, and no way to stop itself. The
harness is the ordinary code that supplies all of that. Most of what makes one
agent better than another, on the same model, is in the harness.

One turn, top to bottom. Each step emits an event; the numbers match the
flow on the architecture diagram:

   1  gateway        a message arrives (terminal, browser or MCP)
   2  guard_in       secrets in the message are masked
   3  working        the session's conversation is loaded (compacted if long)
   4  procedural     skills whose triggers match are loaded
   5  gate           decide whether this turn needs long-term memory at all
   6  semantic/episodic   if so, search it
   7  context        assemble the prompt and count where the tokens went
   8  planner        (pattern=plan) write the steps first
   9  THE LOOP       model -> tool gate -> tools -> model ... (loop.py)
  10  critic         (pattern=reflect) check the draft, allow one revision
  11  guard_out      mask secrets, check citations
  12  reply          out through the same gateway
  13  working        the exchange is saved
  14  consolidate    every few exchanges, distil durable facts into memory
  15  evals, cost, trace   score the turn, write the receipt, save the trace

Read `run_turn` first; every method it calls is one of the steps above.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loopwright import context as ctx_mod
from loopwright import guardrails, patterns, receipt
from loopwright.config import Options, home_dir
from loopwright.events import Event, Recorder, Sink, new_id, write_trace
from loopwright.loop import LoopResult, run_loop
from loopwright.memory import gate as gate_mod
from loopwright.memory.consolidation import consolidate
from loopwright.memory.retrieval import Hit
from loopwright.memory.skills import load_skills, match_skills
from loopwright.memory.store import Store
from loopwright.models.base import Message, ModelError, Provider
from loopwright.models.gateway import ModelGateway
from loopwright.toolgate import Approver, ToolGate, TurnState
from loopwright.tools.builtin import default_registry
from loopwright.tools.delegate import WORKER_TOOLS
from loopwright.tools.registry import Registry, ToolContext


@dataclass
class TurnResult:
    turn_id: str
    reply: str
    ok: bool
    events: list[Event]
    receipt: dict[str, Any]
    checks: list[dict[str, Any]]
    tool_log: list[dict[str, Any]]
    trace_path: str = ""

    def kinds(self) -> list[str]:
        return [e.kind for e in self.events]

    def to_json(self) -> dict[str, Any]:
        return {"turn_id": self.turn_id, "reply": self.reply, "ok": self.ok, "receipt": self.receipt,
                "checks": self.checks, "tool_log": self.tool_log, "events": [e.to_json() for e in self.events]}


@dataclass
class _Turn:
    """Everything one turn carries from step to step."""
    message: str
    session: str
    options: Options
    rec: Recorder
    gateway: ModelGateway
    state: TurnState
    tools: ToolContext
    registry: Registry
    gate: ToolGate
    started: float
    system: str = ""
    messages: list[Message] = field(default_factory=list)
    memories: list[Hit] = field(default_factory=list)
    loop: LoopResult | None = None
    reply: str = ""
    ok: bool = True
    written: int = 0
    resumed_from: int = 0


class Agent:
    def __init__(self, home: Path | None = None, now: Callable[[], dt.datetime] | None = None,
                 registry: Registry | None = None, providers: list[Provider] | None = None):
        self.home = Path(home) if home else home_dir()
        self.store = Store(self.home)
        self.now = now or dt.datetime.now
        self.registry = registry or default_registry()
        self._providers = providers          # tests inject fake models here
        self.load_extensions()

    def load_extensions(self) -> None:
        """(Re)load everything the user added: their own tool files, MCP servers,
        and the list of tools they switched off."""
        from loopwright.tools.mcp_client import load_mcp_tools
        from loopwright.tools.plugins import load_settings, load_user_tools
        for name in getattr(self, "_extension_tools", []):
            self.registry._tools.pop(name, None)
        before = set(self.registry.names())
        self.plugin_status = load_user_tools(self.home, self.registry)
        self.mcp_status = load_mcp_tools(self.home, self.registry)     # tools borrowed from other MCP servers
        self._extension_tools = [n for n in self.registry.names() if n not in before]
        self.disabled_tools = load_settings(self.home)["disabled_tools"]

    # ================================================================ one turn
    def run_turn(self, message: str, session: str = "default", options: Options | None = None,
                 sinks: list[Sink] | None = None, approver: Approver | None = None,
                 turn_id: str | None = None) -> TurnResult:
        t = self._begin(message, session, options or Options(), sinks, approver, turn_id or new_id())
        try:
            self._prepare(t)        # steps 1-8
            self._run_loop(t)       # step 9
            self._review(t)         # step 10
            self._guard_reply(t)    # steps 11-12
        except ModelError as exc:
            self._fail(t, exc)
        except Exception as exc:    # a bug anywhere must still end in a reply and a trace, never a hung turn
            self._fail(t, exc, bug=True)
        return self._finish(t)      # steps 13-15

    def resume(self, turn_id: str, options: Options | None = None, sinks: list[Sink] | None = None,
               approver: Approver | None = None) -> TurnResult:
        """Continue a turn that died mid-loop, from its last checkpoint."""
        saved = self.store.last_checkpoint(turn_id)
        if saved is None:
            raise ValueError(f"no checkpoint for turn {turn_id}")
        step, state = saved
        t = self._begin(state["message"], state["session"], options or Options.from_dict(state["options"]),
                        sinks, approver, turn_id)
        t.system, t.messages, t.resumed_from = state["system"], state["messages"], step
        t.rec.emit("checkpoint.resume", "checkpoint", f"Resumed from the checkpoint after step {step}",
                   "Durable execution. The loop's state was saved after every round, so a turn that died "
                   "(a crash, a deploy, a provider outage) picks up where it stopped instead of starting over. "
                   "Tool calls that already ran are not run again: their results are in the saved conversation, "
                   "and the idempotency keys in the tool gate catch any that slip through.",
                   src="user", data={"turn_id": turn_id, "step": step, "messages": len(t.messages)})
        try:
            self._run_loop(t)
            self._review(t)
            self._guard_reply(t)
        except ModelError as exc:
            self._fail(t, exc)
        return self._finish(t)

    # ================================================================ the steps
    def _begin(self, message: str, session: str, options: Options, sinks: list[Sink] | None,
               approver: Approver | None, turn_id: str) -> _Turn:
        rec = Recorder(turn_id, sinks)
        gateway = ModelGateway(options, rec, home=self.home, providers=list(self._providers) if self._providers else None)
        state = TurnState(chaos_left=options.chaos_tool_failures)
        tools = ToolContext(self.store, self.home, rec, options, self.now)
        off = self.disabled_tools + ([] if options.memory else ["recall", "remember", "forget"])
        registry = self.registry.without(off)
        gate = ToolGate(registry, tools, options, rec, state, approver)
        t = _Turn(message, session, options, rec, gateway, state, tools, registry, gate, time.perf_counter())
        tools.spawn = lambda task, n: self._spawn(t, task, n)
        tools.reload = self.load_extensions
        return t

    def _prepare(self, t: _Turn) -> None:
        rec, opts = t.rec, t.options
        # 1 ---- gateway
        rec.emit("gateway.receive", "gateway", "Message received",
                 "A turn starts when a message comes in through a gateway: the terminal, the browser or an MCP "
                 "client. The gateway is only a door; the same harness runs behind all of them.",
                 src="user", data={"message": t.message, "session": t.session,
                                   "provider": t.gateway.primary.name, "model": t.gateway.primary.model,
                                   "options": opts.to_dict()})
        # 2 ---- input guardrail
        if opts.guardrails:
            guarded = guardrails.guard_input(t.message)
            rec.emit("guard.input", "guard_in", "Input checked" + ("" if guarded.clean else ": secret masked"),
                     "The user is the principal, so their text is obeyed, not filtered. One thing is removed: "
                     "anything shaped like a credential, so it never reaches the trace, the model or memory.",
                     src="gateway", status="ok" if guarded.clean else "warn",
                     data={"findings": guarded.to_json(), "message": guarded.text})
            t.message = guarded.text
        else:
            rec.emit("guard.input", "guard_in", "Input guard is off", "Guardrails are switched off for this turn.",
                     src="gateway", status="skip")
        # 3 ---- working memory
        summary, history = ctx_mod.load_history(self.store, t.session, opts, rec, t.gateway.small("compact"))
        rec.emit("working.load", "working", f"Loaded {len(history)} earlier message(s)",
                 "Working memory is the conversation so far in this session. The model keeps nothing between "
                 "calls, so the harness re-sends it every time."
                 + (" Older messages are present as a summary." if summary else ""),
                 src="guard_in", data={"messages": len(history), "has_summary": bool(summary), "session": t.session})
        # 4 ---- procedural memory
        skills = load_skills(self.home)
        matched = match_skills(skills, t.message)
        rec.emit("skills.match", "procedural",
                 f"Skill loaded: {', '.join(s.name for s, _ in matched)}" if matched else f"No skill triggered ({len(skills)} available)",
                 "Procedural memory is how-to knowledge. Every skill's one-line description is always in the "
                 "prompt; a skill's full instructions are loaded only when the message triggers it "
                 "(progressive disclosure).",
                 src="working", status="ok" if matched else "skip",
                 data={"available": [{"name": s.name, "description": s.description} for s in skills],
                       "loaded": [{"name": s.name, "trigger": trig, "tokens": len(s.body) // 4} for s, trig in matched]})
        # 5-6 ---- the retrieval gate, then retrieval
        t.memories = self._retrieve(t)
        # 7 ---- context
        built = ctx_mod.build(t.message, summary=summary, history=history, skills=skills,
                              active=[s for s, _ in matched], memories=t.memories,
                              tool_schemas=t.registry.schemas(), now=self.now(), guardrails=opts.guardrails,
                              identity=ctx_mod.load_identity(self.home))
        # 8 ---- planner (optional)
        if opts.pattern == "plan":
            plan = patterns.make_plan(t.gateway, built.system, built.messages, rec)
            built = ctx_mod.build(t.message, summary=summary, history=history, skills=skills,
                                  active=[s for s, _ in matched], memories=t.memories,
                                  tool_schemas=t.registry.schemas(), now=self.now(), guardrails=opts.guardrails, plan=plan,
                                  identity=ctx_mod.load_identity(self.home))
        t.system, t.messages = built.system, built.messages
        pct = round(100 * built.total / max(opts.context_budget, 1))
        rec.emit("context.build", "context", f"Context assembled: about {built.total:,} tokens ({pct}% of budget)",
                 "This is everything the model will see: instructions, skills, retrieved memory, the conversation "
                 "and every tool's schema. Each part competes for the same window, and every token is paid for "
                 "again on each round of the loop.",
                 src="gate" if opts.pattern != "plan" else "planner", data={
                     "sections": [{"name": s.name, "tokens": s.tokens, "note": s.note, "text": s.text} for s in built.sections],
                     "history_tokens": built.history_tokens, "tools_tokens": built.tools_tokens,
                     "tool_count": len(t.registry.names()), "total": built.total, "budget": opts.context_budget, "pct": pct})

    def _retrieve(self, t: _Turn) -> list[Hit]:
        rec, opts = t.rec, t.options
        if not opts.memory:
            rec.emit("memory.gate", "gate", "Long-term memory is off", "Memory is switched off for this turn (ablation).",
                     src="procedural", status="skip", data={"retrieve": False, "mode": "off"})
            return []
        decision = gate_mod.decide(t.message, self.store.subjects(), opts.gate,
                                   t.gateway.small("gate") if opts.gate == "model" else None)
        rec.emit("memory.gate", "gate",
                 f"Gate: {'retrieve' if decision.retrieve else 'skip'} ({decision.reason})",
                 "Before touching any store, one cheap decision: does this message need what the agent remembers "
                 "about this person? Retrieving on every turn is slower and pulls loosely related memories into "
                 "the prompt, where they bias the answer.",
                 src="procedural", status="ok" if decision.retrieve else "skip",
                 data={"retrieve": decision.retrieve, "query": decision.query, "reason": decision.reason, "mode": decision.mode})
        if not decision.retrieve:
            return []
        facts = self.store.search_facts(decision.query, k=5)
        rec.emit("memory.search", "semantic", f"Semantic memory: {len(facts)} fact(s) found",
                 "Facts are searched two ways at once: BM25 for exact words and vectors for similarity, merged by "
                 "reciprocal rank fusion. A hit must share a word with the query or clear a similarity floor, "
                 "so an unrelated store returns nothing.",
                 src="gate", status="ok" if facts else "skip",
                 data={"query": decision.query, "hits": [h.to_json() for h in facts], "stored": len(self.store.facts())})
        episodes = self.store.search_episodes(decision.query, k=2)
        rec.emit("memory.episodes", "episodic", f"Episodic memory: {len(episodes)} episode(s) found",
                 "Episodes are dated summaries of past sessions: what happened and when, as opposed to timeless "
                 "facts. They answer 'what did we do last week?'.",
                 src="gate", status="ok" if episodes else "skip",
                 data={"query": decision.query, "hits": [h.to_json() for h in episodes]})
        if facts or episodes:
            t.state.private_read = True
        return facts + episodes

    def _run_loop(self, t: _Turn) -> None:
        def checkpoint(step: int, messages: list[Message]) -> None:
            self.store.save_checkpoint(t.rec.turn_id, step, {
                "message": t.message, "session": t.session, "options": t.options.to_dict(),
                "system": t.system, "messages": messages})
            t.rec.emit("checkpoint.save", "checkpoint", f"Checkpoint after step {step}",
                       "The loop's whole state (prompt and conversation) is saved after every round. If the "
                       "process dies now, `loopwright resume` continues from here.",
                       src="model", data={"step": step, "messages": len(messages)})

        t.loop = run_loop(t.gateway, t.system, t.messages, t.registry, t.gate, t.rec, t.options,
                          start=t.resumed_from + 1,
                          before_call=lambda messages: ctx_mod.trim_seen(messages, t.rec),
                          after_step=checkpoint)
        t.reply = t.loop.text

    def _review(self, t: _Turn) -> None:
        if t.options.pattern != "reflect":
            return
        passed, feedback = patterns.critique(t.message, t.reply, t.tools.cited, t.gateway, t.rec)
        if passed:
            return
        t.messages.append({"role": "user", "content": f"[critic] Revise your last answer. {feedback}"})
        # one revision, and it may not start new tool calls: the cap that keeps a critic from looping
        revised = t.gateway.call(t.system, t.messages, t.registry.schemas(), purpose="loop",
                                 iteration=t.loop.iterations + 1, src="critic")
        t.messages.append({"role": "assistant", "content": revised.text, "tool_calls": []})
        t.reply = revised.text or t.reply

    def _guard_reply(self, t: _Turn) -> None:
        if t.options.guardrails:
            guarded = guardrails.guard_output(t.reply, t.tools.cited)
            t.rec.emit("guard.output", "guard_out", "Output checked" + ("" if guarded.clean else f": {len(guarded.findings)} finding(s)"),
                       "The last check before the reply leaves. Secrets are masked, and every handbook citation "
                       "is compared with the passages this turn actually retrieved: a citation to something "
                       "never read is a fabricated one.",
                       src="model", status="ok" if guarded.clean else "warn",
                       data={"findings": guarded.to_json(), "citations_available": sorted(t.tools.cited)})
            t.reply = guarded.text
        else:
            t.rec.emit("guard.output", "guard_out", "Output guard is off", "Guardrails are switched off for this turn.",
                       src="model", status="skip")

    def _fail(self, t: _Turn, exc: Exception, bug: bool = False) -> None:
        t.ok = False
        if bug:
            t.reply = f"Something inside the harness broke ({type(exc).__name__}: {exc}). The trace of this turn was saved."
            t.rec.emit("turn.failed", "model", f"The turn failed: {type(exc).__name__}",
                       "An unexpected error was caught at the top of the turn. The user still gets a plain message "
                       "and the trace is still written, which is what makes the bug findable.",
                       src="model", status="error", data={"error": f"{type(exc).__name__}: {exc}", "bug": True})
            return
        t.reply = (f"I could not get an answer from the model ({exc}). Nothing was lost: run `loopwright doctor` "
                   "to check the provider, or switch to the built-in scripted model.")
        t.rec.emit("turn.failed", "model", "The turn failed: no model could answer",
                   "Every provider in the chain failed. The harness still returns a clear message and a complete "
                   "trace, and the last checkpoint is kept so the turn can be resumed.",
                   src="model", status="error", data={"error": str(exc)})

    def _finish(self, t: _Turn) -> TurnResult:
        rec = t.rec
        # 12 ---- reply
        rec.emit("reply.send", "reply", "Reply sent", "The answer leaves through the gateway it came in by.",
                 src="guard_out", status="ok" if t.ok else "error", data={"reply": t.reply})
        if t.ok:
            # 13 ---- save the exchange
            self.store.add_message(t.session, "user", t.message, rec.turn_id)
            self.store.add_message(t.session, "assistant", t.reply, rec.turn_id)
            self.store.clear_checkpoints(rec.turn_id)
            rec.emit("working.save", "working", "Exchange saved to working memory",
                     "Both sides of the exchange are appended to the session, to be re-sent next turn and read "
                     "later by consolidation.", src="reply", data={"session": t.session})
            # 14 ---- background memory write
            if t.options.memory:
                small = t.gateway.small("extract")
                done = consolidate(self.store, t.session, rec, every=t.options.consolidate_every, small_model=small)
                t.written = done.get("ADD", 0) + done.get("UPDATE", 0) if done.get("ran") else 0
        t.written += sum(1 for e in rec.find("memory.write") if e.data.get("action") in ("ADD", "UPDATE"))
        # 15 ---- score, receipt, trace
        checks = self._online_checks(t)
        rec.emit("evals.online", "evals", f"Turn checks: {sum(c['pass'] for c in checks)}/{len(checks)} passed",
                 "Online evals: cheap deterministic checks run on every real turn, not just on a test set. "
                 "They are how regressions are noticed in production before a user reports one.",
                 src="reply", status="ok" if all(c["pass"] for c in checks) else "warn", data={"checks": checks})
        duration = int((time.perf_counter() - t.started) * 1000)
        rcpt = receipt.build(t.gateway.calls, t.state.log, memory_used=len(t.memories), memory_written=t.written,
                             duration_ms=duration, iterations=t.loop.iterations if t.loop else 0,
                             stop=t.loop.stop if t.loop else "failed")
        rec.emit("cost.receipt", "cost", rcpt["line"],
                 "The receipt is built from the turn's own records: tokens by purpose, tool calls, memory use, "
                 "time. Token counts marked ~ are estimates.", src="evals", data=rcpt)
        rec.emit("trace.write", "trace", f"Trace saved: {len(rec.events) + 1} events",
                 "Every event of this turn is written to one JSONL file. That file is the full record: it is what "
                 "the replay reads, what the evals grade, and what you would ship to an observability backend.",
                 src="cost", data={"turn_id": rec.turn_id, "events": len(rec.events) + 1})
        header = {"turn_id": rec.turn_id, "session": t.session, "message": t.message,
                  "started": dt.datetime.now().isoformat(timespec="seconds"),
                  "provider": rcpt["provider"] or t.gateway.primary.name, "model": rcpt["model"] or t.gateway.primary.model,
                  "options": t.options.to_dict()}
        footer = {"reply": t.reply, "ok": t.ok, "receipt": rcpt, "checks": checks}
        path = write_trace(self.home, header, rec.events, footer)
        return TurnResult(rec.turn_id, t.reply, t.ok, list(rec.events), rcpt, checks, list(t.state.log), str(path))

    # ================================================================ helpers
    def _spawn(self, t: _Turn, task: str, n: int) -> dict[str, Any]:
        """Run one worker: same loop, fresh context, read-only tools."""
        rec = t.rec.child(f"sub:{n}")
        registry = self.registry.subset(WORKER_TOOLS)
        tools = ToolContext(self.store, self.home, rec, t.options, self.now)      # spawn=None: no grandchildren
        gate = ToolGate(registry, tools, t.options, rec, TurnState(chaos_left=0))
        rec.emit("subagent.start", "subagent", f"Worker {n} starts with a fresh context",
                 "The worker's context holds its task and nothing else: none of the conversation, none of the "
                 "user's memory.", src="tools", data={"task": task, "tools": registry.names()})
        options = Options.from_dict({**t.options.to_dict(), "max_iterations": 4})
        messages: list[Message] = [{"role": "user", "content": task}]
        try:
            result = run_loop(t.gateway, ctx_mod.SUBAGENT_SYSTEM, messages, registry, gate, rec, options, purpose="subagent")
            summary = result.text
        except ModelError as exc:
            summary = f"(worker failed: {exc})"
        tokens = sum(e.data.get("tokens_in", 0) + e.data.get("tokens_out", 0)
                     for e in t.rec.events if e.span == rec.span and e.kind == "model.call")
        return {"task": task, "summary": summary, "tokens": tokens, "cited": sorted(tools.cited)}

    def _online_checks(self, t: _Turn) -> list[dict[str, Any]]:
        log = t.state.log
        failed = [x["name"] for x in log if not x["ok"] and not x["blocked"]]
        blocked = [x["name"] for x in log if x["blocked"]]
        ungrounded = [f for e in t.rec.find("guard.output") for f in e.data.get("findings", []) if f["kind"] == "citation"]
        return [
            {"check": "replied", "pass": bool(t.reply.strip()) and t.ok, "note": "" if t.ok else "the model could not answer"},
            {"check": "loop ended by answering", "pass": bool(t.loop and t.loop.stop == "answered"),
             "note": t.loop.stop if t.loop else "no loop ran"},
            {"check": "within budget", "pass": t.gateway.tokens <= t.options.token_budget and t.state.calls <= t.options.max_tool_calls,
             "note": f"{t.gateway.tokens:,} tokens, {t.state.calls} tool call(s)"},
            {"check": "no tool left failing", "pass": not failed or bool(log and log[-1]["ok"]),
             "note": ", ".join(failed) or "none failed"},
            {"check": "citations grounded", "pass": not ungrounded, "note": f"{len(ungrounded)} ungrounded" if ungrounded else ""},
            {"check": "nothing had to be blocked", "pass": not blocked, "note": ", ".join(blocked)},
        ]
