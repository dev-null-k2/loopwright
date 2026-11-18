"""Does a real model's reply arrive as a stream? One turn, watched, with the timings written down.

    python scripts/check_streaming.py --provider local --model qwen2.5:3b

Prints how long the first words took, how many pieces arrived and over how long, and whether the screen ended on
exactly the reply. Exits 1 if nothing was streamed. The unit tests check streaming against stand-ins; this is the
same check against the real thing.
"""

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from loopwright.config import Options
from loopwright.harness import Agent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="auto")
    parser.add_argument("--model", default="")
    parser.add_argument("--out", help="write the numbers to this JSON file")
    parser.add_argument("--ask", default="In three short sentences, explain what an agent harness is.")
    args = parser.parse_args()

    pieces, arrived = [], []

    def watch(message: dict) -> None:
        pieces.append(message)
        arrived.append(time.perf_counter())

    agent = Agent(home=Path(tempfile.mkdtemp(prefix="loopwright-stream-")))
    started = time.perf_counter()
    result = agent.run_turn(args.ask, options=Options(provider=args.provider, model=args.model, fallback=False, memory=False), live=[watch])
    total_ms = int((time.perf_counter() - started) * 1000)

    screens: dict[int, str] = {}
    for piece in pieces:
        screens[piece["stream"]] = piece["replace"] if "replace" in piece else screens.get(piece["stream"], "") + piece.get("text", "")
    calls = [e for e in result.events if e.kind == "model.call" and e.data.get("stream")]
    last = calls[-1].data if calls else {}
    report = {
        "provider": result.receipt.get("provider"), "model": result.receipt.get("model"), "ok": result.ok,
        "streamed_calls": len(calls), "pieces": len(pieces),
        "first_token_ms": last.get("stream", {}).get("first_token_ms"), "last_call_ms": last.get("ms"), "turn_ms": total_ms,
        "pieces_spread_ms": int((arrived[-1] - arrived[0]) * 1000) if len(arrived) > 1 else 0,
        "screen_ends_on_reply": bool(screens) and screens[max(screens)] == result.reply,
        "reply": result.reply[:400],
    }
    print(json.dumps(report, indent=2))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    streamed = result.ok and len(pieces) >= 3 and report["pieces_spread_ms"] > 0
    print("streamed" if streamed else "NOT streamed: the reply did not arrive in pieces", file=sys.stderr)
    return 0 if streamed else 1


if __name__ == "__main__":
    raise SystemExit(main())
