# Model selection: measured decision, not inherited role assignment
Status (2026-10-06): **17 real calls completed; fail-closed stop; GPU_RELEASE_CONFIRMED / HAND_BACK.** Active project: E:/enterprise-knowledge-base. The owner accepted the stopped run and closed model-selection expansion. **qwen3.5:4b is the default model choice for a model-enabled profile on this local 16 GB machine; 27B remains a configurable experimental/high-quality candidate.** This is an engineering operating choice, not a claim that 4B has better answer quality. Routing and further holdout are deferred.

The user boundary supersedes all earlier shared-window/runtime-binding permission discussions below. D:/property-ai remains entirely read-only. See the [run design and historical execution notes](INDEPENDENT_MODEL_EVAL.md) and [17-call comparison](../reports/model-eval/20261006T051242Z/comparison.md). The upstream owner established the temporary window and owns restoration; Codex C stopped only its E-owned service. That window has been handed back. Historical preparation below is not a new execution authorization.

## Accepted local model strategy and measured evidence

The frozen 12-case-per-candidate plan stopped after **17/24** real generation calls: 4B **12/12**; 27B **5/12** (four runner successes, one strict-schema failure); success/failure/unknown **16/1/0** overall. 27B M2 answered all five substantive facts and identified the right two sources, but emitted citation IDs as strings `["1","2"]` instead of required integer IDs `[1,2]`. The runner immediately stopped, did not retry M2, and left seven 27B cases unattempted. E-owned Ollama, its children, port 11535 and GPU residency were released; [summary](../reports/model-eval/20261006T051242Z/summary.json) and [independent release check](../reports/model-eval/20261006T051242Z/post-run-release-verification.json) retain the evidence.

| Measured dimension | 4B | 27B | Reading limit |
| --- | --- | --- | --- |
| Full gold case result | 8/12 | 5/5 semantically; 4/5 strict usable | Only five 27B cases were attempted. |
| Grounded factual answered outputs | 7/7 | 5/5 | False refusals count against quality, not as invented factual claims. |
| Strict citation correctness on answered outputs | 7/7 | 4/5 | 27B M2 has a type/schema error, not a wrong cited passage. |
| Multi-document completeness | 2/4 | 2/2 semantic, 1/2 strict usable | 27B M3/M4 are untested. |
| Required refusal cases | 3/3, plus false refusals M1/B1 | None attempted | No 27B refusal conclusion. |
| Cold first request / warm mean | 4.859 / 1.187 s | 11.391 / 4.070 s | One serial run; output lengths differ. |
| Peak sampled device VRAM used / minimum free | 5,967 / 10,029 MiB | 13,718 / 2,278 MiB | Device-level samples; 27B remained only 278 MiB above the 2,000 MiB in-flight floor. |

On paired cases, 27B gave more complete answers for S3 and M1. 4B answered S3 and M4 incompletely and falsely refused supported M1 and B1. 4B correctly handled the three required refusals and the A1 injected passage; 27B never reached those cases. Neither model is a general quality winner from this partial, synthetic, fixed-evidence experiment. It measured generation with supplied passages, **not** end-to-end retrieval or the optional main-API model adapter.

The independent application's `ekb.cli init` still boots an extractive, no-model demo by default. “4B default” means the chosen candidate **if local self-hosted generation is enabled and separately configured**; no Ollama service or GPU window is automatically started by this project. 27B stays configurable for controlled experiments. Do not implement routing, replay M2, request a new GPU window, or add a holdout under this decision. Reconsider holdout only if final portfolio closeout finds it is a genuine blocker for a truthful resume claim.

## Read-only findings (2026-10-05)
Upstream deploy/README.md:70–71,135,141 and provider/factory.py establish independent business release gates: Knowledge/RAG may pass while work-order structured analysis does not. scripts/ops/local_ai_http.py enforces rag_only/work_order_only protocols and independent candidate identities.
No head-to-head 4B-versus-27B Enterprise KB benchmark was found in these inspected implementation/docs. The known partition is a release/resource decision; it is not evidence 4B is best for this project. Historical quality reports with business payloads were not read.

|Capability|Host base / route|Docker caller|Model|
|---|---|---|---|
|Guarded RAG adapter|http://127.0.0.1:11436/v1/chat/completions|http://host.docker.internal:11436/v1/chat/completions|qwen3.5:4b|
|RAG native Ollama|http://127.0.0.1:11438; OpenAI base /v1|not the configured business caller route|qwen3.5:4b|
|Embedding adapter|http://127.0.0.1:11436/v1/embeddings|http://host.docker.internal:11436/v1/embeddings|bge-m3, 1024 dimensions|
|Reranker adapter|http://127.0.0.1:11436/v1/rerank|http://host.docker.internal:11436/v1/rerank|bge-reranker-v2-m3|
|Work-order adapter|http://127.0.0.1:11437/v1/chat/completions|http://host.docker.internal:11437/v1/chat/completions|logical qwen38-27b-q3km-g55:7af3b8f|
|27B native Ollama|http://127.0.0.1:11435; OpenAI base /v1|not the configured business caller route|runtime qwen38-27b-q3km-g55-ctx8192:7af3b8f|

