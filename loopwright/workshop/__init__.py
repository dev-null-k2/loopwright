"""The workshop: exercises where the learner writes the code.

Watching an agent work explains what the parts are. Writing one of them is how
it sticks. Each exercise is a small function a real agent needs (the loop, a
retry, a limit in code, a grader), with starter code, a checker, hints, a
reference solution and a pointer to the full version in this repository.

    loopwright exercise                 the list, and what you have passed
    loopwright exercise loop            the brief, and where your file is
    loopwright exercise loop --check    run the checker on your file

Or write in the cockpit: Learn, then Exercises.

Your answers live in ~/.loopwright/workshop/<id>.py. Checking runs YOUR code,
so it runs in a child process with a time limit, a scrubbed environment and,
where the machine has one, the same OS sandbox the shell tool uses (no network,
writes only inside the workshop folder). It is still your code with your
permissions when no sandbox is available; the result says which applied.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import time
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

from loopwright.config import PACKAGE_DIR
from loopwright.tools.shell import clean_env, sandbox_for

HERE = Path(__file__).parent
TIMEOUT = 25
MAX_CODE = 20_000
MAX_OUTPUT = 3_000
MARK = "@@loopwright-result@@"
LEVELS = {1: "Warm-up", 2: "Core", 3: "Stretch"}


@lru_cache(maxsize=1)
def load_exercises() -> list[dict[str, Any]]:
    with (HERE / "exercises.toml").open("rb") as fh:
        exercises = tomllib.load(fh)["exercise"]
    for exercise in exercises:
        for key in ("starter", "solution", "brief"):
            exercise[key] = exercise[key].strip() + "\n"
    return exercises


def exercise(exercise_id: str) -> dict[str, Any]:
    for item in load_exercises():
        if item["id"] == exercise_id:
            return item
    raise KeyError(f"no exercise called {exercise_id!r}. `loopwright exercise` lists them.")


def folder(home: Path) -> Path:
    path = Path(home) / "workshop"
    path.mkdir(parents=True, exist_ok=True)
    return path


def answer_file(home: Path, exercise_id: str) -> Path:
    return folder(home) / f"{exercise(exercise_id)['id']}.py"


def code(home: Path, exercise_id: str) -> str:
    """The learner's current answer, or the starter if they have not begun."""
    file = answer_file(home, exercise_id)
    return file.read_text(encoding="utf-8") if file.is_file() else exercise(exercise_id)["starter"]


def save(home: Path, exercise_id: str, text: str) -> Path:
    if len(text) > MAX_CODE:
        raise ValueError(f"an answer is at most {MAX_CODE} characters")
    file = answer_file(home, exercise_id)
    file.write_text(text, encoding="utf-8")
    return file


def reset(home: Path, exercise_id: str) -> str:
    return save(home, exercise_id, exercise(exercise_id)["starter"]).read_text(encoding="utf-8")


def progress(home: Path) -> dict[str, Any]:
    file = folder(home) / "progress.json"
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    return {"passed": dict(data.get("passed", {})), "attempts": dict(data.get("attempts", {}))}


def _record(home: Path, exercise_id: str, passed: bool) -> dict[str, Any]:
    data = progress(home)
    data["attempts"][exercise_id] = data["attempts"].get(exercise_id, 0) + 1
    if passed and exercise_id not in data["passed"]:
        data["passed"][exercise_id] = dt.datetime.now().isoformat(timespec="seconds")
    (folder(home) / "progress.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def _limits() -> None:            # in the child, before it runs: a ceiling on CPU time
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT, TIMEOUT))


def check(home: Path, exercise_id: str, text: str | None = None) -> dict[str, Any]:
    """Save the answer (if given) and run the exercise's checks on it in a child process."""
    item = exercise(exercise_id)
    file = save(home, exercise_id, text) if text is not None else answer_file(home, exercise_id)
    if not file.is_file():
        file = save(home, exercise_id, item["starter"])
    work = folder(home)
    (work / ".tmp").mkdir(exist_ok=True)
    prefix, sandbox, _ = sandbox_for(work)
    program = ("import sys; sys.path.insert(0, sys.argv.pop(1)); "
               "from loopwright.workshop.runner import main; main()")
    command = [*prefix, sys.executable, "-c", program, str(PACKAGE_DIR.parent), item["id"], str(file)]
    env = {**clean_env(work), "TMPDIR": str(work / ".tmp"), "PYTHONDONTWRITEBYTECODE": "1", "LOOPWRIGHT_PROVIDER": "scripted"}
    started = time.monotonic()
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT, cwd=str(work), env=env,
                              stdin=subprocess.DEVNULL, preexec_fn=_limits if sys.platform != "win32" else None)
        out, err = done.stdout, done.stderr
    except subprocess.TimeoutExpired:
        out, err = "", ""
        result = {"error": f"Your code ran for more than {TIMEOUT} seconds and was stopped. Is there a loop that never ends?", "checks": []}
    else:
        line = next((ln for ln in reversed(out.splitlines()) if ln.startswith(MARK)), None)
        if line is None:
            tail = (err.strip().splitlines() or ["no output"])[-1]
            result = {"error": f"The checker could not finish: {tail}", "checks": []}
        else:
            result = json.loads(line[len(MARK):])
    printed = "\n".join(ln for ln in out.splitlines() if not ln.startswith(MARK)).strip()
    passed = not result["error"] and bool(result["checks"]) and all(c["pass"] for c in result["checks"])
    state = _record(home, item["id"], passed)
    return {"id": item["id"], "passed": passed, "error": result["error"], "checks": result["checks"],
            "output": printed[-MAX_OUTPUT:], "sandbox": sandbox, "seconds": round(time.monotonic() - started, 2),
            "attempts": state["attempts"][item["id"]], "debrief": item["debrief"] if passed else ""}


def install(home: Path, exercise_id: str) -> Path:
    """Add a passing answer to the agent. Only for exercises whose answer is a tool file."""
    item = exercise(exercise_id)
    if item.get("installs") != "tool":
        raise ValueError("this exercise's answer is not something the agent can load")
    if item["id"] not in progress(home)["passed"]:
        raise ValueError("pass the checks first")
    target = Path(home) / "tools" / f"workshop_{item['id']}.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(code(home, item["id"]), encoding="utf-8")
    return target


def overview(home: Path) -> dict[str, Any]:
    """Everything the cockpit needs: the exercises, the learner's code for each, and progress."""
    state = progress(home)
    items = [{**{k: v for k, v in item.items()}, "code": code(home, item["id"]), "passed": item["id"] in state["passed"],
              "attempts": state["attempts"].get(item["id"], 0), "path": str(answer_file(home, item["id"]))} for item in load_exercises()]
    return {"exercises": items, "levels": LEVELS, "folder": str(folder(home)), "passed": len(state["passed"]), "total": len(items)}
