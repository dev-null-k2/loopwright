"""EMBEDDERS: the stand-in, and how a real embedding model takes its place.

An embedder turns text into a vector. Retrieval (retrieval.py) does not care
which one it has: it asks for vectors for the documents once, a vector for
each query, and compares them.

    hash                      the built-in stand-in. Hashes words and trigrams.
                              No model, no network, the same vector on every
                              machine. It measures spelling overlap, not meaning.
    ollama:<model>            a model served by Ollama   (POST /api/embed)
    openai:<model>            any OpenAI-compatible API  (POST /v1/embeddings):
                              OpenAI itself, LM Studio, llama.cpp, vLLM, and
                              Ollama too
    auto                      the first embedding model found on a local model
                              server, else hash

Choose one per turn with the `embedder` option (LOOPWRIGHT_EMBEDDER, the Lab,
or `--set embedder=ollama:nomic-embed-text`).

Four things a real model brings that the stand-in hides, each handled here:

BATCHES AND A CACHE. Embedding costs a model call, so documents are sent in
batches and every vector is kept in a small SQLite file keyed by model and
text. A passage is embedded once, ever; a search then costs one call, for the
query.

QUERY AND DOCUMENT ARE EMBEDDED DIFFERENTLY. Many models were trained with a
short instruction in front of the text, a different one for a question than
for a passage. Leave it out and nothing fails: results are just quietly
worse. PREFIXES holds the instructions for the common families.

A SIMILARITY SCORE MEANS NOTHING ACROSS MODELS. Two unrelated sentences score
about 0 with one model and 0.5 with another, so a fixed "must be above 0.18"
is wrong the moment the model changes. The floor is measured instead: a few
sentences that have nothing to do with each other are embedded, and the floor
is set above how similar this model thinks unrelated text is.

IT CAN BE DOWN. A search must not fail because a model server is off. On any
error the index falls back to the hash embedder for that search and says so
with an event. Vectors from two embedders are never mixed.
"""

from __future__ import annotations

import array
import hashlib
import math
import os
import sqlite3
import statistics
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loopwright.config import home_dir
from loopwright.memory.retrieval import embed as hash_embed
from loopwright.models._http import post_json
from loopwright.models.base import ModelError

Vector = list[float]
BATCH = 32                      # documents per request
HASH_FLOOR, HASH_FLOOR_STRICT = 0.18, 0.30     # tuned by hand for the hash embedder

# (text in the model's name, what goes before a query, what goes before a document)
PREFIXES: list[tuple[str, str, str]] = [
    ("nomic-embed", "search_query: ", "search_document: "),
    ("modernbert-embed", "search_query: ", "search_document: "),
    ("embeddinggemma", "task: search result | query: ", "title: none | text: "),
    ("mxbai-embed", "Represent this sentence for searching relevant passages: ", ""),
    ("bge-small-en", "Represent this sentence for searching relevant passages: ", ""),
    ("bge-base-en", "Represent this sentence for searching relevant passages: ", ""),
    ("bge-large-en", "Represent this sentence for searching relevant passages: ", ""),
    ("qwen3-embedding", "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: ", ""),
    ("e5-", "query: ", "passage: "),
]

# Sentences with nothing in common. How alike a model thinks these are is its background level.
UNRELATED = [
    "The ferry to the island leaves at seven in the morning.",
    "Knead the dough for ten minutes, then let it rise.",
    "Interest on the loan is charged every month.",
    "The goalkeeper saved a penalty in the last minute.",
    "Mount Everest is the highest mountain above sea level.",
    "She tuned the violin before the concert began.",
    "Replace the bicycle chain when it starts to slip.",
    "The museum is closed on public holidays.",
    "Frogs lay their eggs in still water.",
    "The printer is out of yellow ink again.",
    "He planted tomatoes along the south wall.",
    "A triangle has three sides and three angles.",
    "The flight was delayed by a snowstorm.",
    "Brush your teeth twice a day.",
    "The court will hear the appeal next week.",
    "Volcanic rock cools quickly into glass.",
]


class EmbedError(Exception):
    """The embedding model could not be reached or answered with something unusable."""


def prefixes_for(model: str) -> tuple[str, str]:
    """-> (before a query, before a document) for this model family; empty when none is known."""
    low = model.lower()
    for marker, query, document in PREFIXES:
        if marker in low:
            return query, document
    return "", ""


def unit(vector: Vector) -> Vector:
    """Scaled to length 1, so a dot product between two of them is their cosine."""
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


