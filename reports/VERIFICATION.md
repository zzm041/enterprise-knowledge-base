# Verification snapshot — 2026-10-05
Lifecycle: OFFLINE_CORE_VERIFIED. MODEL_COMPARISON_PREPARED_NOT_RUN. PRODUCTION_NOT_ACCEPTED.
This is a phase delivery, not a claim the full live-model selection objective is complete.

- Independent directory/Git/Python environment/lockfile/config/demo data and runtime namespace: created here.
- Four Phase 1 documents and extraction provenance: present.
- Final test run: 35 passed; one upstream Starlette TestClient/HTTPX deprecation warning retained (reports/final-tests.txt).
- Fresh-context read-only review found five issues; 7 failing regressions reproduced and fixed; subsequent full suite passes. Review did not itself execute tests.
- Real separate Uvicorn localhost HTTP process: 8 checks PASS, including restart persistence and archive exclusion (reports/http-demo.json).
- Actual 28-case synthetic BM25 evaluation: baseline Recall@4=.95 / negative refusal=.125; gated variant .90 / 1.00. Existing known bad cases preserved. These are development results, not held-out/production or LLM quality metrics.
- Current 4B and 27B native endpoints were probed by metadata GET only: both 0.34.4, no resident models; guarded adapter 401 without credential. No secret copied, no inference sent.
- Paired model comparison: 12 fixed synthetic cases × 2 models, 24 prepared requests, scores=null. No fixed model or routing decision.
- Default synthetic interactive configuration initialized; all tokens remain under ignored runtime/.
- Upstream HEAD remains 7f89baa9a599b95eb84b7ecd451e197c021c80a7. Beginning/Phase 1/final HEAD+porcelain snapshots match. All direct writes by this task target this new project. Git snapshots cover tracked/index/WIP metadata, not a byte-level snapshot of all ignored service logs. User-authorized native metadata GETs may be recorded by running services.
- Source/business DB/vector collections/queues/containers/services were not modified or restarted by this task. Existing WIP preserved.
- Live model comparison is waiting for a protected execution boundary compatible with source-read-only constraints. Existing adapter inference writes upstream runtime ledger/lock and may stop a bound service on guard failure; raw calls must not bypass that protection. See docs/MODEL_SELECTION.md.

## Remaining limitations
No SSO/HA/distributed worker/ANN-scale validation or PDF/OCR. Static credentials. Exact citations are provenance, not entailment proof. Multi-intent/paraphrase failure cases remain. Read-only sub-agent review is not independent product/production acceptance.
