# Portfolio Freeze — Enterprise Knowledge Base

Status: **PORTFOLIO_READY for a scoped, local AI-application engineering project** (2026-10-06). The evidence supports a resume handoff to Codex B and a 3–5 minute synthetic demonstration. It does not certify production readiness or a live end-to-end LLM-RAG product.

## Current completed scope

- Independent E-drive Python 3.12/FastAPI service with SQLite document catalog, text/Markdown ingestion, immutable revisions, explicit retryable indexing, archive and restart persistence.
- Server-owned tenant/workspace/audience permissions before retrieval, with fresh eligibility checks around answer generation; BM25 retrieval, coverage-gated extractive answers and versioned source-offset citations.
- Optional local HTTP embedding, generation and rerank adapters. A deterministic HTTP fixture was exercised through the actual FastAPI `/query` path: ranking changed, answer/citation followed the new first document, and tenant/workspace/audience filtering remained intact. This is integration proof, not a real reranker quality benchmark.
- Separate controlled fixed-evidence local generation comparison stopped after 17 real calls. Main API/default demo remains extractive and does not start a model.
- Source relationship: this independent engineering project generalizes knowledge-service ideas inspected read-only in property-ai; it is not a second unrelated commercial delivery. No upstream source, business database, private fixtures, credentials or Git history were copied into the new application.

## Verification and evaluation evidence

| Evidence | Measured outcome | Claim boundary |
| --- | --- | --- |
| Full automated suite and static checks | **74 tests passed**; Ruff lint and format checks passed | Local/offline code validation; one upstream TestClient/HTTPX deprecation warning. |
| [Application-query rerank](reports/rerank-query-chain.json) | Order `[1,2]` → `[2,1]`; citation document `2`; public/internal/other-tenant candidate counts `2/3/1`; unauthorized workspace `403` | Deterministic HTTP fixture, no learned reranker quality or model inference. |
| [28-case synthetic BM25 evaluation](reports/eval-20261006T052813Z-f8a060/results.json) | Recall@4/MRR@4/nDCG@4 `0.90`; unsupported-question refusal `8/8`; bad cases `multi`, `paraphrase-hard` | Hand-authored, inspected development set; not blind, representative or a general accuracy figure. |
| [Separate-process HTTP demonstration](reports/http-demo.json) and [route](DEMO.md) | 10 checks passed; temporary server stopped; live `paraphrase-hard` refusal observed | Default extractive application, not LLM generation. |
| [17-call model comparison](reports/model-eval/20261006T051242Z/comparison.md) and [raw responses](reports/model-eval/20261006T051242Z/results.json) | 4B 12/12 attempted; 27B 5/12 attempted; runner success/failure/unknown `16/1/0`; fail-closed at 27B M2 | Synthetic fixed gold passages supplied to generation; no end-to-end retrieval/rerank comparison. |

## Known limits and non-production boundary

- The extractive answer can miss multi-intent or paraphrased questions despite an available source. Citations prove source provenance, not full semantic entailment.
- 4B was complete on 8/12 fixed-evidence cases and had S3/M4 omissions and M1/B1 false refusals. 27B was semantically complete on its five attempted cases and more complete on paired S3/M1, but failed the strict citation-ID type contract at M2; its remaining seven cases, including refusals, were not run. The local 16 GB model-enabled operating default is 4B for latency/resource/contract reasons, not because 4B won answer quality. The default application remains extractive.
- No real reranker or embedding quality ablation, live LLM response through the API, blind holdout, concurrency SLA, PDF/OCR, SSO, TLS perimeter, rate limits, distributed workers or high-availability proof. SQLite/static local credentials and localhost binding are for a small demonstration, not commercial SaaS.
- This closeout used synthetic sources, ignored runtime-only random credentials, no paid APIs and no GPU window. It made no writes to `D:\property-ai`; upstream restoration and source state remain owned by its original engineering thread. Historical local host paths/operational provenance in reports are not instructions to publish raw reports unchanged.

## Delivery audit

- Offline `uv sync --frozen --offline` checked 24 packages without download. The full test and static checks above were run after the query-chain test and demo updates.
- Secret/PII pattern scan covered **115 tracked or unignored candidate files**. It found no private-key block, cloud/model key pattern, long bearer value, phone-like number, Chinese-ID-like number or email-like address. Two credential-assignment matches were synthetic test fixtures, not live credentials. Git ignores `runtime/`, `.venv/` and `.cache/`, including generated demo tokens and copied local model assets.
- Historical evidence contains local home-directory paths and `D:\property-ai` operational references. These are provenance/path metadata, not copied upstream business data; review or redact raw historical reports before any public repository publication. The resume handoff relies on the scoped facts and links here, not on publishing those reports unchanged.
- All commands and generated artifacts in this closeout targeted E. No property-ai service, source, Git, config, tmp binding, business data or model endpoint was operated on by this task. This is a statement of this task's actions, not a byte-level attestation of changes made by other threads.

## Future Work — outside this freeze

Only if a later objective needs it: real embedding/reranker ablation, live model-backed API integration, accessibility/identity/deployment hardening, larger parsers and scale work. No M2 replay, GPU window, routing or holdout is authorized by this document; holdout is considered only if a later truthful resume claim would otherwise be blocked.
