# Extraction map
No verbatim source/fixture/evidence/Git history copied. Reuse means a documented invariant reimplemented independently.
|Source|Use/dependencies|Domain coupling|Value|Decision/reason|Target|
|---|---|---|---|---|---|
|domain/knowledge chunk/hash|stdlib text|low|stable citations|refactor; validate overlap, exact offsets|ekb/text.py|
|service/knowledge lifecycle|SQLAlchemy/IAM/audit/tasks|high|atomic publish, failed replacement safety|rewrite small local transactions|ekb/catalog.py|
|model/repository knowledge|MySQL/users/property/WeChat|high|source version authority|rewrite independent SQLite|ekb/store.py|
|retrieval ACL derivation|resident/employee/grants|high|target-scoped rights|rewrite server-owned tenant/workspace grants|ekb/auth.py|
|Milvus filter + MySQL check|vector + relational DB|medium|index not permission authority|reuse invariant; SQL before scoring and fresh recheck|ekb/retrieval.py|
|HTTP embedding/rerank|HTTP/config/errors|low|self-hosted model interfaces|rewrite strict indexed/finite output contracts|ekb/providers.py|
|structured LLM/citations|factory/property prompt|medium|attributable answers|refactor neutral evidence, reject invalid citation IDs|ekb/answer.py|
|Milvus revision contract|pymilvus|namespace|model mismatch prevention|reuse persisted vector fingerprint invariant|ekb/store.py|
|Celery durable tasks|broker/business task kinds|high|crash recovery|omit broker; explicit persisted retryable ingest|ekb/catalog.py|
|Knowledge tests|business fixtures|high|failure/scope regressions|rewrite all synthetic isolated cases|tests/|
|Mini-program|WeChat/property catalog|high|demo|omit; own HTTP demo + API docs|scripts/demo.py|
|Notifications/work orders/identity|whole business system|high|none here|omit|none|
