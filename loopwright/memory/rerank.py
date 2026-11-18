"""RERANKING: a second, slower look at the few candidates the search found.

First-stage retrieval (BM25, vectors) scores every document against the
query separately and cheaply, which is what lets it cover a whole corpus. A
reranker reads the query and one candidate *together* and judges the match,
which is far more accurate and far too slow to run on everything. So the two
are stacked: retrieve a dozen, rerank them, keep three.

    off        no second stage (the default)
    endpoint   a cross-encoder behind the /v1/rerank API that Cohere, Jina,
               Together, llama.cpp and others share: send the query and the
               documents, get a relevance score for each
    model      the chat model itself puts the candidates in order (a "listwise"
               rerank). No extra service to run, but a model call per search

Two properties are worth remembering. A reranker can only re-order what the
first stage found: if the right passage is not among the candidates, nothing
here can bring it back, so it raises precision at the top and never recall
of the candidate set. And it fails open: any error, an unreadable answer or a
missing model leaves the first-stage order untouched and says so.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from typing import Any

from loopwright.memory.retrieval import Hit
from loopwright.models._http import post_json
from loopwright.models.base import ModelError

CANDIDATES = 12             # how many first-stage results the reranker reads
SNIPPET = 320               # characters of each candidate shown to a model

PROMPT = """Rank these passages by how well each one answers the question.

Question: {query}

{passages}

Reply with only a JSON array of the passage numbers, best first, for example [3, 1, 2]. Include every number once."""


def passage_text(hit: Hit) -> str:
    """What the reranker reads for one candidate: its title, then its text."""
    title = hit.meta.get("term") or hit.meta.get("subject") or ""
    return f"{title}. {hit.text}" if title else hit.text


def by_endpoint(query: str, hits: list[Hit], transport: Callable[..., dict[str, Any]] = post_json) -> list[int]:
    """Ask a cross-encoder for a score per candidate. -> candidate positions, best first."""
    url = os.environ.get("LOOPWRIGHT_RERANK_URL", "").rstrip("/")
    if not url:
        raise ValueError("set LOOPWRIGHT_RERANK_URL to a /v1/rerank endpoint (and LOOPWRIGHT_RERANK_MODEL)")
    if not url.endswith("/rerank"):
        url += "/rerank"
    body: dict[str, Any] = {"query": query, "documents": [passage_text(h) for h in hits], "top_n": len(hits)}
    if os.environ.get("LOOPWRIGHT_RERANK_MODEL"):
        body["model"] = os.environ["LOOPWRIGHT_RERANK_MODEL"]
    key = os.environ.get("LOOPWRIGHT_RERANK_KEY", "")
    data = transport(url, {"authorization": f"Bearer {key}"} if key else {}, body, 60)
    rows = data.get("results") or data.get("data") or []
    scored = [(float(row["relevance_score"]), int(row["index"])) for row in rows
              if isinstance(row, dict) and "index" in row and "relevance_score" in row]
    # some servers return the rows in the order they were sent, so sort here
    order = [index for _, index in sorted(scored, key=lambda pair: -pair[0]) if 0 <= index < len(hits)]
    if not order:
        raise ValueError("the rerank endpoint returned no scores")
    return order


def by_model(query: str, hits: list[Hit], small: Callable[[str], str]) -> list[int]:
    """Ask the chat model to order the candidates. -> candidate positions, best first."""
    passages = "\n".join(f"[{i}] {passage_text(h)[:SNIPPET]}" for i, h in enumerate(hits, 1))
    reply = small(PROMPT.format(query=query, passages=passages))
    found = re.search(r"\[[\d,\s]*\]", reply)
    numbers = json.loads(found.group(0)) if found else [int(n) for n in re.findall(r"\d+", reply)]
    order = list(dict.fromkeys(n - 1 for n in numbers if isinstance(n, int) and 1 <= n <= len(hits)))
    if not order:
        raise ValueError(f"the model's ranking could not be read: {reply[:80]!r}")
    return order


def rerank(query: str, hits: list[Hit], k: int, how: str = "off", *, small: Callable[[str], str] | None = None,
           transport: Callable[..., dict[str, Any]] = post_json) -> tuple[list[Hit], dict[str, Any]]:
    """-> (the best `k` hits, a report). `hits` are first-stage results, best
    first; pass more than `k` of them so there is something to re-order."""
    report: dict[str, Any] = {"how": how, "candidates": len(hits), "kept": min(k, len(hits)), "moved": 0, "note": "", "ms": 0}
    if how == "off" or len(hits) < 2:
        report["note"] = "reranking is off" if how == "off" else "fewer than two candidates"
        return hits[:k], report
    began = time.perf_counter()
    try:
        if how == "endpoint":
            order = by_endpoint(query, hits, transport)
        elif how == "model":
            if small is None:
                raise ValueError("reranking with the model needs a real model; the scripted one cannot judge relevance")
            order = by_model(query, hits, small)
        else:
            raise ValueError(f"unknown rerank setting {how!r}: use off, endpoint or model")
    except (ModelError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        report.update(note=f"kept the first-stage order: {exc}", failed=True, ms=int((time.perf_counter() - began) * 1000))
        return hits[:k], report
    order += [i for i in range(len(hits)) if i not in order]        # anything the reranker left out keeps its place at the end
    ranked = []
    for position in order:
        hit = hits[position]
        hit.first_rank = position + 1
        ranked.append(hit)
    report.update(ms=int((time.perf_counter() - began) * 1000),
                  moved=sum(1 for now, hit in enumerate(ranked[:k], 1) if hit.first_rank != now),
                  before=[h.id for h in hits[:k]], after=[h.id for h in ranked[:k]])
    return ranked[:k], report
