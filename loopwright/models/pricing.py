"""Cost estimates. Prices change, so none are hard-coded here.

Put your own in ~/.loopwright/prices.json (USD per million tokens):

    {"claude-sonnet-5-5": [3.0, 15.0], "my-local-model": [0, 0]}

A model with no entry shows its cost as "n/a" rather than a guess. The
scripted model is free, and Claude Code reports its own figure.
"""

from __future__ import annotations

import json
from functools import lru_cache

from loopwright.config import home_dir


@lru_cache(maxsize=1)
def table() -> dict[str, list[float]]:
    path = home_dir() / "prices.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def cost(model: str, tokens_in: int, tokens_out: int) -> float | None:
    for name, (price_in, price_out) in table().items():
        if model.startswith(name):
            return round((tokens_in * price_in + tokens_out * price_out) / 1_000_000, 6)
    return None
