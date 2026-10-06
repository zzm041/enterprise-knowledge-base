# Resume facts for Codex B — scoped evidence only

These are repository facts available for a truthful project description. Codex B must separately verify the candidate's own contribution and avoid presenting this AI-assisted personal project as employment, a customer deployment or an independent commercial SaaS.

## Defensible technical facts

- Built an independent Python 3.12/FastAPI enterprise-knowledge demonstration with a SQLite document catalog, explicit text/Markdown ingestion, immutable document versions, archive/restart persistence, and tenant/workspace/audience-aware retrieval. [Architecture](TARGET_ARCHITECTURE.md), [HTTP demo](reports/http-demo.json).
- Implemented BM25 retrieval with a lexical coverage gate, versioned exact-source citations and permission rechecks; negative/unauthorized paths are covered by tests and the demo. [Evaluation](reports/eval-20261006T052813Z-f8a060/results.json), [demo route](DEMO.md).
- Verified a configurable rerank adapter through the real application `/query` route with a deterministic HTTP fixture: order `[1,2]` changed to `[2,1]`, the answer/citation pointed to document 2, and tenant/workspace/audience boundaries held. [Query-chain evidence](reports/rerank-query-chain.json). Say **integration verified**, not “real reranker improved retrieval quality.”
- Maintained **74 passing automated tests**, Ruff lint and formatting checks, and a 10-check separate-process local HTTP demonstration. [Verification](reports/VERIFICATION.md).
- Authored a 28-question synthetic development evaluation (20 answerable, 8 negative): BM25 Recall@4/MRR@4/nDCG@4 `0.90`; negative refusal `8/8`; recorded multi-intent and paraphrase misses. [Raw evaluation](reports/eval-20261006T052813Z-f8a060/results.json).
- Ran a separate, controlled 17-call fixed-evidence 4B/27B generation comparison on the same local GPU with fail-closed stop at a strict citation-schema error; measured answer coverage, groundedness/citations, latency and sampled device memory. [Comparison](reports/model-eval/20261006T051242Z/comparison.md), [raw responses](reports/model-eval/20261006T051242Z/results.json).
- Clearly traced the project to property-ai knowledge-service experience while keeping this implementation, tests, data, reports and runtime independent on E. [Source discovery](SOURCE_DISCOVERY.md), [extraction map](EXTRACTION_MAP.md).

## Numbers and claims that must remain qualified

- `0.90` is **Recall@4/MRR@4/nDCG@4 on one inspected 28-case synthetic BM25 development set**, not general answer accuracy, production performance or a quality uplift. Earlier ungated `.95` recall came with much worse negative refusal; do not advertise a blanket improvement.
- `8/8` is refusal on eight synthetic negative queries; it does not mean the system correctly refuses all unsupported requests. The `paraphrase-hard` demo shows an incorrect refusal when evidence exists.
- `74 passed` is automated local test count, not production acceptance, security certification or live model quality.
- The 17-call experiment had 4B `12/12` attempted and 27B `5/12` attempted, with `16` runner successes, `1` strict schema failure and `0` unknown. These are execution/contract counts, not “16/17 correct answers.” 27B was more complete on paired S3/M1; 4B was incomplete on S3/M4 and falsely refused M1/B1. Neither model can be called a general quality winner.
- Cold/warm latency and VRAM values are small, sequential, device-level samples from supplied-gold-passage generation, not throughput, per-process resource use or API end-to-end latency. Do not combine them with the 28-case retrieval metrics into one model score.
- The API default is **extractive**. The local 4B choice applies only when a model-enabled profile is deliberately configured. Do not claim a deployed LLM-RAG service, production users, real customer/business data, QPS, revenue, paid-provider integration or a blind holdout.