class HashEmbedder:
    """The offline stand-in (`embed` in retrieval.py). Instant, deterministic, and blind to meaning."""
    name, real = "hash", False

    def documents(self, texts: list[str]) -> list[Vector]:
        return [hash_embed(t) for t in texts]

    def query(self, text: str) -> Vector:
        return hash_embed(text)

    def floor(self, strict: bool = False) -> float:
        return HASH_FLOOR_STRICT if strict else HASH_FLOOR

    def take_stats(self) -> dict[str, Any]:
        return {}


class FunctionEmbedder(HashEmbedder):
    """Wraps a plain `text -> vector` function, for anyone who passes their own to Index."""
    name = "custom"

    def __init__(self, function: Callable[[str], Vector]):
        self._function = function

    def documents(self, texts: list[str]) -> list[Vector]:
        return [self._function(t) for t in texts]

    def query(self, text: str) -> Vector:
        return self._function(text)


class HttpEmbedder:
    """A real embedding model behind an HTTP API, with a cache on disk."""
    real = True

    def __init__(self, kind: str, model: str, url: str = "", key: str = "", home: Path | None = None,
                 transport: Callable[..., dict[str, Any]] = post_json):
        if kind not in ("ollama", "openai"):
            raise ValueError(f"unknown embedder kind {kind!r}: use ollama:<model> or openai:<model>")
        if not model:
            raise ValueError("name the embedding model, for example ollama:nomic-embed-text")
        self.kind, self.model, self.name = kind, model, f"{kind}:{model}"
        self.url = (url or os.environ.get("LOOPWRIGHT_EMBED_URL") or _default_url(kind)).rstrip("/")
        self.key = key or os.environ.get("LOOPWRIGHT_EMBED_KEY") or os.environ.get("OPENAI_API_KEY") or "local"
        self.query_prefix, self.document_prefix = prefixes_for(model)
        self._post = transport
        self._path = Path(home or home_dir()) / "embeddings.db"
        self._lock = threading.Lock()
        self._floors: dict[bool, float] = {}
        self._stats = {"calls": 0, "embedded": 0, "cached": 0, "ms": 0}

    # ---------------------------------------------------------------- the two things retrieval asks for
    def documents(self, texts: list[str]) -> list[Vector]:
        return self._vectors([self.document_prefix + t for t in texts])

    def query(self, text: str) -> Vector:
        return self._vectors([self.query_prefix + text])[0]

    def floor(self, strict: bool = False) -> float:
        """The similarity a hit must beat to count. Measured, not assumed: this
        model's mean similarity between unrelated sentences, plus two standard
        deviations (three when `strict`). LOOPWRIGHT_EMBED_FLOOR overrides it."""
        fixed = os.environ.get("LOOPWRIGHT_EMBED_FLOOR", "")
        if fixed:
            try:
                return float(fixed)
            except ValueError:
                pass
        if strict not in self._floors:
            vectors = self.documents(UNRELATED)
            pairs = [sum(a * b for a, b in zip(vectors[i], vectors[j], strict=True))
                     for i in range(len(vectors)) for j in range(i + 1, len(vectors))]
            spread = statistics.pstdev(pairs)
            self._floors[False] = statistics.fmean(pairs) + 2 * spread
            self._floors[True] = statistics.fmean(pairs) + 3 * spread
        return self._floors[strict]

    def take_stats(self) -> dict[str, Any]:
        """What happened since the last call to this: for the event a search emits."""
        with self._lock:
            stats, self._stats = self._stats, {"calls": 0, "embedded": 0, "cached": 0, "ms": 0}
        return stats

    # ---------------------------------------------------------------- cache, then the model
    def _vectors(self, texts: list[str]) -> list[Vector]:
        keys = [hashlib.sha256(t.encode()).hexdigest() for t in texts]
        with self._lock:
            found = self._cached(keys)
            missing = [i for i, key in enumerate(keys) if key not in found]
            self._stats["cached"] += len(texts) - len(missing)
            # the same text twice in one request is embedded once
            todo = list(dict.fromkeys(keys[i] for i in missing))
            by_key = {keys[i]: texts[i] for i in missing}
            for start in range(0, len(todo), BATCH):
                batch = todo[start:start + BATCH]
                began = time.perf_counter()
                vectors = [unit(v) for v in self._call([by_key[k] for k in batch])]
                self._stats["calls"] += 1
                self._stats["embedded"] += len(batch)
                self._stats["ms"] += int((time.perf_counter() - began) * 1000)
                self._store(dict(zip(batch, vectors, strict=True)))
                found.update(zip(batch, vectors, strict=True))
        return [found[key] for key in keys]

    def _call(self, texts: list[str]) -> list[Vector]:
        try:
            if self.kind == "ollama":
                data = self._post(f"{self.url}/api/embed", {}, {"model": self.model, "input": texts, "truncate": True}, 120)
                vectors = data.get("embeddings")
            else:
                data = self._post(f"{self.url}/embeddings", {"authorization": f"Bearer {self.key}"},
                                  {"model": self.model, "input": texts}, 120)
                rows = sorted(data.get("data") or [], key=lambda row: row.get("index", 0))
                vectors = [row.get("embedding") for row in rows]
        except ModelError as exc:
            raise EmbedError(f"{self.name} at {self.url}: {exc}") from exc
        if not isinstance(vectors, list) or len(vectors) != len(texts) or not all(isinstance(v, list) and v for v in vectors):
            raise EmbedError(f"{self.name} returned {len(vectors) if isinstance(vectors, list) else 'no'} vector(s) for {len(texts)} text(s)")
        if len({len(v) for v in vectors}) != 1:
            raise EmbedError(f"{self.name} returned vectors of different sizes")
        return vectors

    def _db(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self._path, timeout=10)
        db.execute("CREATE TABLE IF NOT EXISTS vectors (model TEXT, key TEXT, vector BLOB, PRIMARY KEY (model, key))")
        return db

    def _cached(self, keys: list[str]) -> dict[str, Vector]:
        found: dict[str, Vector] = {}
        try:
            with self._db() as db:
                for start in range(0, len(keys), 500):
                    part = keys[start:start + 500]
                    rows = db.execute(f"SELECT key, vector FROM vectors WHERE model=? AND key IN ({','.join('?' * len(part))})",
                                      (self.name, *part))
                    for key, blob in rows:
                        found[key] = array.array("f", blob).tolist()
        except sqlite3.Error:
            pass                                    # a broken cache costs time, never a search
        return found

    def _store(self, vectors: dict[str, Vector]) -> None:
        try:
            with self._db() as db:
                db.executemany("INSERT OR REPLACE INTO vectors VALUES (?,?,?)",
                               [(self.name, key, array.array("f", vector).tobytes()) for key, vector in vectors.items()])
        except sqlite3.Error:
            pass


