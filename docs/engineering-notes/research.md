# Current research — accessed 2026-10-05
Primary-source background research; source pages generally show no publication date. Access date is not publication date.
- [Milvus filtered search](https://milvus.io/docs/filtered-search.md): standard metadata filtering precedes ANN. Inference: apply authoritative scope before scoring every branch.
- [OWASP RAG security](https://cheatsheetseries.owasp.org/cheatsheets/RAG_Security_Cheat_Sheet.html): permission inheritance, retrieval authorization and revocation-aware cache. Inference: recheck relational authority before/after generation; vectors are not permission authority.
- [OWASP authorization](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html): default deny and server-side request authorization.
- [BAAI BGE-M3 model card](https://huggingface.co/BAAI/bge-m3): dense/sparse/multi-vector model; sparse learned weights are not BM25. Feature hashing is not BGE semantic embedding.
- [Milvus reranking](https://milvus.io/docs/reranking.md): RRF rank fusion, k=60. [Sentence Transformers retrieve/rerank](https://www.sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html): cross-encoder query-document scoring differs from RRF.
- [CrossEncoder evaluation](https://www.sbert.net/docs/package_reference/cross_encoder/evaluation.html): do not insert un-retrieved positives into a supposedly fair rerank comparison. Freeze corpus/cases/candidate budget.
- [Milvus Lite](https://milvus.io/docs/milvus_lite.md): documented platforms Ubuntu/macOS, not native Windows. Exact local retrieval avoids an unverified deployment claim.
- Dependency versions resolved into uv.lock in own environment. Official metadata checked: [FastAPI](https://pypi.org/pypi/fastapi/json), [HTTPX](https://pypi.org/pypi/httpx/json), [Uvicorn](https://pypi.org/pypi/uvicorn/json).

## Bounded job samples, not salary-market evidence
[BOSS Hangzhou listing](https://www.zhipin.com/zhaopin/80024450d2fb885d0nF62t27EA~~/) exposed:
- 九州云腾 AI Agent, 15–20K, 3–5 years, bachelor's; Java/full-stack/data-modeling duties. [Detail](https://www.zhipin.com/job_detail/e379474c6fd44f320nBz09W4EVZW.html)
- 华大研究院 AI full-stack, 10–15K, 1–3 years, bachelor's; AI/data platform/prototyping. [Detail](https://www.zhipin.com/job_detail/9be478b0e7b15f2c0nRz3Ni7FVFS.html)
- 熵评 AI Agent, 12–15K, 3–5 years, junior college; automotive ToB application. [Detail](https://www.zhipin.com/job_detail/6c034d8691e28cfa0nFz0t-9FVVX.html)
Listing was readable; all detail pages hit security verification and were not bypassed. Full JD, posting date and still-hiring status unconfirmed. Exact 14–18K market prevalence not established. These samples only motivate engineering-delivery/data-modeling relevance; no salary guarantee.
