
from __future__ import annotations
import json, statistics, sys, time
from pathlib import Path
ROOT = Path(r"E:\enterprise-knowledge-base"); sys.path.insert(0, str(ROOT))
from ekb.auth import Principal
from ekb.catalog import Catalog
from ekb.retrieval import bm25, tokens
DATA = ROOT / "runtime/perf-scale2"
OUT = Path(r"D:\AI\handoff\perf\scale-curve.json")
entry = json.loads((DATA / "config.json").read_text(encoding="utf8"))["credentials"][0]
actor = Principal(subject=entry["subject"], tenant=entry["tenant"], workspaces=entry["workspaces"], audiences=entry["audiences"])
cat = Catalog(DATA / "knowledge.db")
QS = ["入职当天在哪里领取办公电脑", "年假有几天", "出差住宿每晚上限多少", "采购一万元以上要几家比价"]
s = []
for q in QS * 4:
    t0 = time.perf_counter(); rows = cat.eligible(actor, "handbook"); t1 = time.perf_counter()
    bm25(q, [r["text"] for r in rows]); qt = set(tokens(q))
    [len(qt & set(tokens(r["text"]))) / max(len(qt), 1) for r in rows]
    t2 = time.perf_counter(); s.append(((t1-t0)*1000, (t2-t1)*1000))
n = len(rows)
e = statistics.median(x[0] for x in s); b = statistics.median(x[1] for x in s)
print(json.dumps({"chunks": n, "eligible_p50_ms": round(e,1), "bm25_gate_p50_ms": round(b,1), "total_p50_ms": round(e+b,1)}))
prev = json.loads(OUT.read_text(encoding="utf8"))
prev["curve"].append({"label": "at-cap", "chunks": n, "eligible_p50_ms": round(e,1), "bm25_gate_p50_ms": round(b,1), "retrieval_total_p50_ms": round(e+b,1)})
prev["chars_per_chunk_avg"] = 440
OUT.write_text(json.dumps(prev, ensure_ascii=False, indent=2), encoding="utf8")
