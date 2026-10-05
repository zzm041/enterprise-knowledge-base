# Fresh-context read-only review and fixes
Reviewer: separate read-only Codex sub-agent; it ran no tests and inspected no credentials/upstream. This is code review, not independent production acceptance.
Findings: missing default request logs; workspace retrieval disabled after oversize chunk publication; one-shot embedding exceeds bounded response cap; enormous JSON integers escape validation; 413 request IDs inconsistent.
Main implementation wrote 7 regressions: reports/review-fixes-red.txt shows 7 failures/27 passes. Fixes: configured structured logger; capacity check before activation under write lock and preserve old revision; batches of at most 8; numeric overflow maps to stable provider error; shared request ID. reports/review-fixes-green.txt shows 34 passes.
Severity ruling: request-ID inconsistency is included because it breaks the explicit error-tracing contract, despite reviewer P3 classification. No production acceptance inferred.
