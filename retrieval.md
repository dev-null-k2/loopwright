# Retrieval: the stand-in, a real embedding model, and a reranker

Retrieval here has three stages. The first two always run; the third is optional.

1. **BM25** scores every passage by the question's exact words.
2. **Dense** search compares a vector for the question with a vector for every passage.
3. **Reciprocal rank fusion** merges the two rankings.
4. **A reranker** (off by default) reads the best dozen candidates together with the question and re-orders them.

The same code searches long-term memory and the handbook. All of it is in
[`memory/retrieval.py`](../loopwright/memory/retrieval.py), [`memory/embedders.py`](../loopwright/memory/embedders.py)
and [`memory/rerank.py`](../loopwright/memory/rerank.py).

## The embedder

Out of the box the vectors come from a stand-in: `embed()` hashes words and character trigrams into 384 numbers. It
needs no model and gives the same vector on every machine, which is what keeps the evals reproducible. It measures
spelling overlap. It does not know that "booking" and "schedule" are related.

A real embedding model takes its place with one option:

```bash
ollama pull nomic-embed-text
loopwright serve                                   # then choose "auto" under Lab > Embedding model
loopwright ask "..." --set embedder=auto           # the first embedding model on your local server
loopwright ask "..." --set embedder=ollama:nomic-embed-text
LOOPWRIGHT_EMBEDDER=openai:text-embedding-3-small loopwright serve      # needs OPENAI_API_KEY
```

| Setting | Talks to | Notes |
|---|---|---|
| `hash` | nothing | the default |
| `auto` | the first embedding model on a running local server, else `hash` | `loopwright doctor` shows what it found |
| `ollama:<model>` | `POST /api/embed` on Ollama | `LOOPWRIGHT_EMBED_URL` if it is not on this machine |
| `openai:<model>` | `POST /v1/embeddings` on any OpenAI-compatible server | OpenAI, LM Studio, llama.cpp, vLLM; `LOOPWRIGHT_EMBED_URL`, `LOOPWRIGHT_EMBED_KEY` |

Four things come with a real model. Each is a few lines in `embedders.py`.

**Batches and a cache.** Passages are sent 32 at a time, and every vector is kept in `~/.loopwright/embeddings.db`,
keyed by model and text. The handbook is embedded once; after that a search costs one request, for the question.
Change the model and everything is embedded again under the new name: vectors from two models cannot be compared.

**The question and the passage are embedded differently.** Many models were trained with a short instruction in
front of the text, a different one for each side. Leave it out and nothing fails; results are quietly worse.

| Model family | Before a question | Before a passage |
|---|---|---|
| `nomic-embed-text`, `modernbert-embed` | `search_query: ` | `search_document: ` |
| `embeddinggemma` | `task: search result \| query: ` | `title: none \| text: ` |
| `mxbai-embed-large`, `bge-*-en` | `Represent this sentence for searching relevant passages: ` | none |
| `qwen3-embedding` | a one-line instruction, then `Query: ` | none |
| `e5-*` | `query: ` | `passage: ` |
| anything else | none | none |

**The similarity floor is measured.** Dense search always returns its nearest neighbours, relevant or not, so a hit
has to clear a floor. With the stand-in that floor is 0.18, found by hand. It means nothing for another model:
unrelated sentences score near 0 with one and near 0.5 with another. So for a real model, sixteen sentences that
have nothing to do with each other are embedded once and the floor is set at their mean similarity plus two
standard deviations. `LOOPWRIGHT_EMBED_FLOOR` overrides it.

**The sentence, not the keywords.** The retrieval gate shortens a message to keywords, which suits BM25. A real
model was trained on sentences, so the dense side is given the sentence the user wrote.

**It can be down.** If the model cannot be reached, the search is done with the stand-in instead and an
`embed.fallback` step says why. The handbook index tries the model again after a minute.

## The reranker

```bash
loopwright ask "What is a retrieval gate?" --set rerank=model --provider claude-code
LOOPWRIGHT_RERANK_URL=http://127.0.0.1:8080/v1/rerank loopwright ask "..." --set rerank=endpoint
```

| Setting | What reads the candidates | Cost |
|---|---|---|
| `off` | nothing | the default |
| `model` | the chat model, which returns the candidates' numbers in order | one model call per handbook search |
| `endpoint` | a cross-encoder behind `POST /v1/rerank` (the request Cohere, Jina, Together and llama.cpp share) | one request per search; `LOOPWRIGHT_RERANK_MODEL`, `LOOPWRIGHT_RERANK_KEY` |

