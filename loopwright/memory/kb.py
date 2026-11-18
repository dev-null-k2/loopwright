"""THE KNOWLEDGE BASE: retrieval-augmented generation (RAG) over the handbook.

RAG in one sentence: instead of hoping the model memorised something, search
your own documents for the relevant passages and put them in the prompt, then
ask the model to answer from them and cite them.

The corpus here is the agent-design handbook itself: every glossary card in
loopwright/learn/concepts.toml plus the long-form notes in loopwright/kb/*.md.
So the agent can explain its own architecture, with citations you can check.

Chunking: a glossary card is one chunk; a Markdown file is split at its `##`
headings. The chunk boundary is the most underrated RAG decision: a chunk has
to be small enough to be about one thing and large enough to make sense alone.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from typing import Any

from loopwright.config import PACKAGE_DIR
from loopwright.learn import load_concepts
from loopwright.memory import embedders
from loopwright.memory import rerank as reranker
from loopwright.memory.retrieval import Hit, Index


def chunk_markdown(name: str, text: str) -> list[tuple[str, str, str]]:
    """Split at `## ` headings -> [(chunk_id, heading, body)]."""
    chunks = []
    for part in re.split(r"(?m)^(?=## )", text):
        part = part.strip()
        if not part.startswith("## "):
            continue
        heading, _, body = part[3:].partition("\n")
        if body.strip():
            slug = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")
            chunks.append((f"doc:{name}#{slug}", heading.strip(), body.strip()))
    return chunks


RETRY_AFTER_S = 60.0        # an index that fell back to the stand-in is rebuilt after this long
_indexes: dict[str, tuple[Index, float]] = {}
_lock = threading.Lock()


def knowledge_index(embedder: str = "hash") -> Index:
    """The handbook as a searchable index, built once per embedder. One that
    fell back to the stand-in is rebuilt after a minute, so a model server
    that comes back is used again."""
    with _lock:
        built = _indexes.get(embedder)
        if built and not (built[0].fallback and time.monotonic() - built[1] > RETRY_AFTER_S):
            return built[0]
        index = Index(embedders.get(embedder))
        for c in load_concepts():
            body = " ".join([c["one_liner"], c["explain"], c.get("why", ""), c.get("tradeoff", "")])
            index.add(f"concept:{c['id']}", body, {
                "kind": "concept", "term": c["term"], "one_liner": c["one_liner"], "explain": c["explain"],
                "why": c.get("why", ""), "tradeoff": c.get("tradeoff", ""), "file": c.get("file", ""),
                "pillar": c["pillar"], "cite": f"[handbook: {c['term']}]",
            }, boost=" ".join([c["term"], *c.get("aliases", [])]))
        for path in sorted((PACKAGE_DIR / "kb").glob("*.md")):
            for chunk_id, heading, body in chunk_markdown(path.stem, path.read_text(encoding="utf-8")):
                index.add(chunk_id, body, {"kind": "doc", "term": heading, "one_liner": "", "explain": body,
                                           "why": "", "tradeoff": "", "file": f"loopwright/kb/{path.name}",
                                           "pillar": "", "cite": f"[handbook: {heading}]"}, boost=heading)
        _indexes[embedder] = (index, time.monotonic())
        return index


def search(query: str, k: int = 3, mode: str = "hybrid", embedder: str = "hash", rerank: str = "off",
           small: Callable[[str], str] | None = None, report: dict[str, Any] | None = None) -> list[Hit]:
    """Search the handbook. With a reranker on, the first stage fetches more
    candidates than are wanted and the reranker picks the best `k` of them.
    `report` is filled with what the embedder and the reranker did."""
    report = {} if report is None else report
    wanted = max(k, reranker.CANDIDATES) if rerank != "off" else k
    hits = knowledge_index(embedder).search(query, k=wanted, mode=mode, report=report)
    hits, report["rerank"] = reranker.rerank(query, hits, k, rerank, small=small)
    return hits
