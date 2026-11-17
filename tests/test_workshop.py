"""The workshop: exercises where the learner writes the code, and a checker that runs it."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from loopwright import workshop
from loopwright.config import PACKAGE_DIR
from loopwright.harness import Agent
from loopwright.learn import load_concepts
from loopwright.server import App, make_handler
from loopwright.workshop import checks as exercise_checks

EXERCISES = workshop.load_exercises()


@pytest.mark.parametrize("item", EXERCISES, ids=[e["id"] for e in EXERCISES])
def test_the_solution_passes_and_the_starter_does_not(item, tmp_path):
    """An exercise whose starter passes teaches nothing, and one whose solution fails cannot be finished."""
    solved = workshop.check(tmp_path, item["id"], item["solution"])
    assert solved["passed"], [c for c in solved["checks"] if not c["pass"]] or solved["error"]
    assert len(solved["checks"]) >= 3 and solved["debrief"] == item["debrief"]
    started = workshop.check(tmp_path, item["id"], item["starter"])
    assert not started["passed"] and not started["error"]            # the starter runs; it is just not right yet
    failing = [c for c in started["checks"] if not c["pass"]]
    assert failing and all(c["detail"] for c in failing)             # every failure says what was expected


def test_every_exercise_points_at_real_cards_and_real_files():
    cards = {c["id"] for c in load_concepts()}
    assert len(EXERCISES) == 12 and len({e["id"] for e in EXERCISES}) == 12
    for item in EXERCISES:
        assert set(item["concepts"]) <= cards, item["id"]
        assert (PACKAGE_DIR.parent / item["file"]).is_file(), item["file"]
        assert item["level"] in workshop.LEVELS and len(item["hints"]) >= 3 and len(item["brief"]) > 300
        assert item["id"] in exercise_checks.CHECKS
        assert item["starter"] != item["solution"] and item["interview"] and item["debrief"]


def test_broken_answers_are_explained_not_crashed(tmp_path, monkeypatch):
    broken = workshop.check(tmp_path, "rrf", "def fuse(rankings, k=60):\n    return [\n")
    assert not broken["passed"] and "line 2" in broken["error"] and broken["checks"] == []

    raising = workshop.check(tmp_path, "rrf", "def fuse(rankings, k=60):\n    print('looking at', len(rankings))\n    return rankings[5]\n")
    assert not raising["passed"] and "IndexError" in raising["checks"][0]["detail"] and "(line 3)" in raising["checks"][0]["detail"]
    assert "looking at 1" in raising["output"]                       # what the learner printed comes back, for debugging

    at_import = workshop.check(tmp_path, "rrf", "import does_not_exist\n")
    assert "ModuleNotFoundError" in at_import["error"] and "line 1" in at_import["error"]

    renamed = workshop.check(tmp_path, "rrf", "def merge(rankings, k=60):\n    return []\n")
    assert "no `fuse`" in renamed["checks"][0]["detail"]

    monkeypatch.setattr(workshop, "TIMEOUT", 2)
    endless = workshop.check(tmp_path, "rrf", "def fuse(rankings, k=60):\n    while True:\n        pass\n")
    assert not endless["passed"] and "stopped" in endless["error"]

    with pytest.raises(ValueError):
        workshop.save(tmp_path, "rrf", "x" * (workshop.MAX_CODE + 1))
    with pytest.raises(KeyError):
        workshop.check(tmp_path, "../../etc/passwd", "x = 1")


def test_progress_is_kept_and_the_answer_is_a_file_you_can_edit(tmp_path):
    assert workshop.code(tmp_path, "retry") == workshop.exercise("retry")["starter"]
    workshop.check(tmp_path, "retry", workshop.exercise("retry")["starter"])
    workshop.check(tmp_path, "retry", workshop.exercise("retry")["solution"])
    state = workshop.progress(tmp_path)
    assert state["attempts"]["retry"] == 2 and "retry" in state["passed"]
    assert workshop.answer_file(tmp_path, "retry").read_text() == workshop.exercise("retry")["solution"]
    # edited in your own editor, then checked with no code passed in: the file is what is checked
    workshop.answer_file(tmp_path, "retry").write_text(workshop.exercise("retry")["starter"])
    assert not workshop.check(tmp_path, "retry")["passed"]
    assert "retry" in workshop.progress(tmp_path)["passed"]          # once passed, it stays passed
    assert workshop.reset(tmp_path, "retry") == workshop.exercise("retry")["starter"]
    view = workshop.overview(tmp_path)
    assert view["passed"] == 1 and view["total"] == 12 and view["exercises"][0]["path"].endswith(".py")


def test_a_tool_you_wrote_can_be_added_to_the_agent(tmp_path):
    with pytest.raises(ValueError, match="pass the checks"):
        workshop.install(tmp_path, "tool")
    with pytest.raises(ValueError):
        workshop.install(tmp_path, "retry")                          # not a tool
    assert workshop.check(tmp_path, "tool", workshop.exercise("tool")["solution"])["passed"]
    file = workshop.install(tmp_path, "tool")
    assert file.parent == tmp_path / "tools"
    agent = Agent(home=tmp_path)
    assert "word_count" in agent.registry.names()
    agent.store.close()


def test_the_cockpit_can_list_check_and_reset_exercises(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOPWRIGHT_PROVIDER", "scripted")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(App(tmp_path / "home")))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(path, body=None):
        request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}{path}",
                                         data=json.dumps(body).encode() if body is not None else None, headers={"content-type": "application/json"})
        try:
            with opener.open(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    try:
        status, view = call("/api/workshop")
        assert status == 200 and view["total"] == 12 and view["exercises"][0]["code"]
        assert len(call("/api/handbook")[1]["exercises"]) == 12       # the briefs travel with the handbook, so the recorded demo has them
        status, result = call("/api/workshop/stuck/check", {"code": workshop.exercise("stuck")["solution"]})
        assert status == 200 and result["passed"] and result["sandbox"]
        assert call("/api/workshop")[1]["passed"] == 1
        assert call("/api/workshop/stuck/reset", {})[1]["code"] == workshop.exercise("stuck")["starter"]
        assert call("/api/workshop/nope/check", {"code": "x = 1"})[0] == 404
        assert call("/api/workshop/stuck/install", {})[0] == 400
    finally:
        httpd.shutdown()