def _default_url(kind: str) -> str:
    if kind == "openai":
        return os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    from loopwright.models import local
    server = local.detect()
    return server["url"] if server else "http://127.0.0.1:11434"


def local_embedding_models() -> list[str]:
    """Embedding models on the local model server, if one is running."""
    from loopwright.models import local
    server = local.detect()
    return list(server.get("embedders", [])) if server else []


_made: dict[tuple[str, str], Any] = {}
_made_lock = threading.Lock()
HASH = HashEmbedder()


def get(spec: str = "hash", home: Path | None = None) -> Any:
    """The embedder a spec names (see the top of this file). One object per spec
    and state folder, so its cache and its measured floor are shared."""
    spec, url = (spec or "hash").strip(), ""
    if spec == "auto":
        from loopwright.models import local
        models, server = local_embedding_models(), local.detect()
        if not models or not server:
            return HASH
        # every local runner serves the OpenAI-style endpoint; Ollama also has its own
        native = server["name"] == "Ollama"
        spec, url = f"{'ollama' if native else 'openai'}:{models[0]}", server["url"] + ("" if native else "/v1")
    if spec == "hash":
        return HASH
    kind, _, model = spec.partition(":")
    with _made_lock:
        key = (spec, str(home or ""))
        if key not in _made:
            _made[key] = HttpEmbedder(kind, model, url=url, home=home)
        return _made[key]


def forget() -> None:
    """Drop the embedder objects (tests, and after settings change)."""
    with _made_lock:
        _made.clear()


def emit(rec: Any, report: dict[str, Any], stage: str, src: str = "tools") -> None:
    """Say on the board what the embedding model did for this search. The
    stand-in says nothing: it is instant and free, and a step for it would be noise."""
    if report.get("fallback"):
        rec.emit("embed.fallback", stage, "The embedding model could not be reached: the stand-in was used",
                 "Search must not fail because a model server is off. The whole index was searched with the "
                 "built-in hash embedder instead, because vectors from two different models cannot be compared. "
                 "Results are still returned; they match on spelling, not meaning, until the model is back.",
                 src=src, status="warn", data={"why": report["fallback"], "embedder": "hash"})
    elif report.get("embedder") not in (None, "hash", "custom"):
        rec.emit("embed.run", stage, f"Embedded by {report['embedder']}"
                 + (f": {report.get('embedded', 0)} new, {report.get('cached', 0)} from cache" if report.get("cached") else ""),
                 "A real embedding model turned the text into vectors, so passages that mean the same thing in "
                 "different words land close together. Every vector is kept on disk, keyed by model and text: a "
                 "passage is embedded once, and a search then costs one request, for the question.",
                 src=src, dur_ms=report.get("ms"), data={k: report.get(k) for k in ("embedder", "calls", "embedded", "cached", "ms")})
