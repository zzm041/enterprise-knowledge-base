# Gap analysis
1–3 effective development days; not SaaS production certification. Value estimates are engineering judgment, not salary guarantees.
|Gap|Priority|Target-role value|Cost|Decision|
|---|---|---|---|---|
|Independent config/runtime/data/auth|Must|High|.4 day|implement|
|Versions/archive/failed ingest/concurrency|Must|High|.5 day|implement|
|Permission-tested retrieval/citations/refusal|Must|High|.4 day|implement|
|Labeled eval + raw cases + negative queries|Must|High|.4 day|implement|
|BM25 baseline vs dense/hybrid|Must baseline, Should live dense|High if measured|.3 day + endpoint|BM25; optional strict embedding HTTP/exact cosine/RRF|
|Self-hosted generation|Must adapter, separate model acceptance|High|.3 day + endpoint|contract tests; no fake live result|
|Trace/request IDs/latency/corpus fingerprints|Must|High|.2 day|implement|
|PDF/DOCX/OCR|Should PDF, Nice OCR|Medium|.5–2 days|not now; text/Markdown|
|Fixed-candidate rerank ablation|Should|High when labeled|.3 day + endpoint|optional strict adapter, no invented uplift|
|DeepEval/LLM judge|Should after manual rubric|Medium|.5 day|not now; deterministic metrics + explicit generation rubric|
|Queue/Redis/Milvus service|Not now|Low without scale need|1+ day|no shared service modifications|
|SSO/HA/retention|Not now|Useful production work|several days|limits documented|
|Public deployment/real enterprise data|Not now|unnecessary|new approval|excluded|

Expected minimum 2–3 development days. Local software completion and live semantic quality acceptance are separate. Extractive answers must be labeled; lexical results are not Qwen/BGE scores. Current sources and bounded real JD samples go in docs/engineering-notes/research.md.
