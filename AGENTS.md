# AGENTS.md

For anyone changing this repository, human or coding agent.

Loopwright is an agent built to be watched and read. Every change has to keep both true.

## The rules

1. **Every step emits an event.** Nothing prints, nothing logs. If you add a mechanism, emit an event for it with
   `rec.emit(kind, stage, title, detail, ...)`. `detail` is one or two plain sentences saying what happened and
   why the mechanism exists; a newcomer reads it in the cockpit.
2. **Every event kind has exactly one handbook card.** Add the kind to a card's `kinds` in
   `loopwright/learn/concepts.toml`, or write a new card. `tests/test_server_evals.py` fails otherwise.
3. **Every box on the board is a stage in `events.STAGES`,** and the reverse. Add both or neither.
4. **Standard library only.** No runtime dependency. The cockpit is plain HTML, CSS and ES modules with no build step.
5. **Every behaviour change gets an eval case** in `loopwright/evals/datasets/`, and a bug fix adds the case that
   would have caught it. `loopwright eval --gate` must pass.
6. **Limits go in code, not in the prompt.** Anything that must never happen is enforced in `toolgate.py` or
   inside the tool.
7. **Say what was and was not tested.** No number in the README that `results/eval-report.json` or the test suite
   does not back. The scripted model is described as what it is.
8. **Never wipe a user's state** (`~/.loopwright`) except on their explicit request.
9. **Generated files are generated.** `docs/glossary.md`, `lessons.md`, `interview-prep.md`, `events.md` come from
   `python scripts/build_docs.py`; `docs/index.html`, `docs/js`, `docs/demo` from `python scripts/build_demo.py`.
   Edit the sources.

## Commands

`make install` · `make test` · `make gate` · `make docs` · `make demo` · `make serve`

## Where things are

Start at `loopwright/harness.py` (`run_turn`), then `loop.py`, then `toolgate.py`. `docs/architecture.md` walks a
turn through the code.
