"""The web server, the evals and release gate, the handbook's integrity, and the CLI."""
import json
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from loopwright import cli
from loopwright.config import PACKAGE_DIR
from loopwright.evals import gate as release_gate
from loopwright.evals.runner import SUITES, load_cases, run_all, run_case
from loopwright.events import STAGES
from loopwright.learn import load_concepts, load_interview, load_lessons
from loopwright.server import App, make_handler
from loopwright.tools.browser import find_chrome

ROOT = PACKAGE_DIR.parent


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOPWRIGHT_PROVIDER", "scripted")
    app = App(tmp_path / "srv")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(path, body=None, method=None, headers=None):
        request = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                         method=method or ("POST" if body is not None else "GET"),
                                         headers={"content-type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    def chat(message, **extra):
        _, text = call("/api/chat", {"message": message, "options": {"fast": True, "search": "demo"}, **extra})
        rows = [json.loads(line[6:]) for line in text.split("\n\n") if line.startswith("data: ")]
        return [r for r in rows if r["type"] == "event"], next(r for r in rows if r["type"] == "done")

    yield app, call, chat
    httpd.shutdown()


def test_chat_streams_every_event_then_the_result_and_the_run_can_be_fetched_for_replay(server):
    app, call, chat = server
    events, done = chat("What's 18% of 2450?", session="s1")
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1)) and events[-1]["kind"] == "trace.write"
    assert "441" in done["reply"] and done["receipt"]["tool_calls"] == 1
    replay = json.loads(call(f"/api/runs/{done['turn_id']}")[1])
    assert [e["kind"] for e in replay["events"]] == [e["kind"] for e in events]
    assert json.loads(call("/api/sessions")[1])["sessions"][0]["session"] == "s1"
    assert [m["role"] for m in json.loads(call("/api/sessions/s1")[1])["messages"]] == ["user", "assistant"]
    overview = json.loads(call("/api/overview")[1])
    assert overview["turns"] == 1 and overview["tools"][0] == {"name": "calculator", "ran": 1, "failed": 0, "refused": 0}


def test_the_local_server_refuses_dns_rebinding_cross_site_posts_and_path_escapes(server):
    _, call, _ = server
    assert call("/api/health", headers={"Host": "evil.example"})[0] == 403
    assert call("/api/memory/reset", {"confirm": "reset"}, headers={"Origin": "https://evil.example"})[0] == 403
    assert call("/api/memory/reset", {})[0] == 400                               # wiping needs an explicit confirm
    assert call("/api/source?path=loopwright/loop.py")[0] == 200
    for path in ("../../etc/passwd", "/etc/passwd", "pyproject.toml", "loopwright/web/fonts/barlow-latin-400-normal.woff2"):
        assert call(f"/api/source?path={path}")[0] == 404
    assert call("/../../etc/passwd")[0] == 404 and call("/js/main.js")[0] == 200 and call("/")[0] == 200


def test_the_classroom_is_a_separate_agent_that_resets_and_never_touches_real_memory(server):
    app, call, chat = server
    chat("Remember that Alex prefers morning meetings")
    chat("Remember that Sam prefers evening meetings", space="classroom")
    main = [f["text"] for f in json.loads(call("/api/memory")[1])["facts"]]
    room = [f["text"] for f in json.loads(call("/api/memory?space=classroom")[1])["facts"]]
    assert main == ["Alex prefers morning meetings"] and room == ["Sam prefers evening meetings"]
    call("/api/classroom/reset", {})
    assert json.loads(call("/api/memory?space=classroom")[1])["facts"] == [] and len(json.loads(call("/api/memory")[1])["facts"]) == 1


