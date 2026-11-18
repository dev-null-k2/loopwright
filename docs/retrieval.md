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

With the stand-in embedder:

| Ranker | First set, recall@1 / @3 | Held-out, recall@1 / @3 |
|---|---|---|
| BM25 | 84% / 100% | 60% / 75% |
| dense (hash) | 73% / 91% | 50% / 62% |
| hybrid | 84% / 98% | 60% / 73% |

Read that as: on questions in the handbook's vocabulary the right card is nearly always in the top three; asked
in other words, one question in four misses. That gap is what a real embedding model is for.

## What has and has not been run

- The stand-in numbers above are from `loopwright eval`, on every push.
- The embedders and both rerankers are tested over real HTTP against a stand-in server that speaks Ollama's
  `/api/embed`, the OpenAI-style `/v1/embeddings` and `/v1/rerank`: batching, prefixes, the cache, the measured
  floor, the fallback, a full turn, and the eval. The stand-in's "model" is the hash embedder plus a few
  synonyms, enough to tell a search that used it from one that did not.
- **No real embedding model and no real reranker has been scored yet.** The table has no row for one because none
  has been run. The command above produces that row on any machine with Ollama, and writes it to
  `results/retrieval.json`.
- The floor's "mean plus two standard deviations" is a reasoned default, not a tuned one. Check it against your
  model with `loopwright eval --retrieval` and set `LOOPWRIGHT_EMBED_FLOOR` if dense recall looks cut off.
