# Engineering notes for read-only reuse by Codex A/B

## 2026-10-06 local model operating choice after the stopped real run
Why/problem: this single 16 GB GPU host needs a usable default without treating an incomplete 4B/27B comparison as a global quality ranking.
Decision: when self-hosted generation is explicitly enabled, qwen3.5 4B is the local default; the standalone no-model demo remains extractive. 27B stays configurable as an experimental/high-quality candidate. Do not implement routing, replay failed M2, request another GPU window or add a holdout during this closeout.
Evidence: 17 real frozen-evidence calls, 4B 12/12 and 27B 5/12; 27B was more complete on paired S3/M1, but M2 violated the strict citation-ID type contract and stopped the run. The 27B calls were slower and left only 2,278 MiB sampled free VRAM at minimum. 4B had incomplete S3/M4 and false refusals M1/B1. See `reports/model-eval/20261006T051242Z/comparison.md`.
Trade-off/limit: this is a local resource and contract decision, not proof that 4B answers better. Seven 27B cases, including refusal and injection, are unknown. The run isolated generation from fixed evidence and did not verify the optional API's real embedding/reranker/generator path. A future holdout is only considered if final portfolio review finds it required for a truthful resume claim.
## Independent embedded catalog
Why/problem: upstream knowledge repository also handles WeChat and notifications, and requires business IAM/MySQL/task stack.
Design: fresh SQLite catalog with neutral tenant/workspace/audience; version activation owns a transaction.
Alternative: duplicate upstream stack or introduce hosted vector service.
Trade-off: reproducible small local environment, but no distributed/HA claims.
Implementation: ekb/catalog.py + store.py.
Test/result: catalog red/green tests prove access denial, failed replacement safety, stale completion fencing and archive races.
Limitation: embedded single host and synchronous ingest; restart recovery is explicit retry.

## Authority before retrieval and after providers
Why/problem: derived vectors may survive archive or model delay.
Design: eligibility SQL is authority; scope applies before scoring. Recheck before and after model call. Invalid citation IDs reject the whole answer.
Alternative: rely only on vector filter or silently discard invented references.
Trade-off: extra DB reads; stronger revocation behavior and honest malformed-output handling.
Test/result: archive-during-generation and invalid-citation tests.
Limitation: response snapshot, not an atomic permission lock through network transmission.

## Local lexical baseline, optional semantic model
Why/problem: cannot claim BGE/Qwen quality without a real endpoint. Windows Lite support is not assumed.
Design: transparent BM25/CJK bigram baseline, optional exact dense/RRF/model rerank.
Alternative: fake hash embeddings branded as semantic search; this was rejected.
Trade-off: reproducible offline retrieval but paraphrase recall is weaker.
Test/result: actual 28-case eval, all raw outcomes retained.
Limitation: default answer is a direct source excerpt; semantic generation acceptance remains separate.

## Conservative refusal
Why/problem: first eval found incidental overlap produced 7/8 false answers on negatives.
Design: remove English stopwords and require .45 query-term coverage for lexical candidates.
Alternative: tune a corpus-dependent BM25 score threshold; use real semantic rerank/generator when available.
Trade-off: current negative refusal 8/8 but one multi-intent case loses recall; substantial paraphrase still fails.
Test/result: regression tests saw failures then passed; baseline/current eval retained.
Limitation: inspected development set, not unseen validation. No general lift claim.
