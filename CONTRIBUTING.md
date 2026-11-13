# Contributing

Thanks for looking. Small, focused pull requests are the easiest to review.

```bash
git clone https://github.com/dev-null-k2/loopwright && cd loopwright
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
make test && make gate
```

Read [AGENTS.md](AGENTS.md) first: it has the nine rules every change follows.

## Good first contributions

- **A handbook card.** Add a `[[concept]]` to `loopwright/learn/concepts.toml` and run `make docs`. No Python.
- **A skill.** A folder with one `SKILL.md` under `loopwright/skills/`.
- **An eval case.** One JSON line in `loopwright/evals/datasets/`. Cases that currently fail are especially welcome.
- **A lesson.** A `[[lesson]]` in `loopwright/learn/lessons.toml`; a test checks that it produces the steps it promises.
- **A web search backend or a model provider.** One function behind an existing seam, plus a test with a fake transport.

## Before you open a pull request

- `make test` and `make gate` pass.
- If you changed what a step does, its event `detail` and its handbook card still tell the truth.
- If you changed the cockpit, `make demo` was re-run so the static demo matches.
- The description says what you tested and what you did not.
