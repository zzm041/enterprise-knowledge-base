
"""Break the /query latency down into its parts, on the live data dir.

Times, for the live catalog: SQL eligible() scan, BM25 + coverage over all chunks,
and one CPU rerank call on 12 candidates against the local rag_service. Read-only.
"""
from __future__ import annotations
import json, time, sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx
from ekb.retrieval import bm25, tokens

DB = [ROOT / "runtime/console-live/knowledge.db"]
print("db candidates:", [str(p) for p in DB])
if not DB:
    raise SystemExit("no db")
db = DB[0]

con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
rows = con.execute("""
    SELECT c.id, c.text FROM chunks c
    JOIN documents d ON d.id = c.document_id
    JOIN versions v ON v.document_id = c.document_id AND v.version = c.version
    WHERE d.workspace='handbook' AND d.audience IN ('all','internal')
      AND d.status='ACTIVE' AND c.version=d.current_version AND v.status='ACTIVE'
    ORDER BY c.id
""").fetchall()
texts = [r["text"] for r in rows]
print("chunks:", len(texts), "avg chars:", round(sum(len(t) for t in texts)/max(len(texts),1), 1))

Q = "入职当天在哪里领取办公电脑"

def timeit(fn, n=5):
    xs = []
    for _ in range(n):
        t0 = time.perf_counter(); fn(); xs.append((time.perf_counter()-t0)*1000)
    xs.sort()
    return {"p50_ms": round(xs[len(xs)//2], 2), "max_ms": round(xs[-1], 2)}

def sql_scan():
    con.execute("""
        SELECT c.id, c.text FROM chunks c
        JOIN documents d ON d.id = c.document_id
        JOIN versions v ON v.document_id = c.document_id AND v.version = c.version
        WHERE d.workspace='handbook' AND d.audience IN ('all','internal')
          AND d.status='ACTIVE' AND c.version=d.current_version AND v.status='ACTIVE'
        ORDER BY c.id
    """).fetchall()

def bm25_full():
    scores = bm25(Q, texts)
    qt = set(tokens(Q))
    _ = [len(qt.intersection(tokens(t)))/max(len(qt),1) for t in texts]

print("sql eligible:", timeit(sql_scan, 10))
print("bm25+coverage:", timeit(bm25_full, 10))

# one rerank call on 12 candidates, the way the engine does it
cands = texts[:12]
with httpx.Client(timeout=120.0) as c:
    t0 = time.perf_counter()
    r = c.post("http://127.0.0.1:56850/v1/rerank", json={"model": "bge-reranker-v2-m3", "query": Q, "documents": cands})
    dt = (time.perf_counter()-t0)*1000
    print("rerank status", r.status_code, "first_call_ms", round(dt,1))
    for k in (6, 12):
        t0 = time.perf_counter()
        r = c.post("http://127.0.0.1:56850/v1/rerank", json={"model": "bge-reranker-v2-m3", "query": Q, "documents": cands[:k]})
        print(f"rerank k={k} status={r.status_code} ms={round((time.perf_counter()-t0)*1000,1)}")

print(json.dumps({"chunks": len(texts)}, ensure_ascii=False))
