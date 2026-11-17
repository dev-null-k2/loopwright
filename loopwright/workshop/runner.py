"""The child process that runs one exercise's checks against a learner's file.

    python -c "... from loopwright.workshop.runner import main; main()" <exercise id> <path to the answer>

It prints one line starting with MARK followed by JSON. Everything else on
stdout is the learner's own print output, which the cockpit shows too.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import traceback

from loopwright.workshop import checks

MARK = "@@loopwright-result@@"


def main() -> None:
    exercise, path = sys.argv[1], sys.argv[2]
    checks.ANSWER_FILE = path
    tester = checks.Tester()
    try:
        spec = importlib.util.spec_from_file_location("answer", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except SyntaxError as exc:
        print(MARK + json.dumps({"error": f"Python could not read your file: {exc.msg} (line {exc.lineno})", "checks": []}))
        return
    except BaseException as exc:      # noqa: BLE001  (anything the learner's top-level code does)
        line = next((frame.lineno for frame in reversed(traceback.extract_tb(exc.__traceback__)) if frame.filename == path), None)
        where = f" (line {line})" if line else ""
        print(MARK + json.dumps({"error": f"Your file raised {type(exc).__name__} while loading: {exc}{where}", "checks": []}))
        return
    checks.CHECKS[exercise](module, tester)
    print(MARK + json.dumps({"error": "", "checks": tester.results}))


if __name__ == "__main__":
    main()
