"""The handbook: glossary cards and interview questions, loaded from TOML.

One source feeds four readers: the knowledge base the agent searches (RAG),
the Learn tab in the cockpit, the generated docs, and the tests that check
every card points at a real file and a real diagram box.
"""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent

PILLARS = {
    "harness": "Harness",
    "loop": "Loop and orchestration",
    "context": "Context engineering",
    "memory": "Memory and retrieval",
    "tools": "Tools and protocols",
    "safety": "Safety and guardrails",
    "reliability": "Reliability",
    "ops": "Observability and LLM-Ops",
    "evals": "Evals",
    "models": "Models and cost",
}


@lru_cache(maxsize=1)
def load_concepts() -> list[dict[str, Any]]:
    with (HERE / "concepts.toml").open("rb") as fh:
        return tomllib.load(fh)["concept"]


@lru_cache(maxsize=1)
def load_interview() -> list[dict[str, Any]]:
    with (HERE / "interview.toml").open("rb") as fh:
        return tomllib.load(fh)["scenario"]


@lru_cache(maxsize=1)
def load_lessons() -> list[dict[str, Any]]:
    with (HERE / "lessons.toml").open("rb") as fh:
        return tomllib.load(fh)["lesson"]


def concept_for_kind(kind: str) -> dict[str, Any] | None:
    """The card that explains an event kind (cards list the kinds they cover)."""
    for concept in load_concepts():
        if kind in concept.get("kinds", []):
            return concept
    return None
