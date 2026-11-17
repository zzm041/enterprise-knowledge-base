
"""Push the throwaway perf workspace from the real corpus up to the frozen
5000-chunk ceiling, measuring retrieval cost on the way, then show what the
ceiling actually does (ingest refuses, read refuses).
"""
from __future__ import annotations

import json, statistics, sys, time
from pathlib import Path

ROOT = Path(r"E:\enterprise-knowledge-base")
sys.path.insert(0, str(ROOT))
from ekb.auth import Principal
from ekb.catalog import Catalog
from ekb.errors import KBError
from ekb.retrieval import bm25, tokens

DATA = ROOT / "runtime/perf-scale"
CORPUS = Path(r"D:\AI\handoff\perf\corpus\govcn-corpus.jsonl")
OUT = Path(r"D:\AI\handoff\perf\scale-curve.json")
prev = json.loads(OUT.read_text(encoding="utf8")) if OUT.exists() else {"curve": [], "skipped": []}

entry = json.loads((DATA / "config.json").read_text(encoding="utf8"))["credentials"][0]
actor = Principal(subject=entry["subject"], tenant=entry["tenant"],
                  workspaces=entry["workspaces"], audiences=entry["audiences"])
catalog = Catalog(DATA / "knowledge.db")
docs = [json.loads(x) for x in CORPUS.read_text(encoding="utf8").splitlines() if x.strip()]
QUESTIONS = ["入职当天在哪里领取办公电脑", "年假有几天", "出差住宿每晚上限多少", "采购一万元以上要几家比价"]

def count():
    return len(catalog.eligible(actor, "handbook"))

def measure(label, n):
    s = []
    for q in QUESTIONS * 3:
        t0 = time.perf_counter(); rows = catalog.eligible(actor, "handbook"); t1 = time.perf_counter()
        bm25(q, [r["text"] for r in rows]); qt = set(tokens(q))
        [len(qt & set(tokens(r["text"]))) / max(len(qt), 1) for r in rows]
        t2 = time.perf_counter(); s.append(((t1 - t0) * 1000, (t2 - t1) * 1000))
    e = statistics.median(x[0] for x in s); b = statistics.median(x[1] for x in s)
    print(f"{label:>22} chunks={n:>5}  eligible_p50={e:7.1f}ms  bm25+gate_p50={b:7.1f}ms  total={e+b:7.1f}ms")
    return {"label": label, "chunks": n, "eligible_p50_ms": round(e, 1),
            "bm25_gate_p50_ms": round(b, 1), "retrieval_total_p50_ms": round(e + b, 1)}

curve = prev["curve"]
n = count(); print("start chunks:", n)
targets = [x for x in (4000, 4990) if x > n]
dup = 0
while targets:
    d = docs[dup % len(docs)]
    try:
        doc = catalog.create(actor, "handbook", (d["title"][:140] + f" 副本{dup}")[:190], d["text"], "all", f"dup-{dup}.md", "perf")
        catalog.ingest(actor, doc["id"], 1)
    except KBError as e:
        print("ingest refused:", e.code, "at chunks=", count()); break
    dup += 1
    n = count()
    if n >= targets[0]:
        curve.append(measure(f"@dup{dup}", n)); targets.pop(0)

n = count()
curve.append(measure("@ceiling-1", n))
# one more document must be refused
try:
    d = docs[dup % len(docs)]
    doc = catalog.create(actor, "handbook", (d["title"][:140] + " 越界")[:190], d["text"], "all", "over.md", "perf")
    catalog.ingest(actor, doc["id"], 1)
    ceiling = {"code": "accepted", "chunks": count()}
except KBError as e:
    ceiling = {"code": e.code, "status": e.status, "chunks_before": n,
               "rule": "catalog.py:204 active_count + len(pieces) > 5000 -> KB.CORPUS_LIMIT 409"}
print("ceiling:", ceiling)
prev.update({"curve": curve, "ceiling": ceiling, "max_chunks_measured": n})
OUT.write_text(json.dumps(prev, ensure_ascii=False, indent=2), encoding="utf8")
print(json.dumps({"chunks": n, "ceiling": ceiling, "curve_tail": curve[-3:]}, ensure_ascii=False))