With a reranker on, the first stage fetches twelve candidates and the reranker keeps the best three. It applies to
the handbook search (`search_knowledge`). Memory search is left alone: it runs on every turn over a handful of
facts, and a model call there would cost more than it returns.

Two things to remember. A reranker only re-orders what the first stage found, so it improves the top of the list
and never brings back a passage that was not among the candidates. And it fails open: an error, an unreadable
answer, or the scripted model (which cannot judge relevance) leaves the first-stage order in place and the
`rerank.run` step says so.

## Measuring it

```bash
loopwright eval --retrieval                                             # the stand-in
loopwright eval --retrieval --embedder hash --embedder auto             # side by side with your local model
loopwright eval --retrieval --embedder ollama:mxbai-embed-large --rerank model --provider local
```

Two question sets are scored, and the difference between them matters more than either number.

- **The first set** (44 questions, [`retrieval.jsonl`](../loopwright/evals/datasets/retrieval.jsonl)) was written
  alongside the handbook and mostly uses the cards' own words: "what is a retrieval gate". Keyword search does
  well on it almost by construction.
- **The held-out set** (60 questions,
  [`retrieval-heldout.jsonl`](../loopwright/evals/datasets/retrieval-heldout.jsonl)) asks for the same cards in
  other words: "decide whether to look anything up before answering". It was written in one sitting before any
  result on it had been seen, and nothing has been tuned against it since.

| Embedder | Ranker | First set, recall@1 / @3 | Held-out, recall@1 / @3 |
|---|---|---|---|
| none | BM25 | 84% / 100% | 60% / 75% |
| hash (the stand-in) | dense | 73% / 93% | 50% / 62% |
| | hybrid | 84% / 98% | 60% / 73% |
| `all-minilm` (23M parameters) | dense | 75% / 95% | 52% / 72% |
| | hybrid | 86% / 100% | 65% / 82% |
| `nomic-embed-text` | dense | 86% / 93% | 60% / 77% |
| | hybrid | 89% / 98% | 68% / 83% |
| `mxbai-embed-large` | dense | 86% / 93% | 68% / 92% |
| | hybrid | 91% / 100% | 75% / 88% |
| `nomic-embed-text` | hybrid, then `qwen2.5:3b` reranks twelve candidates | 57% / 91% | 72% / 93% |

What the numbers say:

- **The stand-in is what it claims to be.** On questions in the handbook's vocabulary it finds the right card in
  the top three almost every time; asked in other words, one question in four misses.
- **A real embedding model closes part of that gap, and how much depends on the model.** On the held-out set
  hybrid recall@3 goes from 73% to 82%, 83% and 88%. The smallest model is barely better than the stand-in on its
  own (dense 72% against 62%) and still lifts the hybrid by nine points.
- **Hybrid is not always the best of both.** With `mxbai-embed-large`, dense search alone finds 92% of held-out
  answers in the top three, and fusing it with BM25 brings that down to 88%: on paraphrases the keyword ranker is
  the weak partner, and equal-weight fusion lets it pull good results down. On the first set the same fusion helps.
  Which ranker to trust depends on how your users phrase things, which is a reason to measure on your own questions.
- **A small chat model is an erratic reranker.** It lifted the held-out set to 93% at recall@3, which it can do
  because it chooses from twelve candidates where the first stage keeps three, and it made the top result *worse* on the easy set
  (57% from 89%): it re-orders lists that were already right. A cross-encoder built for the job, or a larger
  model, is the usual answer; neither has been run here.

## What has and has not been run

- The stand-in rows are from `loopwright eval`, on every push.
- The real-model rows are from one run of the "Model scores" workflow on a GitHub-hosted runner (4 cores, no GPU,
  Ollama, Loopwright 0.11.1): [`results/retrieval.json`](../results/retrieval.json) and
  [`results/retrieval-rerank.json`](../results/retrieval-rerank.json). Embedding is deterministic, so those rows
  should reproduce; the reranked row depends on a chat model and will move from run to run. No search fell back
  to the stand-in during the run.
- The embedders and both rerankers are also tested on every push, over real HTTP against a stand-in server that
  speaks Ollama's `/api/embed`, the OpenAI-style `/v1/embeddings` and `/v1/rerank`: batching, prefixes, the cache,
  the measured floor, the fallback, a full turn, and the eval.
- Not run: the OpenAI-style embeddings path against a real service, the `endpoint` reranker against a real
  cross-encoder, and any embedding model larger than these three.
- The floor's "mean plus two standard deviations" is a reasoned default, not a tuned one. Dense recall with the
  three models above does not look cut off by it, which is some evidence and not a calibration. Set
  `LOOPWRIGHT_EMBED_FLOOR` if it does with yours.
