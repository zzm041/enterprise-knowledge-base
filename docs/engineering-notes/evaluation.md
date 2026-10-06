# Evaluation evidence and bad cases
Dataset: evaluation/corpus.json and cases.json. Entirely newly authored fictional enterprise text. 16 documents include other-tenant, inaccessible-workspace and internal-audience distractors. 28 questions, 20 answerable/8 unanswerable for their assigned principal.
Metrics computed at document level with duplicate IDs removed: Recall@k = fraction of gold documents retrieved; MRR@k = reciprocal first relevant rank; nDCG@k binary relevance; negative refusal separate. Empty classes produce null, not perfect scores. tests/test_evaluate.py checks known ranks.
Reports retain every question, relevant IDs, predicted rank, citations, status, synthetic answer, trace, corpus/cases fingerprints. Latency is sequential local small-corpus timing, not service benchmark.

Initial run: reports/eval-20261005T132700Z-fa0a73/results.json, Recall/MRR/nDCG .95; negative refusal .125.
Coverage-gated run: reports/eval-20261005T132810Z-ea6738/results.json, .90; negative refusal 1.0.
Changes were made after inspecting failures. This is exploratory development evidence, not independent held-out proof. No semantic-model ablation was run.
Remaining bad cases:
- multi: VPN plus password question; per-passage coverage cannot represent two distinct intents, so conservative gate refuses.
- paraphrase-hard: login secret/regain access fails lexical correspondence to account-password recovery.
These motivate measured semantic retrieval or query decomposition later; neither is silently implemented or claimed.

The default application response remains extractive; exact citation integrity and permission rechecks are tested. The fresh E-root retrieval report is `reports/eval-20261006T052813Z-f8a060/results.json` with the same measured .90 Recall@4/MRR@4/nDCG@4, 8/8 negative refusals, and the same `multi` / `paraphrase-hard` bad cases. The independent HTTP process demo in `reports/http-demo.json` passed ten lifecycle/permission/citation/bad-case checks. `reports/rerank-query-chain.json` shows a deterministic HTTP rerank fixture acting through the FastAPI query route: order `[1,2]` to `[2,1]`, citation to document 2 and tenant/workspace/audience boundaries retained. This verifies application integration, not real reranker ranking quality. Retrieval metrics do not grade answer entailment.

## Real fixed-evidence generation screen, 2026-10-06

The controlled E-owned Ollama run sent **17/24** frozen synthetic requests and stopped on the first failure: 4B 12/12, 27B 5/12; runner success/failure/unknown 16/1/0. It did not exercise the application's retrieval or rerank path. Full per-case answers, manual rubric, resource samples and paired disagreements are in `reports/model-eval/20261006T051242Z/comparison.md`; a versionable raw-response snapshot is in `reports/model-eval/20261006T051242Z/results.json`, with the original reservation ledger under the E-owned `runtime/model-eval/budget/`. The E service and GPU were released before HAND_BACK.

- **Answer quality:** 4B met every expected case fact/refusal on 8/12. It omitted S3 laptop/MFA details and M4 MFA verification, and falsely refused M1 and B1 despite supporting evidence. 27B was substantively complete on the five cases it reached; S3 and M1 were more complete than 4B. Seven 27B cases, including refusals and injection, remain untested.
- **Faithfulness/citations:** All factual answered outputs reviewed were supported by their supplied passages (4B 7/7; 27B 5/5). Strict citation correctness was 4B 7/7, 27B 4/5. The 27B M2 answer identified correct sources but serialized IDs as strings, violating the frozen integer contract and causing the fail-closed stop.
- **Latency/resources:** Cold first requests were 4.859 s (4B) and 11.391 s (27B); warm means 1.187 s over 11 calls and 4.070 s over four calls. Peak sampled device VRAM used was 5,967 vs 13,718 MiB; 27B's minimum free VRAM was 2,278 MiB, only 278 MiB above the in-flight floor. These are small serial samples, device-level rather than process-attributed, and not a production throughput estimate.
- **Refusal:** 4B correctly refused all three deliberately unsupported cases but had two false refusals on supported questions. 27B refusal performance is unknown.

The accepted engineering choice is 4B for a model-enabled profile on this local 16 GB host, with 27B configurable as an experimental/high-quality candidate. This is not a 4B quality win. No M2 replay, further GPU window, routing or holdout is in the current scope. A new holdout is conditional Future Work only if final portfolio review finds it necessary for a truthful resume claim.
