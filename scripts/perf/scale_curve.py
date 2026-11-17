
"""Clean single-pass scale curve on the real gov.cn corpus: real docs first,
then duplicate documents to walk the same workspace up to the 5000-chunk cap.
Writes runtime/perf-scale2 only.
"""
from __future__ import annotations
import json, statistics, shutil, sys, time
from pathlib import Path

ROOT = Path(r"E:\enterprise-knowledge-base"); sys.path.insert(0, str(ROOT))
from ekb.auth import Principal
from ekb.catalog import Catalog
from ekb.cli import initialize
from ekb.errors import KBError
from ekb.retrieval import bm25, tokens

CORPUS = Path(r"D:\AI\handoff\perf\corpus\govcn-corpus.jsonl")
DATA = ROOT / "runtime/perf-scale2"
OUT = Path(r"D:\AI\handoff\perf\scale-curve.json")
docs = [json.loads(x) for x in CORPUS.read_text(encoding="utf8").splitlines() if x.strip()]

def reject(t):
    if len(t.encode("utf8")) > 200_000: return "KB.INVALID_TEXT (>200000 bytes)"
    bad = [hex(ord(c)) for c in t if ord(c) < 32 and c not in "\n\r\t"]
    return "KB.INVALID_TEXT (control chars " + ",".join(sorted(set(bad))) + ")" if bad else None

if DATA.exists(): shutil.rmtree(DATA)
initialize(DATA)
entry = json.loads((DATA / "config.json").read_text(encoding="utf8"))["credentials"][0]
actor = Principal(subject=entry["subject"], tenant=entry["tenant"], workspaces=entry["workspaces"], audiences=entry["audiences"])
cat = Catalog(DATA / "knowledge.db")
QS = ["入职当天在哪里领取办公电脑", "年假有几天", "出差住宿每晚上限多少", "采购一万元以上要几家比价"]
n_chunks = lambda: len(cat.eligible(actor, "handbook"))

def measure(label):
    s = []
    for q in QS * 3:
        t0 = time.perf_counter(); rows = cat.eligible(actor, "handbook"); t1 = time.perf_counter()
        bm25(q, [r["text"] for r in rows]); qt = set(tokens(q))
        [len(qt & set(tokens(r["text"]))) / max(len(qt), 1) for r in rows]
        t2 = time.perf_counter(); s.append(((t1 - t0) * 1000, (t2 - t1) * 1000))
    e = statistics.median(x[0] for x in s); b = statistics.median(x[1] for x in s); n = n_chunks()
    print(f"{label:>18} chunks={n:>5} eligible_p50={e:7.1f}ms bm25+gate_p50={b:7.1f}ms total={e+b:7.1f}ms", flush=True)
    return {"label": label, "chunks": n, "eligible_p50_ms": round(e, 1), "bm25_gate_p50_ms": round(b, 1), "retrieval_total_p50_ms": round(e + b, 1)}

curve, skipped, targets = [], [], [500, 1000, 2000, 3000, 4000, 4800]
real = 0
for d in docs:
    why = reject(d["text"])
    if why:
        skipped.append({"id": d["id"], "reason": why, "bytes": len(d["text"].encode("utf8"))}); continue
    doc = cat.create(actor, "handbook", d["title"][:180], d["text"], "all", f"govcn-{d['id']}.md", "perf")
    cat.ingest(actor, doc["id"], 1); real += 1
    if targets and n_chunks() >= targets[0]:
        curve.append(measure(f"real@{real}docs")); targets.pop(0)

print("real docs:", real, "chunks:", n_chunks(), "skipped:", len(skipped), flush=True)
dup = 0
while targets:
    d = docs[dup % len(docs)]
    if reject(d["text"]): dup += 1; continue
    try:
        doc = cat.create(actor, "handbook", (d["title"][:140] + f" 副本{dup}")[:190], d["text"], "all", f"dup-{dup}.md", "perf")
        cat.ingest(actor, doc["id"], 1)
    except KBError as e:
        print("ingest refused:", e.code, "at", n_chunks()); break
    dup += 1
    if n_chunks() >= targets[0]:
        curve.append(measure(f"dup{dup}")); targets.pop(0)

before = n_chunks()
try:
    d = docs[0]
    doc = cat.create(actor, "handbook", "越界验证", d["text"], "all", "over.md", "perf")
    cat.ingest(actor, doc["id"], 1); ceiling = {"code": "accepted", "chunks": n_chunks()}
except KBError as e:
    ceiling = {"code": e.code, "status": e.status, "chunks_before": before,
               "rule": "ekb/catalog.py:204 active_count + len(pieces) > 5000 -> KB.CORPUS_LIMIT 409"}
out = {"corpus_docs": len(docs), "real_docs": real, "skipped": skipped, "curve": curve, "ceiling": ceiling,
       "source": "www.gov.cn policy documents, D:/AI/handoff/perf/corpus/govcn-corpus.jsonl"}
OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf8")
print("ceiling:", ceiling)
