> Active project root: `E:\enterprise-knowledge-base`. `D:\property-ai` remains strictly read-only. The E-owned model comparison stopped after 17 real calls and completed GPU_RELEASE_CONFIRMED / HAND_BACK; there is no active GPU window or model service. The accepted local model choice and limits are below. See the [17-call evidence](reports/model-eval/20261006T051242Z/comparison.md).

# Enterprise Knowledge Base
基于 property-ai 已实现知识服务能力进一步抽象的独立企业知识库专项；去除物业领域耦合，针对知识版本管理、权限检索、检索实验和评测重新工程化。不是第二个独立商业交付案例，也不是生产 SaaS。

Standalone Enterprise RAG system derived and generalized from the knowledge-service capability originally implemented in property-ai, then extended with independent knowledge management, retrieval experimentation and evaluation. Upstream validation is attributed to the source project; this repo's evidence is separately measured.

## What runs today
- Independent Python 3.12/FastAPI API, SQLite catalog, immutable text/Markdown revisions, explicit retryable ingestion, archive and concurrent-update protection.
- Server-owned tenant/workspace/audience grants; authorization before scoring and fresh checks before and after generation.
- BM25 lexical retrieval with an explicit coverage gate. Exact source offsets and versioned citations. Default **extractive answer**, not simulated LLM output.
- Optional localhost OpenAI-compatible embedding/generation and indexed rerank adapters, dense cosine and hybrid RRF. HTTP contracts are tested with doubles; the default application does not start or load a model. Existing 4B/27B assets were copied offline into ignored E-owned runtime storage for a separate controlled generation comparison, not bundled in the distributable source.
- Reproducible synthetic evaluation and real HTTP process demo. The [3–5 minute demonstration route](DEMO.md) uses only local synthetic data.

## Start (PowerShell, from this directory)
Prerequisites: existing Python 3.12 and uv. Installs small Python packages; no large model downloads or Docker needed.

~~~powershell
$env:UV_CACHE_DIR = "$PWD\.cache\uv"
$env:UV_PYTHON_DOWNLOADS = "never"
uv sync --frozen
.\.venv\Scripts\python.exe -m ekb.cli init
.\.venv\Scripts\python.exe -m uvicorn ekb.api:create_app --factory --host 127.0.0.1 --port 8765 --no-access-log
~~~

Init creates new synthetic sources, runtime/config.json and random local demo credentials. It refuses to overwrite an existing database/config. Restart only the last command. No upstream services are required. All writable paths belong to this repository.

