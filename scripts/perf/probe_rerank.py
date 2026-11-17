
import json, time, httpx
from pathlib import Path
ROOT = Path(r"E:\enterprise-knowledge-base")
tok = json.loads((ROOT/"runtime/console-live/demo-credentials.json").read_text(encoding="utf8"))["reader"]
Q = "入职当天在哪里领取办公电脑"
with httpx.Client(timeout=180.0, headers={"Authorization": f"Bearer {tok}"}) as c:
    for i in range(10):
        t0 = time.perf_counter()
        r = c.post("http://127.0.0.1:53122/query", json={"workspace": "handbook", "question": Q, "mode": "bm25"})
        dt = (time.perf_counter()-t0)*1000
        b = r.json() if r.status_code == 200 else {}
        t = b.get("trace", {})
        print(i, r.status_code, "client_ms=", round(dt,1), "server_ms=", t.get("total_ms"), "rerank=", t.get("rerank"), "cands=", t.get("lexical_candidates") or t.get("ranked_chunk_ids") and len(t["ranked_chunk_ids"]))