def test_an_approval_pauses_the_turn_until_the_browser_answers(server):
    app, call, _ = server
    box = {}

    def run():
        _, text = call("/api/chat", {"message": "Send a message to Priya: hi", "options": {"fast": True, "approvals": "ask"}})
        box["done"] = json.loads([line for line in text.split("\n\n") if '"type": "done"' in line][0][6:])

    thread = threading.Thread(target=run)
    thread.start()
    for _ in range(200):                                                         # wait for the turn to reach the gate
        if app._pending:
            break
        threading.Event().wait(0.02)
    call_id = next(iter(app._pending))
    assert app.agent.store.outbox() == []                                        # paused: nothing sent yet
    assert json.loads(call("/api/approve", {"call_id": call_id, "approved": True})[1])["ok"]
    thread.join(10)
    assert "Sent to Priya" in box["done"]["reply"] and len(app.agent.store.outbox()) == 1


def test_skills_identity_tools_and_mcp_can_be_managed_over_the_api(server):
    app, call, chat = server
    assert call("/api/skills", {"name": "Standup", "description": "standups", "triggers": ["standup"], "body": "three bullets"})[0] == 200
    assert call("/api/skills", {"name": "", "description": "", "body": ""})[0] == 400
    events, _ = chat("standup please")
    assert next(e for e in events if e["kind"] == "skills.match")["data"]["loaded"][0]["name"] == "standup"
    call("/api/identity", {"text": "You are Pat."})
    assert json.loads(call("/api/memory")[1])["identity"] == "You are Pat."
    call("/api/identity", {"text": ""})
    assert json.loads(call("/api/memory")[1])["identity_is_custom"] is False
    call("/api/tools/toggle", {"name": "send_message", "enabled": False})
    tools = {t["name"]: t for t in json.loads(call("/api/tools")[1])["tools"]}
    assert tools["send_message"]["enabled"] is False and tools["calculator"]["effect"] == "read" and len(tools) >= 17
    assert call("/api/skills/standup", method="DELETE")[0] == 200


# ------------------------------------------------------------------ evals

def test_the_full_eval_run_passes_the_release_gate_and_the_ablations_have_teeth(tmp_path):
    report = run_all(tmp_path)
    failed = [c["check"] for c in report["verdict"]["checks"] if not c["pass"]]
    assert report["verdict"]["passed"], failed
    assert report["cases_passed"] == report["cases_total"] >= 50 and report["attack_success_rate"] == 0
    ab = report["ablations"]
    assert ab["no-guardrails"]["attack_success_rate"] == 1.0 and ab["no-memory"]["memory"] < 0.5
    assert ab["always-retrieve"]["memory_searches"] > 2 * ab["full"]["memory_searches"]
    assert ab["no-retries"]["reliability"] < 1.0 and report["suites"]["reliability"]["pass_all_trials"] == 1.0
    assert report["judge"]["skipped"] and (tmp_path / "traces" / "core-01.jsonl").is_file()
    assert json.loads((tmp_path / "eval-report.json").read_text())["cases_total"] == report["cases_total"]


def test_the_gate_fails_when_quality_drops():
    case = next(c for c in load_cases("safety") if c["id"] == "safe-01")
    assert run_case(case)["passed"] and not run_case(case, {"guardrails": False})["passed"]
    report = {"suites": {s: {"pass_at_1": 1.0, "passed": 1, "cases": 1} for s in SUITES}, "attack_success_rate": 0.25,
              "retrieval": {"hybrid": {"recall_at_3": 1.0}}, "gate": {"accuracy": 1.0, "false_skip_rate": 0.0}}
    verdict = release_gate.evaluate(report)
    assert not verdict["passed"] and [c["check"] for c in verdict["checks"] if not c["pass"]] == ["no injection attack succeeds"]


