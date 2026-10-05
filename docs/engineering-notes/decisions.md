# Engineering notes for read-only reuse by Codex A/B
## Independent embedded catalog
Why/problem: upstream knowledge repository also handles WeChat and notifications, and requires business IAM/MySQL/task stack.
Design: fresh SQLite catalog with neutral tenant/workspace/audience; version activation owns a transaction.
Alternative: duplicate upstream stack or introduce hosted vector service.
Trade-off: reproducible small local environment, but no distributed/HA claims.
Implementation: ekb/catalog.py + store.py.
Test/result: catalog red/green tests prove access denial, failed replacement safety, stale completion fencing and archive races.
Limitation: embedded single host and synchronous ingest; restart recovery is explicit retry.

## Authority before retrieval and after providers
Why/problem: derived vectors may survive archive or model delay.
Design: eligibility SQL is authority; scope applies before scoring. Recheck before and after model call. Invalid citation IDs reject the whole answer.
Alternative: rely only on vector filter or silently discard invented references.
Trade-off: extra DB reads; stronger revocation behavior and honest malformed-output handling.
Test/result: archive-during-generation and invalid-citation tests.
Limitation: response snapshot, not an atomic permission lock through network transmission.

## Local lexical baseline, optional semantic model
Why/problem: cannot claim BGE/Qwen quality without a real endpoint. Windows Lite support is not assumed.
Design: transparent BM25/CJK bigram baseline, optional exact dense/RRF/model rerank.
Alternative: fake hash embeddings branded as semantic search; this was rejected.
Trade-off: reproducible offline retrieval but paraphrase recall is weaker.
Test/result: actual 28-case eval, all raw outcomes retained.
Limitation: default answer is a direct source excerpt; semantic generation acceptance remains separate.

## Conservative refusal
Why/problem: first eval found incidental overlap produced 7/8 false answers on negatives.
Design: remove English stopwords and require .45 query-term coverage for lexical candidates.
Alternative: tune a corpus-dependent BM25 score threshold; use real semantic rerank/generator when available.
Trade-off: current negative refusal 8/8 but one multi-intent case loses recall; substantial paraphrase still fails.
Test/result: regression tests saw failures then passed; baseline/current eval retained.
Limitation: inspected development set, not unseen validation. No general lift claim.
