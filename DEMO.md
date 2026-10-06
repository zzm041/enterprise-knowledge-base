# Enterprise Knowledge Base: 3–5 minute local demonstration

This is an E-only, synthetic, extractive-answer demonstration. It needs Python 3.12 and the existing locked environment; it starts a temporary FastAPI process on an available localhost port, creates its own catalog and random credentials under ignored `runtime/`, then stops that process. It makes no model or upstream calls. From `E:\enterprise-knowledge-base`:

```powershell
$env:TEMP = "$PWD\.cache\temp"
$env:TMP = $env:TEMP
$env:UV_CACHE_DIR = "$PWD\.cache\uv"
$env:UV_PYTHON_DOWNLOADS = "never"
uv sync --frozen
.\.venv\Scripts\python.exe scripts\demo.py
Get-Content .\reports\http-demo.json -Raw | ConvertFrom-Json | Select-Object status,checks,answer,bad_case,cross_tenant_read_status,server_stopped,elapsed_seconds
```

The script is the repeatable live run. Use the report and the source in `scripts/demo.py` to show the actual requests; the suggested speaking route below takes 3–5 minutes even though its automated checks normally finish faster.

| Time | Show and say | Expected observable result |
| --- | --- | --- |
| 0:00–0:40 | Run the command. Point out `init` creates an isolated synthetic SQLite catalog and local credentials, then a separate Uvicorn process starts. | `status: PASS`, `startup` and `anonymous denial` in checks. |
| 0:40–1:35 | Show the `Demo restoration` document created and ingested over HTTP, then query `restoration checksum review`. | `answer.status: answered`, `answer_mode: extractive`; the answer is from the ingested text. |
| 1:35–2:15 | Expand `answer.citations[0]`. | Matching document ID, title, revision 1, exact source offsets and excerpt; `permission_recheck: passed`. |
| 2:15–2:55 | Show authorization checks. | Cross-tenant direct read returns 404; reader archive returns 403; anonymous list returns 401. |
| 2:55–3:40 | Show `bad_case` from the same live run. The source `Account recovery` exists, but “I forgot my login secret; where can I regain access?” is refused. | `actual_status: insufficient_knowledge`; this is a known lexical paraphrase miss, not a correct unsupported-question refusal. |
| 3:40–4:30 | Show restart persistence, archive exclusion and process stop. | Checks include both lifecycle items; after archive the query has no citation; `server_stopped: true`. |

Recorded full run: [HTTP demo report](reports/http-demo.json), with **10 checks passing** and the temporary server stopped. A separate [application-query rerank verification](reports/rerank-query-chain.json) uses a deterministic HTTP reranker fixture: baseline document order `[1,2]` becomes `[2,1]`, the answer and citation point to document 2, and tenant/workspace/audience boundaries remain enforced. This demonstrates integration behavior, not real reranker quality or end-to-end LLM generation.