def test_cli_eval_gate_exit_code_and_other_commands(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("LOOPWRIGHT_PROVIDER", "scripted")
    assert cli.main(["eval", "--gate", "--no-ablations", "--out", str(tmp_path)]) == 0
    assert "GATE PASSED" in capsys.readouterr().out
    monkeypatch.setitem(release_gate.THRESHOLDS, "gate_accuracy_min", 0.99)
    assert cli.main(["eval", "--gate", "--no-ablations", "--out", str(tmp_path)]) == 1
    capsys.readouterr()
    assert cli.main(["ask", "What's", "2", "+", "2?", "--steps"]) == 0
    out, err = capsys.readouterr()
    assert "2 + 2 = **4**" in out and "Tool gate" in err
    assert cli.main(["replay", "last", "--speed", "0", "--brief"]) == 0 and "Ran calculator" in capsys.readouterr().out
    assert cli.main(["learn", "retrieval", "gate"]) == 0 and "Why it exists" in capsys.readouterr().out
    assert cli.main(["doctor"]) == 0 and "scripted" in capsys.readouterr().out
    assert cli.main(["runs"]) == 0 and cli.main(["memory"]) == 0
    capsys.readouterr()
    assert cli.main(["selftest", "--provider", "scripted"]) == 0 and "8/8 passed on scripted" in capsys.readouterr().out


# ------------------------------------------------------------------ the handbook and the board cannot drift from the code

def test_every_card_points_at_a_real_file_and_a_real_box():
    concepts = load_concepts()
    ids = [c["id"] for c in concepts]
    assert len(ids) == len(set(ids)) >= 80
    for c in concepts:
        assert (ROOT / c["file"]).is_file(), f"{c['id']}: {c['file']} does not exist"
        assert c["node"] in STAGES, f"{c['id']}: unknown box {c['node']}"
        assert all(c.get(k) for k in ("term", "one_liner", "explain", "why", "tradeoff", "question", "answer", "try"))
    for _, _, file in STAGES.values():
        assert (ROOT / file).is_file()
    for lesson in load_lessons():
        assert all(i in ids for i in lesson["concepts"]) and (lesson.get("prompt") or lesson.get("open"))
    for scenario in load_interview():
        assert all(i in ids for i in scenario["concepts"]) and len(scenario["answer"]) > 600


def test_every_event_kind_the_engine_emits_is_explained_by_exactly_one_card():
    source = "\n".join(p.read_text() for p in PACKAGE_DIR.rglob("*.py"))
    emitted = set(re.findall(r'\.emit\(\s*"([a-z_]+\.[a-z_]+)"', source))
    explained = [k for c in load_concepts() for k in c.get("kinds", [])]
    assert len(explained) == len(set(explained)), "a kind is claimed by two cards"
    assert emitted - set(explained) == set(), "these steps have no card to explain them"
    assert set(explained) - emitted == set(), "these cards explain steps that no longer exist"
    lesson_kinds = {w["kind"] for lesson in load_lessons() for w in lesson["watch"]}
    assert lesson_kinds <= emitted


def test_the_board_draws_exactly_the_stages_the_engine_knows():
    board = (PACKAGE_DIR / "web" / "js" / "board.js").read_text()
    detailed = board[board.index("export const LAYOUT"): board.index("// ============================================================ SIMPLE")]
    assert set(re.findall(r"^\s+(\w+):\s+\[", detailed, re.M)) == set(STAGES)             # detailed: one box per stage
    edges = re.findall(r'\["(\w+)", "(\w+)"\]', detailed)
    assert edges and all(a in STAGES and b in STAGES for a, b in edges)

    simple = board[board.index("const SIMPLE_NODES"): board.index("const DETAILED")]
    boxes = dict(re.findall(r'id: "(\w+)".*?stages: \[([^\]]*)\]', simple))
    grouped = [stage for stages in boxes.values() for stage in re.findall(r'"(\w+)"', stages)]
    assert sorted(grouped) == sorted(STAGES), "every stage belongs to exactly one box of the simple drawing"
    assert len(boxes) < 20 < len(STAGES)
    links = re.findall(r'^\s+\["(\w+)", "(\w+)", `', simple, re.M)
    journeys = [key.split(">") for key in re.findall(r'"(\w+>\w+)":', simple)]
    assert links and all(a in boxes and b in boxes for a, b in [*links, *journeys])


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_every_cockpit_script_parses():
    for path in sorted((PACKAGE_DIR / "web" / "js").glob("*.js")):
        check = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        assert check.returncode == 0, check.stderr


def test_lessons_run_as_written_on_the_scripted_model(tmp_path):
    """Each lesson promises that certain steps will appear. Run them all and hold them to it."""
    from loopwright.config import Options
    from loopwright.harness import Agent
    from tests.conftest import NOW
    for lesson in load_lessons():
        if not lesson.get("prompt"):
            continue
        if lesson.get("needs") == "browser" and not find_chrome():
            continue
        home = tmp_path / lesson["id"]
        home.mkdir()
        (home / "settings.json").write_text(json.dumps({"enabled_tools": lesson.get("enable", [])}))
        agent = Agent(home=home, now=lambda: NOW)
        base = {"provider": "scripted", "fast": True, "search": "demo"}
        for prompt in lesson.get("before", []):
            agent.run_turn(prompt, session="a", options=Options.from_dict({**base, "approvals": "approve"}))
        options = Options.from_dict({**base, "approvals": "approve", **lesson.get("options", {})})
        result = agent.run_turn(lesson["prompt"], session="b" if lesson.get("new_session") else "a", options=options,
                                approver=lambda request: True)
        kinds = [e.kind for e in result.events]
        for watch in lesson["watch"]:
            assert watch["kind"] in kinds, f"lesson {lesson['id']} promises {watch['kind']}"


def test_a_phone_on_the_network_needs_the_access_key(tmp_path, monkeypatch):
    """`serve --lan`: a request that is not from this machine must carry the key from the printed link."""
    monkeypatch.setenv("LOOPWRIGHT_PROVIDER", "scripted")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(App(tmp_path / "lan"), access_key="k3y"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    phone = f"192.168.1.20:{port}"                      # the Host header a phone would send

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)

    def call(path, headers=None, body=None):
        request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode() if body is not None else None,
                                         headers={"Host": phone, "content-type": "application/json", **(headers or {})})
        try:
            with opener.open(request, timeout=20) as response:
                return response.status, dict(response.headers), response.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read().decode()

    try:
        assert call("/")[0] == 403 and call("/api/memory")[0] == 403                    # no key: nothing, not even the page
        assert call("/?key=guess")[0] == 403
        status, headers, _ = call("/?key=k3y")                                           # the link from the terminal
        assert status == 303 and headers["Location"] == "/" and "lw_key=k3y" in headers["Set-Cookie"] and "HttpOnly" in headers["Set-Cookie"]
        cookie = {"Cookie": "lw_key=k3y"}
        assert call("/", cookie)[0] == 200 and json.loads(call("/api/health", cookie)[2])["version"]
        assert call("/api/feedback", {**cookie, "Origin": f"http://{phone}"}, {"turn_id": "t", "rating": "up"})[0] == 200
        assert call("/api/feedback", {**cookie, "Origin": "https://evil.example"}, {"turn_id": "t", "rating": "up"})[0] == 403
        assert call("/api/feedback", {"Cookie": "lw_key=wrong", "Origin": f"http://{phone}"}, {"turn_id": "t", "rating": "up"})[0] == 403
        assert call("/api/health", {"Host": f"127.0.0.1:{port}"})[0] == 200             # this machine itself still needs no key
    finally:
        httpd.shutdown()
    plain = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(App(tmp_path / "plain")))  # without --lan there is no key to present
    threading.Thread(target=plain.serve_forever, daemon=True).start()
    port = plain.server_address[1]
    try:
        assert call("/?key=", {"Host": phone})[0] == 403 and call("/", {"Host": phone, "Cookie": "lw_key="})[0] == 403
    finally:
        plain.shutdown()
