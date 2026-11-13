"""Workflows, scheduled tasks, the shell, the browser, chat gateways, model comparison and trace export."""
import datetime as dt
import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from loopwright import cli, otel, scheduler, workflows
from loopwright.config import Options
from loopwright.evals import compare, mine
from loopwright.events import read_trace, trace_dir
from loopwright.gateways import discord, slack, telegram, whatsapp
from loopwright.gateways.chat import ChatBridge
from loopwright.harness import Agent
from loopwright.tools.browser import Browser, find_chrome
from loopwright.tools.plugins import set_tool_enabled, switched_off
from loopwright.tools.shell import clean_env
from loopwright.ws import WebSocket, WebSocketClosed
from tests.conftest import NOW

needs_browser = pytest.mark.skipif(not find_chrome(), reason="no Chromium-family browser on this machine")


def wait_for(condition, seconds=5.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


# ------------------------------------------------------------------ workflows

def test_a_workflow_runs_in_rounds_and_the_model_is_called_at_fixed_points(agent, opts):
    agent.store.add_note("Ship the pricing page")
    result = agent.run_turn("/workflow morning-brief", options=opts)
    start = next(e for e in result.events if e.kind == "workflow.start")
    assert start.data["waves"] == [["agenda", "notes", "idea"], ["brief"]]
    assert [e.data["id"] for e in result.events if e.kind == "workflow.node"][-1] == "brief"
    assert "Ship the pricing page" in result.reply and result.receipt["model_calls"] == 1
    assert result.receipt["stop"] == "workflow" and all(c["pass"] for c in result.checks)
    assert "memory.gate" not in result.kinds() and "loop.stop" not in result.kinds()      # no agent loop ran


def test_a_route_runs_one_branch_and_skips_the_rest(agent, opts):
    result = agent.run_turn("/workflow triage: buy milk tomorrow", options=opts)
    states = {e.data["id"]: e.data["status"] for e in result.events if e.kind == "workflow.node"}
    assert states == {"research": "skipped", "capture": "done", "reply": "skipped", "done": "done"}
    assert agent.store.notes()[0]["text"] == "To do: buy milk tomorrow"


def test_workflow_files_are_validated(tmp_path):
    with pytest.raises(workflows.WorkflowError, match="cycle"):
        workflows.parse('[[node]]\nid="a"\nkind="model"\nafter=["b"]\nprompt="x"\n[[node]]\nid="b"\nkind="model"\nafter=["a"]\nprompt="x"')
    with pytest.raises(workflows.WorkflowError, match="does not exist"):
        workflows.parse('[[node]]\nid="a"\nkind="model"\nafter=["ghost"]\nprompt="x"')
    with pytest.raises(workflows.WorkflowError, match="use tool, model, agent or route"):
        workflows.parse('[[node]]\nid="a"\nkind="magic"')
    (tmp_path / "workflows").mkdir()
    (tmp_path / "workflows" / "mine.toml").write_text('name="mine"\n[[node]]\nid="say"\nkind="tool"\ntool="clock"\n')
    (tmp_path / "workflows" / "broken.toml").write_text("this is not toml [")
    found = workflows.load_all(tmp_path)
    assert found["mine"].source == "yours" and "broken" not in found and "triage" in found
    assert workflows.render({"a": "{{x}} and {{y|fallback}}", "b": [1, "{{x}}"]}, {"x": "1"}) == {"a": "1 and fallback", "b": [1, "1"]}


def test_the_agent_can_choose_a_workflow_as_a_tool_and_an_unknown_one_is_reported(agent, opts):
    result = agent.run_turn("run the triage workflow on: remember to call the bank", options=opts)
    assert [t["name"] for t in result.tool_log] == ["save_note", "run_workflow"] and "workflow.end" in result.kinds()
    assert "call the bank" in agent.store.notes()[0]["text"]
    assert "no workflow named 'nope'" in agent.run_turn("/workflow nope", options=opts).reply


def test_a_failing_step_does_not_lose_the_other_steps(fake_agent, opts):
    agent, model = fake_agent(RuntimeError("the model is down"))
    result = agent.run_turn("/workflow morning-brief", options=Options(provider="fake", fast=True, retries=0, fallback=False))
    end = next(e for e in result.events if e.kind == "workflow.end")
    assert end.status == "warn" and end.data["state"]["brief"] == "failed" and end.data["state"]["agenda"] == "done"
    assert "brief was failed" in result.reply


# ------------------------------------------------------------------ scheduled tasks

def test_schedules_are_normalised_and_the_next_run_is_computed():
    thursday_evening = NOW
    cases = {"Every weekday at 8am": ("weekdays 08:00", "2026-11-13 08:00"), "daily 7:30pm": ("daily 19:30", "2026-11-12 19:30"),
             "every 45 minutes": ("every 45m", "2026-11-12 18:45"), "every monday at 9am": ("weekly mon 09:00", "2026-11-16 09:00"),
             "monthly 1 09:00": ("monthly 1 09:00", "2026-12-01 09:00"), "0 8 * * 1-5": ("0 8 * * 1-5", "2026-11-13 08:00"),
             "0 10 * * 0": ("0 10 * * 0", "2026-11-15 10:00"), "once 2026-11-20 14:00": ("once 2026-11-20 14:00", "2026-11-20 14:00")}
    for said, (stored, upcoming) in cases.items():
        assert scheduler.normalise(said) == stored
        assert scheduler.next_after(stored, thursday_evening).strftime("%Y-%m-%d %H:%M") == upcoming
    assert scheduler.next_after("once 2026-11-01 09:00", thursday_evening) is None
    for nonsense in ("sometimes", "daily 25:00", "every 0m", "61 * * * *"):
        with pytest.raises(ValueError):
            scheduler.normalise(nonsense)


def test_a_due_task_runs_once_unattended_and_moves_its_next_run(tmp_path, opts):
    clock = [NOW]
    agent = Agent(home=tmp_path / "a", now=lambda: clock[0])
    agent.run_turn("Every 30 minutes, send Alex: the build is green", options=opts)
    task = agent.store.tasks()[0]
    assert task["schedule"] == "every 30m" and dt.datetime.fromtimestamp(task["next_run"]) == NOW + dt.timedelta(minutes=30)
    sched = scheduler.Scheduler(agent, opts)
    assert sched.tick() == []                                    # not due yet
    clock[0] = NOW + dt.timedelta(minutes=31)
    (result,) = sched.tick()
    assert result.kinds()[0] == "schedule.fire" and "toolgate.refuse" in result.kinds() and agent.store.outbox() == []
    assert next(e for e in result.events if e.kind == "gateway.receive").data["options"]["approvals"] == "deny"
    after = agent.store.tasks()[0]
    assert after["runs"] == 1 and after["last_turn"] == result.turn_id and after["next_run"] > task["next_run"]
    assert sched.tick() == []                                    # claimed: a second clock finds nothing to do
    assert not agent.store.claim_task(task["id"], task["next_run"], None)     # and a stale claim loses


def test_tasks_can_be_run_now_paused_and_delivered_to_the_chat_that_made_them(agent, opts):
    made = scheduler.add_task(agent.store, "What's on my calendar?", "daily 08:00", NOW, origin="telegram:42")
    result = agent.run_turn(f"/task {made['id']}", options=opts)
    assert result.kinds()[0] == "schedule.fire" and "Nothing on your calendar" in result.reply
    waiting = agent.store.take_deliveries("telegram")
    assert len(waiting) == 1 and "Scheduled task #1" in waiting[0]["body"] and agent.store.take_deliveries("telegram") == []
    agent.store.set_task(made["id"], enabled=False)
    assert agent.store.due_tasks(NOW.timestamp() + 10 ** 7) == []
    assert "#1" in agent.run_turn("what tasks are scheduled?", options=opts).reply
    assert "Cancelled task #1" in agent.run_turn("cancel task 1", options=opts).reply and agent.store.tasks() == []


# ------------------------------------------------------------------ the shell

def test_the_shell_is_opt_in_and_its_environment_is_scrubbed(tmp_path, opts, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    home = tmp_path / "s"
    agent = Agent(home=home, now=lambda: NOW)
    assert "run_command" in switched_off(home) and "run_command" not in [t["name"] for t in agent.registry.without(agent.disabled_tools).schemas()]
    assert "sk-ant-secret" not in json.dumps(clean_env(home)) and clean_env(home)["HOME"] == str(home)
    set_tool_enabled(home, "run_command", True)
    agent.load_extensions()
    assert "run_command" not in agent.disabled_tools
    result = agent.run_turn("run `echo key=$ANTHROPIC_API_KEY; pwd`", options=opts)
    shell = next(e for e in result.events if e.kind == "shell.run")
    assert shell.data["exit_code"] == 0 and "key=\n" in shell.data["stdout"] and shell.data["stdout"].strip().endswith("workspace")
    assert shell.data["sandbox"] in ("none", "sandbox-exec", "bubblewrap") and "sk-ant-secret" not in result.reply
    declined = agent.run_turn("run `echo two`", options=Options(provider="scripted", fast=True), approver=lambda request: False)
    assert "shell.run" not in declined.kinds() and "declined" in declined.reply
    set_tool_enabled(home, "run_command", False)
    assert "run_command" in switched_off(home)


def test_a_command_that_hangs_is_stopped(tmp_path, opts, monkeypatch):
    from loopwright.tools import shell
    monkeypatch.setattr(shell, "TIMEOUT", 1)
    (tmp_path / "settings.json").write_text('{"enabled_tools": ["run_command"]}')
    agent = Agent(home=tmp_path, now=lambda: NOW)
    result = agent.run_turn("run `sleep 5`", options=opts)
    assert next(e for e in result.events if e.kind == "shell.run").data["timed_out"] is True


# ------------------------------------------------------------------ the browser

@needs_browser
def test_the_browser_reads_clicks_types_and_refuses_private_addresses():
    browser = Browser()
    try:
        browser.open("demo://site")
        home = browser.snapshot()
        assert home["url"] == "demo://site/index.html" and '[2] link "Pricing"' in home["elements"]
        browser.click(2)
        assert "Team plan costs $8" in browser.snapshot()["text"]
        browser.open("demo://site")
        field = next(int(line[1:line.index("]")]) for line in browser.snapshot()["elements"] if "text field" in line)
        browser.type(field, "offline", submit=True)
        results = browser.snapshot()
        assert results["url"].startswith("demo://site/search.html?q=offline") and "Version 3.2" in results["text"]
        assert browser.screenshot()[:3] == b"\xff\xd8\xff"                       # a JPEG
        for forbidden in ("http://127.0.0.1:1/", "file:///etc/passwd", "http://169.254.169.254/latest/meta-data/"):
            with pytest.raises(Exception, match="not a public web address"):
                browser.open(forbidden)
        browser.open("data:text/html,<img src='http://127.0.0.1:9/x.png'><p>hello</p>")
        assert browser.blocked == ["http://127.0.0.1:9/x.png"]                   # the page's own requests are filtered too
    finally:
        browser.close()


@needs_browser
def test_a_browser_turn_runs_as_a_worker_saves_screenshots_and_taints_the_turn(agent, opts):
    result = agent.run_turn("Use the browser: open demo://site, follow the Pricing link and tell me what the Team plan costs", options=opts)
    actions = [e for e in result.events if e.kind == "browser.action"]
    assert [a.data["url"] for a in actions] == ["demo://site/index.html", "demo://site/pricing.html"]
    assert all(a.span == "sub:browser" for a in actions) and "$8 per person" in result.reply
    assert all((trace_dir(agent.home) / "shots" / a.data["shot"]).stat().st_size > 2000 for a in actions)
    assert "file://" not in json.dumps([e.to_json() for e in result.events])      # no local path leaks into a trace
    assert [t["name"] for t in result.tool_log] == ["browse_web"]                # the main agent saw one tool call
    follow_up = agent.run_turn("Use the browser: open demo://site, and then send Alex: hi", options=opts)
    assert agent.store.outbox() == [] or "toolgate.refuse" in follow_up.kinds()


# ------------------------------------------------------------------ the WebSocket client

def test_the_websocket_client_speaks_the_protocol(ws_server):
    big = "x" * 70000

    def script(send, recv):
        assert recv() == "hello"
        send(b"ping-me", opcode=9)              # a ping: the client must answer and carry on
        send("small")
        send(big)
        assert recv() == big
        send(b"\x03\xe8", opcode=8)             # close 1000

    server = ws_server(script)
    client = WebSocket(server.url, timeout=5)
    client.send("hello")
    assert client.recv() == "small" and client.recv() == big
    client.send(big)
    with pytest.raises(WebSocketClosed, match="1000"):
        client.recv()
    client.close()
    server.thread.join(2)
    assert server.error is None


# ------------------------------------------------------------------ chat gateways

def test_the_chat_bridge_allowlists_keeps_sessions_and_asks_for_approval_in_words(agent, opts):
    sent = []
    bridge = ChatBridge(agent, "telegram", lambda chat, text: sent.append((chat, text)), {"42"},
                        options=Options(provider="scripted", fast=True), approval_timeout=5)
    bridge.handle("666", "hi")
    assert "private" in sent[-1][1] and "666" in sent[-1][1] and agent.store.sessions() == []      # a stranger starts no turn
    bridge.handle("42", "/help")
    assert "/steps" in sent[-1][1]
    bridge.handle("42", "/steps")
    bridge.handle("42", "What is 6 * 7?")
    assert "= **42**" in sent[-1][1] and "- calculator: ok" in sent[-1][1]
    assert agent.store.sessions()[0]["session"] == "telegram-42"
    turn = threading.Thread(target=bridge.handle, args=("42", "send Alex: lunch at noon?"))
    turn.start()
    assert wait_for(lambda: any("Reply yes" in text for _, text in sent))
    bridge.handle("42", "yes")                                   # this message is the approval, not a new turn
    turn.join(5)
    assert agent.store.outbox()[0]["body"] == "lunch at noon?" and "Sent to Alex" in sent[-1][1]
    bridge.handle("42", "/new")
    bridge.handle("42", "hi")
    assert len(agent.store.sessions()) == 2
    scheduler.add_task(agent.store, "What's on my calendar?", "every 30m", NOW, origin="telegram:42")
    scheduler.Scheduler(agent, opts).run_task(agent.store.tasks()[0])
    assert bridge.deliver_waiting() == 1 and "Scheduled task #1" in sent[-1][1]


def test_telegram_polls_for_updates_and_replies(agent, monkeypatch):
    calls = []

    def api(token, method, payload, timeout=40):
        calls.append((method, payload))
        if method == "getUpdates" and payload["offset"] == 0:
            return [{"update_id": 7, "message": {"chat": {"id": 42}, "text": "What is 2 + 2?"}},
                    {"update_id": 8, "message": {"chat": {"id": 42}}}]             # a photo: no text, ignored
        return []

    monkeypatch.setenv("LOOPWRIGHT_PROVIDER", "scripted")
    telegram.run(agent, token="t", allowed={"42"}, api=api, once=True)
    assert wait_for(lambda: any(m == "sendMessage" for m, _ in calls))
    reply = next(p for m, p in calls if m == "sendMessage")
    assert reply["chat_id"] == "42" and "2 + 2 = **4**" in reply["text"]
    with pytest.raises(SystemExit, match="TELEGRAM_BOT_TOKEN"):
        telegram.run(agent, token="", once=True)


def test_discord_identifies_heartbeats_and_answers_mentions_and_dms(agent, ws_server, monkeypatch):
    monkeypatch.setenv("LOOPWRIGHT_PROVIDER", "scripted")
    got, posts = [], []

    def script(send, recv):
        send(json.dumps({"op": 10, "d": {"heartbeat_interval": 60000}}))
        got.append(json.loads(recv()))                               # Identify
        send(json.dumps({"op": 0, "t": "READY", "s": 1, "d": {"user": {"id": "BOT"}}}))
        send(json.dumps({"op": 0, "t": "MESSAGE_CREATE", "s": 2, "d": {
            "author": {"id": "U1"}, "channel_id": "DM1", "content": "What is 3 * 3?", "mentions": []}}))
        send(json.dumps({"op": 0, "t": "MESSAGE_CREATE", "s": 3, "d": {        # in a server, not mentioned: ignored
            "author": {"id": "U1"}, "channel_id": "C9", "guild_id": "G", "content": "chatter", "mentions": []}}))
        send(json.dumps({"op": 0, "t": "MESSAGE_CREATE", "s": 4, "d": {        # the bot's own message: ignored
            "author": {"id": "BOT", "bot": True}, "channel_id": "DM1", "content": "x", "mentions": []}}))
        time.sleep(0.3)

    server = ws_server(script)
    bridge, channel_of = discord.make_bridge(agent, "tok", {"U1"}, post=lambda token, channel, text: posts.append((channel, text)),
                                             open_dm=lambda token, user: "DM-OPENED")
    discord.session(bridge, channel_of, "tok", url=server.url, max_events=4)
    assert got[0]["op"] == 2 and got[0]["d"]["token"] == "tok" and got[0]["d"]["intents"] & (1 << 15)
    assert wait_for(lambda: posts) and posts == [("DM1", "3 * 3 = **9**.")]
    channel_of.clear()
    bridge.send("U1", "a scheduled result")                          # after a restart there is no channel: open a DM
    assert posts[-1] == ("DM-OPENED", "a scheduled result")


def test_slack_acknowledges_every_envelope_and_replies_through_the_web_api(agent, ws_server, monkeypatch):
    monkeypatch.setenv("LOOPWRIGHT_PROVIDER", "scripted")
    acks, calls = [], []

    def script(send, recv):
        send(json.dumps({"type": "hello"}))
        send(json.dumps({"envelope_id": "e1", "type": "events_api", "payload": {"event": {
            "type": "message", "channel_type": "im", "user": "U1", "channel": "D1", "text": "What is 10 - 4?"}}}))
        acks.append(json.loads(recv()))
        send(json.dumps({"envelope_id": "e2", "type": "events_api", "payload": {"event": {
            "type": "message", "channel_type": "im", "bot_id": "B1", "channel": "D1", "text": "my own echo"}}}))
        acks.append(json.loads(recv()))
        time.sleep(0.3)

    server = ws_server(script)
    bridge, channel_of = slack.make_bridge(agent, "xoxb", {"U1"}, call=lambda token, method, payload: calls.append((method, payload)))
    slack.session(bridge, channel_of, server.url, max_events=3)
    server.thread.join(3)
    assert acks == [{"envelope_id": "e1"}, {"envelope_id": "e2"}]
    assert wait_for(lambda: calls) and calls == [("chat.postMessage", {"channel": "D1", "text": "10 - 4 = **6**."})]


def test_whatsapp_verifies_the_webhook_and_drops_unsigned_deliveries(agent, monkeypatch):
    monkeypatch.setenv("LOOPWRIGHT_PROVIDER", "scripted")
    sent = []
    server, _ = whatsapp.make_server(agent, 0, token="t", phone_id="p", verify_token="verify-me", app_secret="s3cret",
                                     allowed={"15550001"}, send=lambda token, phone, to, text: sent.append((to, text)))
    server.server_address = ("127.0.0.1", server.server_address[1])
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/webhook"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        ok = opener.open(base + "?hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=12345", timeout=5)
        assert ok.read() == b"12345"
        with pytest.raises(urllib.error.HTTPError) as wrong:
            opener.open(base + "?hub.mode=subscribe&hub.verify_token=nope&hub.challenge=1", timeout=5)
        assert wrong.value.code == 403
        body = json.dumps({"entry": [{"changes": [{"value": {"messages": [
            {"from": "15550001", "type": "text", "text": {"body": "What is 9 + 1?"}}]}}]}]}).encode()
        with pytest.raises(urllib.error.HTTPError) as unsigned:
            opener.open(urllib.request.Request(base, data=body, headers={"X-Hub-Signature-256": "sha256=forged"}), timeout=5)
        assert unsigned.value.code == 403 and sent == []
        signature = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
        assert opener.open(urllib.request.Request(base, data=body, headers={"X-Hub-Signature-256": signature}), timeout=5).status == 200
        assert wait_for(lambda: sent) and sent == [("15550001", "9 + 1 = **10**.")]
    finally:
        server.shutdown()
    assert not whatsapp.signed("", body, signature)                    # no secret configured means nothing is trusted


# ------------------------------------------------------------------ comparison, your own cases, export

def test_compare_runs_the_same_cases_through_each_target_and_keeps_the_traces(tmp_path):
    targets = [compare.parse_target("scripted"), compare.parse_target("scripted+pattern=plan")]
    assert targets[1]["options"] == {"pattern": "plan"} and targets[1]["slug"] == "scripted-pattern-plan"
    with pytest.raises(ValueError, match="unknown provider"):
        compare.parse_target("gpt-9000")
    seen = []
    report = compare.run_compare(targets, tmp_path, on_progress=seen.append)
    plain, planned = report["targets"]
    assert plain["passed"] == planned["passed"] == 8 and len(seen) == 16
    assert planned["tokens"] > plain["tokens"]                         # planning costs an extra call per turn
    assert (tmp_path / "traces" / "scripted-pattern-plan" / "live-01.jsonl").is_file()
    assert json.loads((tmp_path / "compare.json").read_text())["cases"][0]["id"] == "live-01"
    assert "8/8" in compare.format_table(report)


def test_an_unreachable_model_fails_fast_in_a_comparison(tmp_path):
    report = compare.run_compare([compare.parse_target("local")], tmp_path)
    statuses = [row["status"] for row in report["targets"][0]["rows"]]
    assert statuses[0] == "unreachable" and set(statuses[1:]) == {"skipped"} and report["targets"][0]["passed"] == 0


def test_a_real_run_becomes_an_eval_case_and_feedback_is_kept(agent, opts):
    result = agent.run_turn("What's 12 * 7.5, and save that as a note", options=opts)
    trace = read_trace(result.trace_path)
    case = mine.add_case(agent.home, trace, "arithmetic then a note")
    assert case["turns"] == ["What's 12 * 7.5, and save that as a note"]
    assert {"tool_called": "calculator"} in case["expect"] and {"tool_called": "save_note"} in case["expect"]
    mine.add_case(agent.home, trace)                                    # saving the same run twice replaces, not duplicates
    assert len(mine.load_mine(agent.home)) == 1
    outcome = mine.run_mine(agent.home, {"provider": "scripted", "fast": True})
    assert outcome == {**outcome, "passed": 1, "total": 1}
    mine.add_feedback(agent.home, result.turn_id, "down", "wrong rounding")
    mine.add_feedback(agent.home, result.turn_id, "up")
    assert mine.load_feedback(agent.home)[result.turn_id]["rating"] == "up"
    assert mine.remove_case(agent.home, case["id"]) and mine.load_mine(agent.home) == []


def test_a_trace_exports_as_opentelemetry_spans(agent, opts, monkeypatch):
    result = agent.run_turn("What is 18% of 2450?", options=opts)
    spans = otel.to_otlp(read_trace(result.trace_path))["resourceSpans"][0]["scopeSpans"][0]["spans"]
    root, children = spans[0], spans[1:]
    attrs = lambda span: {a["key"]: next(iter(a["value"].values())) for a in span["attributes"]}  # noqa: E731
    assert root["name"] == "invoke_agent loopwright" and attrs(root)["gen_ai.operation.name"] == "invoke_agent"
    assert [c["name"] for c in children] == ["chat scripted-v1", "execute_tool calculator", "chat scripted-v1"]
    assert all(c["parentSpanId"] == root["spanId"] and c["traceId"] == root["traceId"] for c in children)
    assert attrs(children[0])["gen_ai.usage.input_tokens"] != "0" and attrs(children[1])["gen_ai.tool.name"] == "calculator"
    assert "memory.gate" in [e["name"] for e in root["events"]]
    assert int(root["startTimeUnixNano"]) <= int(children[0]["startTimeUnixNano"]) <= int(root["endTimeUnixNano"])

    received = []

    class Collector(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            received.append((self.path, self.headers.get("x-api-key"), json.loads(self.rfile.read(int(self.headers["content-length"])))))
            self.send_response(200)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("NO_PROXY", "127.0.0.1")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", f"http://127.0.0.1:{server.server_address[1]}")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "x-api-key=abc")
        agent.run_turn("hi", options=opts)                              # every turn is exported as it finishes
        assert wait_for(lambda: received)
        assert received[0][0] == "/v1/traces" and received[0][1] == "abc" and "resourceSpans" in received[0][2]
    finally:
        server.shutdown()


def test_the_new_commands_work_from_the_terminal(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("LOOPWRIGHT_PROVIDER", "scripted")
    assert cli.main(["workflow"]) == 0 and "morning-brief" in capsys.readouterr().out
    assert cli.main(["workflow", "triage", "buy", "stamps", "--quiet"]) == 0 and "buy stamps" in capsys.readouterr().out
    bad = tmp_path / "bad.toml"
    bad.write_text('[[node]]\nid="a"\nkind="model"\nafter=["a"]\nprompt="x"')
    assert cli.main(["workflow", "--check", str(bad)]) == 1 and "not valid" in capsys.readouterr().out
    assert cli.main(["tasks", "add", "weekdays 08:00", "What", "is", "on", "my", "calendar?"]) == 0
    assert "Monday to Friday at 08:00" in capsys.readouterr().out
    assert cli.main(["tasks"]) == 0 and "#1" in capsys.readouterr().out
    assert cli.main(["tasks", "run", "1"]) == 0 and "calendar" in capsys.readouterr().out
    assert cli.main(["tasks", "pause", "1"]) == 0 and cli.main(["tasks", "tick"]) == 0 and "0 task(s) were due" in capsys.readouterr().out
    assert cli.main(["tasks", "rm", "1"]) == 0 and cli.main(["tasks", "add", "whenever"]) == 2
    capsys.readouterr()
    out = tmp_path / "trace.json"
    assert cli.main(["export", "last", "--out", str(out)]) == 0 and "resourceSpans" in out.read_text()
    assert cli.main(["compare", "--targets", "scripted", "--out", str(tmp_path / "cmp")]) == 0 and "8/8" in capsys.readouterr().out
    assert cli.main(["eval", "--mine"]) == 0 and "no cases yet" in capsys.readouterr().out
    assert cli.main(["doctor"]) == 0 and "Chat gateways" in capsys.readouterr().out
