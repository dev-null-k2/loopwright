"""A real embedding model and a reranker, spoken to over real HTTP through a stand-in server."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from loopwright.config import Options
from loopwright.evals.runner import run_retrieval
from loopwright.memory import embedders, kb, rerank
from loopwright.memory.retrieval import Hit, Index, embed
from loopwright.models import local

SAME = {"booking": "schedule", "reservation": "schedule", "appointment": "schedule", "automobile": "car"}     # what "meaning" is, to the stub


class Models(BaseHTTPRequestHandler):
    """An embedding model and a cross-encoder, as a local runner serves them.

    The embedder is the hash stand-in with one difference: it knows a few
    synonyms, so "booking" and "schedule" land on the same vector. That is
    enough to tell, in a test, a search that used this model from one that
    did not. The reranker likes any document that contains the word "gate"."""
    requests: list = []
    down = False
    shuffle = False

    def log_message(self, *args):
        pass

    def _send(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    @staticmethod
    def vector(text):
        for prefix in ("search_query: ", "search_document: "):
            text = text.removeprefix(prefix)
        return embed(" ".join(SAME.get(word.strip(".,?").lower(), word) for word in text.split()))

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        Models.requests.append({"path": self.path, "auth": self.headers.get("authorization"), **body})
        if Models.down:
            return self._send(503, {"error": {"message": "model is loading"}})
        if self.path == "/api/embed":
            return self._send(200, {"model": body["model"], "embeddings": [self.vector(t) for t in body["input"]]})
        if self.path == "/v1/embeddings":
            rows = [{"index": i, "embedding": self.vector(t)} for i, t in enumerate(body["input"])]
            return self._send(200, {"data": rows[::-1] if Models.shuffle else rows, "model": body["model"]})
        if self.path == "/v1/rerank":
            rows = [{"index": i, "relevance_score": 5.0 if "gate" in d.lower() else -1.0 - i} for i, d in enumerate(body["documents"])]
            return self._send(200, {"results": rows})                     # in the order sent, as llama.cpp does
        self._send(404, {"error": {"message": "no such route"}})


@pytest.fixture
def models(monkeypatch):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Models)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    monkeypatch.setenv("LOOPWRIGHT_EMBED_URL", url)
    Models.requests, Models.down, Models.shuffle = [], False, False
    embedders.forget()
    kb._indexes.clear()
    yield url
    httpd.shutdown()
    embedders.forget()
    kb._indexes.clear()


def embed_calls():
    return [r for r in Models.requests if "embed" in r["path"]]


# ---------------------------------------------------------------- the embedder

def test_documents_go_in_batches_with_the_models_prefixes_and_are_embedded_once(models, tmp_path):
    model = embedders.HttpEmbedder("ollama", "nomic-embed-text", home=tmp_path)
    texts = [f"passage number {i}" for i in range(70)]
    vectors = model.documents(texts)
    calls = embed_calls()
    assert [len(c["input"]) for c in calls] == [32, 32, 6] and all(c["path"] == "/api/embed" and c["truncate"] for c in calls)
    assert all(t.startswith("search_document: ") for c in calls for t in c["input"])
    assert len(vectors) == 70 and abs(sum(x * x for x in vectors[0]) - 1) < 1e-6          # unit length: a dot product is a cosine
    model.query("which passage?")
    assert embed_calls()[-1]["input"] == ["search_query: which passage?"]                 # the question gets the other prefix
    before = len(embed_calls())
    again = embedders.HttpEmbedder("ollama", "nomic-embed-text", home=tmp_path).documents(texts + ["passage number 3"])
    assert len(embed_calls()) == before and len(again) == 71                              # a new object, the same cache on disk
    assert max(abs(a - b) for a, b in zip(again[5], vectors[5], strict=True)) < 1e-6
    assert model.take_stats()["embedded"] == 71 and embedders.prefixes_for("mxbai-embed-large")[1] == ""


def test_an_openai_style_server_is_spoken_to_its_own_way(models, tmp_path, monkeypatch):
    monkeypatch.setenv("LOOPWRIGHT_EMBED_KEY", "secret-for-the-test")
    Models.shuffle = True                                                                  # rows may come back in any order
    model = embedders.HttpEmbedder("openai", "text-embedding-3-small", url=models + "/v1", home=tmp_path)
    first, second = model.documents(["the car is red", "retries need jitter"])
    call = embed_calls()[-1]
    assert call["path"] == "/v1/embeddings" and call["auth"] == "Bearer secret-for-the-test" and call["input"] == ["the car is red", "retries need jitter"]
    assert sum(a * b for a, b in zip(first, Models.vector("the car is red"), strict=True)) > 0.999
    assert sum(a * b for a, b in zip(second, Models.vector("retries need jitter"), strict=True)) > 0.999
    with pytest.raises(ValueError, match="ollama:<model> or openai:<model>"):
        embedders.get("cohere:embed-v4")


def test_a_real_model_finds_the_paraphrase_the_stand_in_misses(models, tmp_path):
    def build(embedder):
        index = Index(embedder)
        index.add("a", "Alex wants every schedule sent on Friday", boost="Alex")
        index.add("b", "The printer on the second floor is out of toner")
        return index
    question = "who asked about a booking"
    assert build(None).search(question, mode="dense") == []                                # no shared word, so the stand-in finds nothing
    report = {}
    hits = build(embedders.get("ollama:nomic-embed-text", home=tmp_path)).search(question, mode="dense", report=report)
    assert [h.id for h in hits] == ["a"] and report["embedder"] == "ollama:nomic-embed-text"
    assert report["calls"] >= 2 and not report["fallback"]
    assert embed_calls()[0]["input"][0] == "search_document: Alex. Alex wants every schedule sent on Friday"      # the title once, not three times


def test_the_similarity_floor_is_measured_for_the_model_not_assumed(models, tmp_path, monkeypatch):
    model = embedders.get("ollama:nomic-embed-text", home=tmp_path)
    vectors = [Models.vector(t) for t in embedders.UNRELATED]
    pairs = [sum(a * b for a, b in zip(vectors[i], vectors[j], strict=True)) for i in range(16) for j in range(i + 1, 16)]
    mean = sum(pairs) / len(pairs)
    spread = (sum((p - mean) ** 2 for p in pairs) / len(pairs)) ** 0.5
    assert abs(model.floor() - (mean + 2 * spread)) < 1e-4 and model.floor(strict=True) > model.floor()
    assert embedders.HASH.floor() == 0.18 and embedders.HASH.floor(strict=True) == 0.30
    monkeypatch.setenv("LOOPWRIGHT_EMBED_FLOOR", "0.42")
    assert model.floor() == 0.42


def test_when_the_model_is_down_search_still_answers_with_the_stand_in(models, tmp_path):
    Models.down = True
    index = Index(embedders.get("ollama:nomic-embed-text", home=tmp_path))
    index.add("a", "Alex prefers morning meetings", boost="Alex")
    report = {}
    hits = index.search("when does Alex like meetings", report=report)
    assert [h.id for h in hits] == ["a"] and report["embedder"] == "hash" and "HTTP 503" in report["fallback"]
    assert index._vectors[0] == embed("Alex Alex Alex prefers morning meetings")           # re-embedded the stand-in's way, nothing mixed

    Models.down = False                                                                    # up for the documents, down for the question
    index = Index(embedders.get("ollama:nomic-embed-text", home=tmp_path))
    index.add("a", "Alex prefers morning meetings", boost="Alex")
    index._embedded()
    Models.down = True
    report = {}
    assert [h.id for h in index.search("when does Alex like meetings", report=report)] == ["a"] and report["fallback"]


def test_the_knowledge_base_retries_the_model_after_a_fallback(models, monkeypatch):
    Models.down = True
    report = {}
    assert kb.search("what is a retrieval gate", embedder="ollama:nomic-embed-text", report=report)[0].id == "concept:retrieval-gate"
    assert report["fallback"]
    Models.down = False
    report = {}
    kb.search("what is a retrieval gate", embedder="ollama:nomic-embed-text", report=report)
    assert report["fallback"]                                                              # within the minute: still the stand-in
    monkeypatch.setattr(kb, "RETRY_AFTER_S", 0.0)
    report = {}
    kb.search("what is a retrieval gate", embedder="ollama:nomic-embed-text", report=report)
    assert not report["fallback"] and report["embedder"] == "ollama:nomic-embed-text" and report["embedded"] > 90


def test_auto_picks_an_embedding_model_from_the_local_server(models, tmp_path, monkeypatch):
    assert local.is_embedder("nomic-embed-text:latest") and local.is_embedder("bge-m3") and not local.is_embedder("qwen2.5:7b")
    monkeypatch.setattr(local, "detect", lambda refresh=False: None)
    assert embedders.get("auto") is embedders.HASH
    found = {"name": "LM Studio", "url": models, "models": ["qwen2.5:7b"], "embedders": ["nomic-embed-text"], "preferred": "", "why": ""}
    monkeypatch.setattr(local, "detect", lambda refresh=False: found)
    monkeypatch.delenv("LOOPWRIGHT_EMBED_URL")
    picked = embedders.get("auto", home=tmp_path)
    assert picked.name == "openai:nomic-embed-text" and picked.url == models + "/v1"
    found["name"] = "Ollama"
    assert embedders.get("auto", home=tmp_path).name == "ollama:nomic-embed-text"


# ---------------------------------------------------------------- the reranker

def hits_for(*texts):
    return [Hit(id=f"h{i}", text=t, score=1.0 / i, meta={"term": f"Card {i}"}) for i, t in enumerate(texts, 1)]


def test_a_cross_encoder_reorders_the_candidates_and_can_only_reorder(models, monkeypatch):
    monkeypatch.setenv("LOOPWRIGHT_RERANK_URL", models + "/v1")
    monkeypatch.setenv("LOOPWRIGHT_RERANK_MODEL", "bge-reranker-v2-m3")
    found = hits_for("about loops", "about budgets", "the retrieval gate decides", "about tracing")
    best, report = rerank.rerank("what decides whether to search", found, 2, "endpoint")
    call = Models.requests[-1]
    assert call["path"] == "/v1/rerank" and call["model"] == "bge-reranker-v2-m3" and call["documents"][2] == "Card 3. the retrieval gate decides"
    assert [h.id for h in best] == ["h3", "h1"] and best[0].first_rank == 3 and best[0].to_json()["first_rank"] == 3
    assert report["moved"] == 2 and report["before"] == ["h1", "h2"] and report["after"] == ["h3", "h1"] and not report.get("failed")
    assert {h.id for h in best} <= {h.id for h in found}                                   # nothing new: it re-orders what it was given


def test_the_chat_model_can_rerank_and_a_bad_answer_keeps_the_first_order():
    found = hits_for("about loops", "about budgets", "the retrieval gate decides")
    asked = []

    def model(prompt):
        asked.append(prompt)
        return "Here you go: [3, 1, 2]"

    best, report = rerank.rerank("gate?", found, 3, "model", small=model)
    assert [h.id for h in best] == ["h3", "h1", "h2"] and "[3] Card 3. the retrieval gate decides" in asked[0]
    best, report = rerank.rerank("gate?", hits_for("a", "b", "c"), 2, "model", small=lambda prompt: "2 is the most relevant")
    assert [h.id for h in best] == ["h2", "h1"]                                            # a partial answer: the rest keep their order
    for broken in (lambda prompt: "I cannot rank these.", lambda prompt: "[9, 12]", None):
        best, report = rerank.rerank("gate?", hits_for("a", "b", "c"), 2, "model", small=broken)
        assert [h.id for h in best] == ["h1", "h2"] and report["failed"] and report["note"].startswith("kept the first-stage order")
    best, report = rerank.rerank("gate?", hits_for("a", "b"), 2, "endpoint")               # no endpoint configured
    assert [h.id for h in best] == ["h1", "h2"] and "LOOPWRIGHT_RERANK_URL" in report["note"]
    assert rerank.rerank("gate?", hits_for("a", "b", "c"), 2, "off")[1]["note"] == "reranking is off"


# ---------------------------------------------------------------- through a whole turn, and through the eval

def test_a_turn_uses_the_embedding_model_and_the_reranker_and_says_so(models, tmp_path, monkeypatch, fake_agent):
    monkeypatch.setenv("LOOPWRIGHT_RERANK_URL", models + "/v1/rerank")
    agent, model = fake_agent([("search_knowledge", {"query": "re-order the best results"})], "done")
    agent.store.add_fact("Alex wants every schedule sent on Friday", "Alex")
    options = Options(fast=True, embedder="ollama:nomic-embed-text", rerank="endpoint", gate="always")
    result = agent.run_turn("who asked about a booking?", options=options)
    kinds = result.kinds()
    assert kinds.count("embed.run") == 2 and "embed.fallback" not in kinds                 # once for memory, once for the handbook
    memory = next(e for e in result.events if e.kind == "memory.search")
    assert [h["text"] for h in memory.data["hits"]] == ["Alex wants every schedule sent on Friday"]       # found by meaning: no word in common
    searched = next(e for e in result.events if e.kind == "kb.search")
    second = next(e for e in result.events if e.kind == "rerank.run")
    assert searched.data["embedder"] == "ollama:nomic-embed-text" and second.status == "ok" and second.data["candidates"] == 12
    assert kinds.index("embed.run", kinds.index("memory.search")) < kinds.index("rerank.run") < kinds.index("kb.search")
    passages = {doc_id: f"{meta['term']}. {text}" for doc_id, text, meta in kb.knowledge_index("ollama:nomic-embed-text").docs}
    assert "gate" in passages[searched.data["hits"][0]["id"]].lower() and second.data["after"][0] == searched.data["hits"][0]["id"]      # the stub's favourite came first


def test_the_retrieval_eval_scores_any_embedder_on_both_question_sets(models, capsys, tmp_path):
    scored = run_retrieval("ollama:nomic-embed-text", "model", small=lambda prompt: "[2, 1]")
    assert scored["embedder_used"] == "ollama:nomic-embed-text" and not scored["fallback"]
    assert scored["questions"] == 44 and scored["heldout"]["questions"] == 60
    for block in (scored, scored["heldout"]):
        assert all(0 <= block[m]["recall_at_3"] <= 1 for m in ("bm25", "dense", "hybrid", "reranked")) and block["reranked"]["failed"] == 0
    plain = run_retrieval()
    assert plain["bm25"] == scored["bm25"] and "reranked" not in plain                     # keyword search does not depend on the embedder
    assert plain["heldout"]["hybrid"]["recall_at_3"] < plain["hybrid"]["recall_at_3"]      # paraphrases are harder for the stand-in

    from loopwright.cli import main
    assert main(["eval", "--retrieval", "--embedder", "hash", "--embedder", "ollama:nomic-embed-text", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "hash" in out and "ollama:nomic-embed-text" in out and "held-out" in out
    saved = json.loads((tmp_path / "retrieval.json").read_text())
    assert [r["embedder"] for r in saved["rows"]] == ["hash", "ollama:nomic-embed-text"]
