# Execution evidence
Native implementation; all writes to new independent project. User explicitly prohibited worktree/branch of upstream, so no worktree used.
Task 1 RED missing module -> GREEN 6 tests.
Task 2 RED missing retrieval/providers -> GREEN 20 tests.
Task 3 RED missing API -> GREEN 22 tests.
Task 4 RED missing metrics -> GREEN 24 tests and actual 28-case eval.
Refusal improvement RED 2 failures -> GREEN 27 tests; retained both evals and recall trade-off.
Fresh read-only review -> 7 reproductions RED -> 34 GREEN.
Scope decision: SQLite/exact retrieval for bounded native-Windows independent demo; cost is lack of scale/HA parity with upstream.
2026-10-06 current state: active root E:/enterprise-knowledge-base; D:/property-ai was reference-only and remains untouched. The upstream owner established the new exclusive GPU window, Codex C ran the E-owned service serially, and the service/GPU were released before HAND_BACK. The controlled comparison stopped at 17/24 after 27B M2 violated the integer citation-ID contract: 4B 12/12, 27B 5/12, success/failure/unknown 16/1/0. No replay or additional model work is planned. See `reports/model-eval/20261006T051242Z/comparison.md` and `docs/MODEL_SELECTION.md`.

Accepted operating decision: 4B is the local 16 GB model-enabled default; 27B remains a configurable experimental/high-quality candidate, not an inferior-quality model. 27B's observed S3/M1 completeness advantage, M2 schema failure, higher latency and narrow VRAM headroom are recorded. 4B's incomplete S3/M4 and false refusals M1/B1 are recorded. No routing or holdout now; both are conditional Future Work only if final portfolio truthfulness requires new evidence.

Portfolio closeout: full offline suite 74/74, Ruff check and format check clean; `reports/eval-20261006T052813Z-f8a060/results.json` retains the 28-case BM25 evaluation and bad cases. `reports/rerank-query-chain.json` records a positive FastAPI query-chain rerank test with exact citation and scope checks; `reports/http-demo.json` records a ten-check real localhost process demo on E, including the `paraphrase-hard` limitation. `DEMO.md`, `PORTFOLIO_READY.md` and `RESUME_FACTS.md` carry the reproducible route and claim boundaries. No model selection expansion or commercial SaaS work was added.
