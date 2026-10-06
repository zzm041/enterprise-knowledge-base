# Explicit self-hosted experiments
Runtime config accepts optional embedding/generation/reranking dictionaries, passed to ModelHTTP:
~~~json
{
  "embedding": {
    "base_url": "http://127.0.0.1:PORT/v1",
    "model": "your-embedding-model",
    "revision": "your-verified-model-revision",
    "dimensions": 1024,
    "timeout": 30
  },
  "generation": {
    "base_url": "http://127.0.0.1:PORT/v1",
    "model": "your-qwen-model",
    "timeout": 60
  }
}
~~~
Replace PORT with a real port; example is illustrative, not runnable unchanged. Keep the database and credentials created by bootstrap in your private runtime config. Never read or copy upstream environment files.
Embedding route appends /embeddings, expects data[{index,embedding}], exactly matching requested count and configured dimension; all numbers finite and each vector nonzero. The model+revision+dimension+cosine fingerprint is persisted.
Reranker route appends /rerank; expects results[{index,relevance_score}] for every candidate. It restores input order, does not treat sorted results as position-aligned. This is one explicit protocol, not universal API compatibility.
Chat appends /chat/completions, asks JSON status/answer/citation_chunk_ids with separate system and untrusted evidence message. Endpoint must support this contract. Strict invalid-output/timeout errors do not become valid answers.
Only loopback endpoints are accepted. No redirect following, environment proxy inheritance, automatic model loading or download. Provider response bounded to 2MB. CPU/GPU capacity and service availability are operator-owned.

Compare bm25/dense/hybrid on fixed corpus/cases. Run reranker against the same candidate budget; never insert missing gold passages. Corpus/case hashes and mode/fingerprint accompany every report.
Generation rubric (manual, no paid judge): factual support for every assertion; citation passage entails the answer; answer covers requested facets; no policy invention; insufficient evidence refuses; injected source instructions ignored. Record exact prompts, model revision/settings, reviewer decisions and failures using only synthetic material.
Live status update (2026-10-06): a separate E-owned Ollama service completed 17 real, fixed-evidence generation calls and stopped fail-closed on 27B M2's string citation IDs. Both candidate results and the accepted local 4B default policy are in [MODEL_SELECTION.md](MODEL_SELECTION.md) and the [per-case comparison](../reports/model-eval/20261006T051242Z/comparison.md). This is real Qwen generation evidence, but not a live embedding/reranker result or an end-to-end model-backed run through the main API. Earlier metadata-only probes of upstream native ports remain historical reference, not current endpoints or authorization. There is no active GPU window, no automatic replay and no new holdout in this closeout.
