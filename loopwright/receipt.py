"""THE RECEIPT: what one turn did and what it cost, in numbers.

You cannot manage what you do not measure. Every turn ends with a receipt
built from the turn's own records: model calls by purpose, tokens in and out,
tool calls, what memory was used and written, how long it took. The same
numbers feed the evals (tokens per task is a metric) and would feed a
dashboard or a budget alert in production.

Token counts are the provider's when it reports them and an estimate
(characters / 4) otherwise; `estimated` says which.
"""

from __future__ import annotations

from typing import Any


def build(calls: list[dict[str, Any]], tool_log: list[dict[str, Any]], *, memory_used: int,
          memory_written: int, duration_ms: int, iterations: int, stop: str) -> dict[str, Any]:
    by_purpose: dict[str, dict[str, int]] = {}
    for c in calls:
        row = by_purpose.setdefault(c["purpose"], {"calls": 0, "tokens_in": 0, "tokens_out": 0})
        row["calls"] += 1
        row["tokens_in"] += c["tokens_in"]
        row["tokens_out"] += c["tokens_out"]
    costs = [c["cost_usd"] for c in calls]
    last = calls[-1] if calls else {}
    receipt = {
        "provider": last.get("provider", ""), "model": last.get("model", ""),
        "model_calls": len(calls), "by_purpose": by_purpose,
        "tokens_in": sum(c["tokens_in"] for c in calls), "tokens_out": sum(c["tokens_out"] for c in calls),
        "cached_in": sum(c.get("cached_in", 0) for c in calls),
        "estimated": any(c.get("estimated") for c in calls),
        "cost_usd": None if any(c is None for c in costs) or not costs else round(sum(costs), 6),
        "tool_calls": len(tool_log), "tools_failed": sum(1 for t in tool_log if not t["ok"] and not t["blocked"]),
        "tools_blocked": sum(1 for t in tool_log if t["blocked"]),
        "tools": [t["name"] for t in tool_log],
        "memory_used": memory_used, "memory_written": memory_written,
        "iterations": iterations, "stop": stop, "duration_ms": duration_ms,
    }
    receipt["line"] = line(receipt)
    return receipt


def line(r: dict[str, Any]) -> str:
    cost = "n/a" if r["cost_usd"] is None else f"${r['cost_usd']:.4f}"
    tokens = f"{'~' if r['estimated'] else ''}{r['tokens_in']:,} in / {r['tokens_out']:,} out"
    return (f"{r['model'] or 'no model'} · {r['model_calls']} model call(s) · {tokens} · {cost} · "
            f"{r['tool_calls']} tool call(s) · memory {r['memory_used']} used, {r['memory_written']} written · "
            f"{r['duration_ms']} ms")
