"""Configuration: where state lives, and the switches a single turn can flip.

`Options` is deliberately one flat dataclass. Each field is a design decision
an agent builder has to make, and the Lab tab in the cockpit exposes every one
of them so you can turn a mechanism off and watch what breaks. The eval
ablations (docs/evals.md) are nothing more than runs with different Options.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

PACKAGE_DIR = Path(__file__).resolve().parent


def home_dir() -> Path:
    """Where memory, traces and the workspace live. One folder, yours to read."""
    path = Path(os.environ.get("LOOPWRIGHT_HOME", "") or Path.home() / ".loopwright")
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_dotenv(path: Path | None = None) -> None:
    """Read KEY=VALUE lines from .env into the environment (never overriding
    what is already set). Stdlib only, so nothing runs at import time."""
    path = path or Path.cwd() / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Options:
    # ---- model
    provider: str = "auto"          # auto | scripted | claude-code | anthropic | openai
    model: str = ""                 # empty = the provider's default
    # ---- orchestration
    pattern: str = "react"          # react | plan | reflect
    max_iterations: int = 8         # loop stop condition 2
    max_tool_calls: int = 16        # budget: tool calls per turn
    token_budget: int = 60_000      # budget: model tokens per turn
    # ---- memory
    memory: bool = True             # long-term memory on/off (ablation)
    gate: str = "rules"             # rules | model | always | never
    consolidate_every: int = 3      # background write after N user messages
    # ---- context
    context_budget: int = 8_000     # tokens the prompt may use before compaction
    compact_at: float = 0.6         # compact history past this share of the budget
    # ---- safety
    guardrails: bool = True         # input/tool-result/output guards (ablation)
    approvals: str = "ask"          # ask | approve | deny  (human in the loop)
    # ---- the outside world
    search: str = "auto"            # auto | tavily | brave | claude-code | duckduckgo | demo
    # ---- reliability
    retries: int = 2                # retries per model call before falling back
    fallback: bool = True           # try the next model when retries run out
    chaos_model_failures: int = 0   # fail the first N model calls (failure lab)
    chaos_tool_failures: int = 0    # fail the first N tool calls (failure lab)
    chaos_seed: int = 0             # shifts which calls fail
    fast: bool = False              # skip backoff sleeps (tests and evals)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> Options:
        """Build from untrusted JSON: unknown keys are dropped, types coerced."""
        opts = cls()
        for f in fields(cls):
            if not raw or f.name not in raw or raw[f.name] is None:
                continue
            value, default = raw[f.name], getattr(opts, f.name)
            try:
                if isinstance(default, bool):
                    value = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
                elif isinstance(default, int):
                    value = int(value)
                elif isinstance(default, float):
                    value = float(value)
                else:
                    value = str(value)
            except (TypeError, ValueError):
                continue
            setattr(opts, f.name, value)
        opts.max_iterations = max(1, min(opts.max_iterations, 30))
        opts.max_tool_calls = max(1, min(opts.max_tool_calls, 60))
        opts.retries = max(0, min(opts.retries, 5))
        return opts

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def env_options() -> Options:
    """Defaults, overridden by LOOPWRIGHT_<FIELD> environment variables."""
    raw = {f.name: os.environ.get(f"LOOPWRIGHT_{f.name.upper()}") for f in fields(Options)}
    return Options.from_dict(raw)
