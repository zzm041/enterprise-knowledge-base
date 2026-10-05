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

Generation quality: default response is extractive; exact citation integrity tested. No LLM factuality/faithfulness score exists. Apply manual rubric in MODEL_EXPERIMENTS.md to future live-model runs.
