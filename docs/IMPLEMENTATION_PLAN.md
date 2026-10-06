# Enterprise Knowledge Base implementation plan
Goal: independently runnable authorization-aware retrieval with honest eval.
Spec: ../TARGET_ARCHITECTURE.md. Native implementation; user's explicit continuous-execution authorization supersedes repeated skill approval gates.
Constraints: upstream read-only; all writes here; Python 3.12; no private data, paid APIs or large downloads.
Review focus: concurrent archive; failed/out-of-order replacement; indexed/finite provider outputs; negative-query false positives; error/log redaction.
1. Catalog: ekb/text.py, store.py, auth.py, catalog.py; tests/test_catalog.py. Write lifecycle/ACL/concurrency tests RED, implement, GREEN.
2. Retrieval: retrieval.py, providers.py, answer.py; test_retrieval.py/test_providers.py. Catalog.eligible -> Engine.query; test ACL, false citations, timeouts, ranking then implement and verify.
3. API/config/demo: api.py/config.py/cli.py/scripts/demo.py, test_api.py. RED auth/errors/input; implement; GREEN; separate HTTP process/restart verification.
4. Eval/closeout: evaluation/corpus.json/cases.json, evaluate.py/test_evaluate.py, README/docs. RED metric contracts; actual raw eval, bad cases, independent read-only review, full tests, upstream snapshot and independence verification.

## Historical model-evaluation execution plan and current stop
The user-approved same-GPU design is recorded in [INDEPENDENT_MODEL_EVAL.md](INDEPENDENT_MODEL_EVAL.md), and the [dedicated execution plan](superpowers/plans/2026-10-05-independent-model-eval.md) remains as historical preparation. The run stopped at 17/24 with GPU_RELEASE_CONFIRMED / HAND_BACK; the owner accepted this result and halted model-selection expansion. See [MODEL_SELECTION.md](MODEL_SELECTION.md) and the [comparison](../reports/model-eval/20261006T051242Z/comparison.md). No remaining plan checkbox authorizes replay, a new GPU window, routing or holdout. Current closeout focuses on a truthful, small standalone portfolio artifact, not a commercial SaaS.
