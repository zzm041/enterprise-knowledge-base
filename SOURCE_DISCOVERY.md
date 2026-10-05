# Source discovery — 2026-10-05
Upstream D:/property-ai HEAD 7f89baa9a599b95eb84b7ecd451e197c021c80a7.
Read-only inspection, not an upstream test rerun. Phase 1 ending HEAD/porcelain matched beginning. Existing WIP includes model/ai.py (textual diff empty), deploy config, mini-program config, reports, learning and career. No knowledge source changes were found. Path-only snapshots under reports/.

## Actual boundary
- api/v1/assistant.py POST /assistant/query -> service/retrieval.py. admin_knowledge.py exposes scoped list/detail/create/version/archive. Mini-program assistant and supervisor/knowledge are property-specific UI.
- service/knowledge.py: service-owned transactions, fresh management principal, audit, immutable source versions and ingestion jobs. Embedding/index calls outside transaction. Attempt and latest-version checks fence activation. Failed replacement preserves prior active version. Archive hides knowledge; a new version can reactivate.
- model/knowledge.py: document/version/chunk/job, but also NotificationDelivery. repository/knowledge.py mixes knowledge, users, WeChat and notification operations.
- domain/knowledge.py: paragraph/character chunks, defaults 400 chars/40 overlap, title/chunk locators; property-specific visibility enums.
- provider/knowledge_parser.py: UTF-8 text/Markdown only, explicitly rejects PDF. MySQL source_text is authority; storage_key alone does not prove document-file ingestion.
- retrieval.py: derive tenant/property/visibility -> embedding -> Milvus top 8 -> MySQL current-version/active/ACL recheck -> rerank top 4 threshold .12 -> structured LLM -> allowlisted citations. No hybrid lexical query here.
- embedding provider HTTP /embeddings; development mode is character hashing, not BGE. Reranker development mode is overlap. Qwen/OpenAI-compatible generator is factory-configured.
- vector provider checks dimension/metric/model metadata. Milvus is rebuildable derivative; docs/11-OPERATIONS says rebuild from MySQL.
- worker/tasks.py/task_intents.py connect Celery and durable leases. Copying this imports wider business task infrastructure.
- Config contains independent rag_llm/embedding/reranker/model-revision settings. No .env or secrets read.
- Limitations: retrieval discards request_id; rerank failure assigns all visible candidates 1.0, weakening relevance rejection. Reordered/invalid HTTP responses need stronger adapter contracts.

## Evidence inspected
Relevant sections/searches: docs/00-PROJECT-SCOPE, 01-PRD, 02-ROLES-PERMISSIONS, 05-AI-DISPATCH-SPEC, 06-API-CONTRACT, 10-ACCEPTANCE, 11-OPERATIONS; backend/pyproject.toml.
Actual test bodies: test_knowledge_policy, test_knowledge_retrieval_scope, selected test_knowledge_runtime and test_milvus_rag. Management test cases enumerate locking/version/archive/scope/pagination. Integration tests mutate databases/collections; not run.
Actual historical diffs: 7d24202 fixes mixed-role tenant resolution from persisted property; e543c6f reactivation preserves version history. Log subjects 28c08df/ed66d37 indicate scoped RAG/durable work, not independently verified acceptance.
Runtime model reports, business logs, attachments and credentials: not investigated in this pass.

## Decisions answering discovery questions
Reusable boundary: knowledge lifecycle plus permission-aware evidence retrieval. Common capabilities: persistence, identity context, bounded providers, audit. Resident/employee/property models and visibility are coupled. Reuse lifecycle fencing and permission invariants, rewrite coupled service/repository. Omit WeChat, notifications, work orders, Redis/RabbitMQ/MinIO/whole IAM. Missing independent evaluation, neutral access and experiment traces are Must. New parsers/large infrastructure are not automatically required.
