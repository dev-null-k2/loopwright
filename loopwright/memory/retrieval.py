"""RETRIEVAL: how the agent finds the right few lines out of everything it knows.

Two rankers look at the same question from different sides, and a third step
merges them:

  BM25    exact words. Finds "BILL-4004" or "Alex" because the word is there.
          Blind to synonyms: "booking" does not match "schedule".
  Dense   meaning, as a vector. Finds near-matches and typos. Blind to rare
          exact tokens, and it always returns *something*, relevant or not.
  RRF     reciprocal rank fusion. Each ranker votes 1/(k + rank); the votes
          add up. No score calibration needed, which is why it is the default
          way to combine rankers that score on different scales.

The same three functions serve long-term memory and the knowledge base (RAG).

About the embedder: `embed()` hashes character trigrams and words into a
fixed-size vector. It is a stand-in that needs no model download and gives the
same vector on every machine, which keeps the evals reproducible. It captures
spelling overlap, not real meaning. Swap in a real embedding model by passing
your own `embed` function to `Index`; nothing else changes.

Everything here is plain Python over an in-memory list, which is honest up to
a few thousand items. Past that you would move BM25 to SQLite FTS5 or
OpenSearch and the vectors to pgvector or a vector database.
"""

from __future__ import annotations

import math
import re
import zlib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

DIMS = 384
RRF_K = 60

STOPWORDS = frozenset("""
a an and are as at be but by can could did do does for from had has have how i if in into is it
its me my of on or our please should so than that the their them then there these they this to
us was we were what when where which who why will with would you your about tell explain
""".split())

_WORD = re.compile(r"[a-z0-9][a-z0-9\-']*")


def stem(word: str) -> str:
    """A deliberately tiny stemmer: enough that 'meetings' meets 'meeting'."""
    for suffix in ("ing", "ies", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def tokenize(text: str, keep_stopwords: bool = False) -> list[str]:
    words = _WORD.findall(text.lower())
    return [stem(w) for w in words if keep_stopwords or w not in STOPWORDS]


def embed(text: str, dims: int = DIMS) -> list[float]:
    """Feature hashing: every trigram and word adds +1 or -1 to one of `dims`
    slots, then the vector is scaled to length 1 so a dot product is cosine."""
    vec = [0.0] * dims
    words = tokenize(text)                  # stopwords out, so "what is a" adds no signal
    features: Counter[str] = Counter()
    for word in words:
        features["w:" + word] += 2          # whole words count double
        padded = f"^{word}$"
        for i in range(len(padded) - 2):    # trigrams inside each word: typo and inflection tolerance
            features["c:" + padded[i:i + 3]] += 1
    for feature, count in features.items():
        h = zlib.crc32(feature.encode())
        vec[h % dims] += (1.0 if (h >> 16) & 1 else -1.0) * math.sqrt(count)
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


@dataclass
class Hit:
    id: str
    text: str
    score: float                  # the fused RRF score
    bm25_rank: int | None = None  # 1 = best; None = the ranker did not return it
    dense_rank: int | None = None
    bm25: float = 0.0
    cosine: float = 0.0
    coverage: float = 0.0         # share of the query's words that appear in this document
    meta: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "text": self.text, "score": round(self.score, 4),
                "bm25_rank": self.bm25_rank, "dense_rank": self.dense_rank,
                "bm25": round(self.bm25, 3), "cosine": round(self.cosine, 3),
                "coverage": round(self.coverage, 2), **self.meta}


class Index:
    """A small searchable collection: add documents, then search three ways."""

    def __init__(self, embedder: Callable[[str], list[float]] = embed):
        self._embed = embedder
        self.docs: list[tuple[str, str, dict[str, Any]]] = []   # (id, text, meta)
        self._tokens: list[list[str]] = []
        self._vectors: list[list[float]] = []
        self._df: Counter[str] = Counter()

    def add(self, doc_id: str, text: str, meta: dict[str, Any] | None = None,
            boost: str = "") -> None:
        """`boost` is text that should count extra (a title, a subject): it is
        indexed three times so a match on it outranks a match in the body."""
        indexed = f"{boost} {boost} {boost} {text}" if boost else text
        tokens = tokenize(indexed)
        self.docs.append((doc_id, text, meta or {}))
        self._tokens.append(tokens)
        self._vectors.append(self._embed(f"{boost} {boost} {text}" if boost else text))
        self._df.update(set(tokens))

    def __len__(self) -> int:
        return len(self.docs)

    # ---- ranker 1: BM25 (exact words, weighted by how rare they are)
    def bm25(self, query: str, k: int = 10, k1: float = 1.5, b: float = 0.75) -> list[tuple[int, float]]:
        terms = tokenize(query)
        if not terms or not self.docs:
            return []
        n = len(self.docs)
        avg_len = sum(len(t) for t in self._tokens) / n or 1.0
        scored = []
        for i, tokens in enumerate(self._tokens):
            tf = Counter(tokens)
            score = 0.0
            for term in terms:
                if term not in tf:
                    continue
                idf = math.log(1 + (n - self._df[term] + 0.5) / (self._df[term] + 0.5))
                score += idf * tf[term] * (k1 + 1) / (tf[term] + k1 * (1 - b + b * len(tokens) / avg_len))
            if score > 0:
                scored.append((i, score))
        return sorted(scored, key=lambda pair: -pair[1])[:k]

    # ---- ranker 2: dense (nearest vectors)
    def dense(self, query: str, k: int = 10, floor: float = 0.0) -> list[tuple[int, float]]:
        if not self.docs:
            return []
        q = self._embed(query)
        scored = [(i, cosine(q, v)) for i, v in enumerate(self._vectors)]
        return sorted((p for p in scored if p[1] > floor), key=lambda pair: -pair[1])[:k]

    # ---- fusion
    def search(self, query: str, k: int = 5, mode: str = "hybrid",
               min_cosine: float = 0.18) -> list[Hit]:
        """mode: 'bm25' | 'dense' | 'hybrid'. A hit must share a word with the
        query or clear `min_cosine`; without that floor, dense search hands
        back its nearest neighbours even when nothing is relevant."""
        lexical = self.bm25(query, k=max(k * 3, 10)) if mode in ("bm25", "hybrid") else []
        vectors = self.dense(query, k=max(k * 3, 10), floor=min_cosine) if mode in ("dense", "hybrid") else []
        hits: dict[int, Hit] = {}
        terms = set(tokenize(query))

        def hit(i: int) -> Hit:
            if i not in hits:
                doc_id, text, meta = self.docs[i]
                coverage = len(terms & set(self._tokens[i])) / len(terms) if terms else 0.0
                hits[i] = Hit(id=doc_id, text=text, score=0.0, coverage=coverage, meta=meta)
            return hits[i]

        for rank, (i, score) in enumerate(lexical, 1):
            h = hit(i)
            h.bm25_rank, h.bm25 = rank, score
            h.score += 1.0 / (RRF_K + rank)
        for rank, (i, score) in enumerate(vectors, 1):
            h = hit(i)
            h.dense_rank, h.cosine = rank, score
            h.score += 1.0 / (RRF_K + rank)
        return sorted(hits.values(), key=lambda h: -h.score)[:k]