Open [local API documentation](http://127.0.0.1:8765/docs); pass Authorization: Bearer TOKEN for business endpoints. Tokens are generated locally under runtime/demo-credentials.json (Git ignored); never copy them into source or reports. Default anonymous access is rejected.

Example in a second PowerShell terminal:
~~~powershell
$creds = Get-Content .\runtime\demo-credentials.json -Raw | ConvertFrom-Json
$headers = @{ Authorization = "Bearer $($creds.reader)" }
$body = @{ workspace = "handbook"; question = "How much notice is required for annual leave?" } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8765/query -Method Post -Headers $headers -ContentType application/json -Body $body
~~~

This is a local synthetic demonstration. Static grants and credentials are not SSO, user provisioning or production identity management.

## Tests / evaluation / reproducible HTTP demo
~~~powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ekb.evaluate
.\.venv\Scripts\python.exe scripts/demo.py
~~~

Tests and caches stay under this repo. Eval creates its own isolated database per run, never the interactive catalog. HTTP demo creates its own config/database/random credentials and temporary localhost process, verifies persistence across restart, then stops only that process.

The 28-case hand-authored set contains 20 answerable and 8 negative queries, plus cross-tenant/workspace/internal distractors. It is a development validation set, **not a blind benchmark**. Baseline and later runs are both retained. The [fresh E-root BM25 run](reports/eval-20261006T052813Z-f8a060/results.json) measured Recall@4/MRR@4/nDCG@4 = 0.90 and negative refusal = 1.00 (8 cases only). Earlier ungated lexical baseline: 0.95 / 0.125. This is a measured precision/recall trade-off on inspected synthetic questions, not a general accuracy uplift. Multi-intent and substantial paraphrase remain bad cases. The [fresh separate-process HTTP demo](reports/http-demo.json) passed ten startup, ingest, citation, denial, bad-case, restart and archive checks. A [positive application-query rerank test](reports/rerank-query-chain.json) verified changed order, matching citation and scope filters with a deterministic HTTP fixture.

## Model Selection
The owner accepted the stopped [17-call real, fixed-evidence comparison](reports/model-eval/20261006T051242Z/comparison.md), with [raw response records](reports/model-eval/20261006T051242Z/results.json): 4B completed 12/12; 27B completed 5/12, with four strict successes and one citation-ID type failure. Overall success/failure/unknown was 16/1/0. The remaining seven 27B calls were not made, and failed M2 will not be replayed. The E-owned service, listener and GPU residency were released. This compared generation from supplied passages, **not** the end-to-end API's retrieval or reranker.

For a model-enabled profile on this local 16 GB machine, **qwen3.5 4B is the default operating choice**. The main no-model demo above remains extractive and never auto-starts Ollama. **27B stays configurable as an experimental/high-quality candidate**, not the current default: it was more complete on paired S3/M1, but had the M2 schema failure, slower observed calls and only 2,278 MiB minimum sampled free VRAM. We do not claim 4B has better quality: it omitted required details in S3/M4 and falsely refused supported M1/B1. No model routing, replay, additional holdout or new GPU window is planned for this closeout. See [decision and denominators](docs/MODEL_SELECTION.md).

No model service or paid API is automatically called. Runtime config never loads property-ai `.env`. Configure only an authorized localhost endpoint and model/revision if explicitly enabling the optional adapters. New source versions ingested with embeddings receive a fingerprint; dense/hybrid rejects text-only or mismatched indices. Switching models means creating/reindexing an independent catalog with the chosen contract. See [adapter protocol](docs/MODEL_EXPERIMENTS.md).
~~~powershell
.\.venv\Scripts\python.exe -m ekb.evaluate --mode hybrid --config runtime/model-config.json
~~~
This will make actual inference calls only to your explicitly configured local services.

## Architecture and provenance
Start with SOURCE_DISCOVERY.md, EXTRACTION_MAP.md, GAP_ANALYSIS.md, TARGET_ARCHITECTURE.md.
Engineering notes: docs/engineering-notes/decisions.md, research.md, evaluation.md, security.md.
No upstream source files, private fixtures, secrets, attachments, databases, volumes or Git history were copied. Upstream was inspected read-only. Relevant evidence is file/commit-based; upstream integration tests were not rerun because they mutate its services.

## Limitations
- Small single-host corpus, exact scoring capped at 5,000 eligible chunks; no ANN scale claims, HA, queue workers or concurrent-load SLA.
- Text/Markdown only; no PDF/DOCX/OCR, URL crawling or arbitrary upload paths.
- Synchronous explicit ingestion; failed/interrupted ingest retries after a 120-second lease. No automatic worker claim.
- Extractive answers may not answer every facet; coverage thresholds are lexical heuristics. Citations establish provenance, not semantic entailment.
- The real 4B/27B run used fixed gold evidence in a separate E-owned runner. It does not prove real embedding/reranker quality or a live LLM response through this API. The positive rerank integration test uses a deterministic HTTP fixture, not a real reranker quality measurement.
- The 27B comparison stopped after five cases, so its refusal, injection and seven later case results are unknown. Its string citation IDs failed the strict integer contract. The 4B answer-quality bad cases above remain.
- Local static credentials/grants, no rate-limiting/SSO/TLS perimeter. Bind localhost only; do not publish as production.
- Dependency TestClient currently emits an HTTPX deprecation warning; retained in test evidence.

## Future Work (not in the current closeout)
- PDF/DOCX/OCR and URL ingestion, ANN/distributed scale, SSO/rate limits/HA and background workers.
- Real embedding/reranker ablations, model routing and any further GPU holdout. Reconsider a holdout only if final portfolio review finds it necessary for a truthful resume claim; do not automatically resume the stopped run.
