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
from functools import lru_cache

from loopwright.config import PACKAGE_DIR
from loopwright.learn import load_concepts
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


@lru_cache(maxsize=1)
def knowledge_index() -> Index:
    index = Index()
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
    return index


def search(query: str, k: int = 3, mode: str = "hybrid") -> list[Hit]:
    return knowledge_index().search(query, k=k, mode=mode)