Evidence: exact allowlist of nonsecret fields from deploy/.env.delivery; Compose host.docker.internal mapping; deploy/rag-runtime.ps1:24,197,220; deploy/work-ai-runtime.ps1 runtime tag; scripts/ops/local_ai_models.py.
BGE revision: 5617a9f61b028005a4858fdac845db406aefb181. Reranker revision: 953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e.
BGE is a local offline CPU transformer behind the adapter, CLS pooling + L2 normalization, not Ollama /api/embed. Reranker applies sigmoid to sequence-classification logits and returns ordered scores, not indexed results. New generic adapter's indexed-results contract is different and must not be silently treated as compatible.
The RAG adapter requires internal authorization, X-Prompt-Version=rag-answer-v2, one user message, temperature=0, reasoning_effort=none, max_tokens=512. The work-order adapter rejects that RAG protocol. A generic two-message RAG payload is not directly compatible with either guarded adapter.
Native metadata GETs to 11435/11438 returned Ollama 0.34.4 and both model tags, with no resident model. Adapter health without credentials returns 401. No token was read/copied. Metadata evidence: reports/model-selection-metadata.json and rag-runtime-metadata.json. Configured Docker routes were read, not tested from inside containers.

## Historical protection findings (shared execution now revoked)
Both protected adapters use D:/property-ai/tmp/delivery-ai-shared/model.lock and completion-unknown.json. Each inference writes reservations, inputs, results and potentially failures under upstream runtime directories. The lock code opens a+b and writes a byte. Resource guards check 4500 MiB free RAM, 6000 MiB free GPU at cold start and 2000 MiB while running. Fixed native context: 4B 4096, 27B 8192; one model/concurrent request; keep-alive 30s.
On timeout/resource-floor/unknown completion the guard may stop the exact receipt-bound native runtime, or set a shared completion barrier. Read-only code inspection alone cannot authorize service stop or upstream runtime writes.
An earlier, now-revoked authorization allowed a 24-call shared protected window, transient upstream lock/ledger and controlled native stop/unload/reload. Source, Git, all configuration files, database and business data remain read-only. The shared lock/barrier directory is ignored and has no Git-tracked entries. That historical recovery/configuration conflict is no longer the proposed execution route. All upstream runtime writes and service controls by Codex C remain forbidden.

## Concrete bounded experiment
Prepared dataset: evaluation/model-selection-cases.json; request manifest generated by scripts/prepare_model_selection.py. 12 synthetic cases × 2 models = 24 calls, sequential, no retry. Max 512 output tokens, temperature 0, reasoning disabled; same questions, evidence, instructions, output contract for both. No business data, no large downloads, no paid API.
Phase A was a controlled-evidence generation comparison: retrieval was held constant using gold passages. This isolates generation and MUST NOT be reported as end-to-end RAG. The formerly proposed Phase B is Future Work and is not authorized in the current closeout.
Small bounded set includes simple extraction, Chinese, four multi-document questions, three refusals, ambiguous policy boundaries and source prompt injection. Historical upstream context capacities differ. The new E-owned plan targets equal effective context 4096 for both, verified from actual runtime behavior; preserve model digests and stop on unverified/mismatched settings. Prompts fit both.
Record raw JSON, parse failures, citation IDs, per-call wall time, cold/warm classification, RAM/GPU baseline and peak/delta samples, completion state, model digest, context size and manifest hash.
Human/independent-review rubric: answer coverage 0/1/2; every claim supported 0/1/2; citation entailment 0/1/2; multi-document completeness 0/1/2; correct refusal boolean. Citation membership is automated syntax evidence only, not faithfulness. Do not use the tested model as its own judge. Missing measurements remain null.

## Historical decision rule, declared before inference
Security/unsupported claims take precedence over speed. Compare paired cases and show disagreements, not only a combined average.
- Fixed 4B only if it meets all critical faithfulness/refusal/citation cases and 27B adds no material multi-document benefit on this small set.
- Fixed 27B if it materially improves required answer coverage/faithfulness and fits measured resource/latency constraints.
- Routing only if a stable, observable pre-generation rule separates failures and actually improves quality/cost; validate on a fresh holdout before implementation. No routing solely because it sounds advanced.
24 calls are an initial screening, not enough to establish production routing reliability. A boundary case gets a stated follow-up sample, not fabricated certainty.

## Historical restoration blocker — exception request withdrawn
The native runtime is bound to PID, executable and process start time. `scripts/ops/local_ai_http.py` loads that identity into adapter memory and checks the binding file against `runtime_binding_sha256`. A guard-triggered native process stop makes the old binding stale. Restarting the native process alone cannot restore usable guarded model entry points, even if `/healthz` still reports ready.

The existing recovery flow writes a new temporary binding receipt and updates only `runtime_binding_path` / `runtime_binding_sha256` in:
- `D:/property-ai/tmp/delivery-rag-runtime/config.json`
- `D:/property-ai/tmp/delivery-work-ai-runtime/config.json`

The corresponding adapter must restart to load the new identity. The request to change these two runtime-only configurations is withdrawn following the latest user instruction. They remain read-only, including temporary binding receipts. Shared-runtime recovery is excluded from the replacement design.

Historical checks are saved in `reports/model-window-preflight.json`: API/critical and optional checked dependencies healthy; both authenticated guarded adapters ready and inactive; both native endpoints have zero resident models; Worker active/reserved/scheduled all zero; task-intent rows only terminal statuses. These historical observations do not establish the new window. Fresh business/AI pause confirmation comes from the original owner; Codex C verifies OS/GPU observations without calling upstream APIs or touching its state. Earlier queue probe found two existing DLQ entries; they were not consumed or changed.

## Historical execution preparation
The shared-window code has been moved to inert historical text under `reports/revoked-shared-window/`. `scripts/model_window.py` fails closed without imports or operations against property-ai. Its old shared locking/stopping path is no longer callable through the active module.

All project material was migrated to E:/enterprise-knowledge-base; the Python environment was recreated offline. Original report paths inside historical evidence are preserved. Current synthetic configuration resolves inside the new root.

This section records the preparation stage before the completed 2026-10-06 run. The current 17-call outcome, release state and operating choice are in the first section above. No further inference is authorized by the historical design or window receipts.
