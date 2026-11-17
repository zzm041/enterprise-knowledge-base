
"""Ingest the real gov.cn corpus into a throwaway data dir and measure how the
frozen retrieval path scales: per-query SQL scan + BM25 + lexical coverage gate
as the chunk count grows, and where the documented 5000-chunk ceiling bites.

Read-only w.r.t. the product: it only writes into runtime/perf-scale.
"""
from __future__ import annotations

import json, statistics, sys, time
from pathlib import Path

ROOT = Path(r"E:\enterprise-knowledge-base")
sys.path.insert(0, str(ROOT))

from ekb.auth import Principal
from ekb.catalog import Catalog
from ekb.cli import initialize
from ekb.errors import KBError
from ekb.retrieval import bm25, tokens

CORPUS = Path(r"D:\AI\handoff\perf\corpus\govcn-corpus.jsonl")
DATA = ROOT / "runtime/perf-scale"
OUT = Path(r"D:\AI\handoff\perf\scale-curve.json")

docs = [json.loads(x) for x in CORPUS.read_text(encoding="utf8").splitlines() if x.strip()]
print("corpus docs:", len(docs), "chars:", sum(len(d["text"]) for d in docs))

# what normalize() rejects, so skipped docs carry a reason instead of a bare code
def reject_reason(text):
    if len(text.encode("utf-8")) > 200_000:
        return "KB.INVALID_TEXT (>200000 bytes)"
    bad = [hex(ord(c)) for c in text if ord(c) < 32 and c not in "\n\r\t"]
    if bad:
        return "KB.INVALID_TEXT (control chars " + ",".join(sorted(set(bad))) + ")"
    return None

if (DATA / "config.json").exists():
    import shutil
    shutil.rmtree(DATA)

initialize(DATA)
entry = json.loads((DATA / "config.json").read_text(encoding="utf8"))["credentials"][0]
actor = Principal(subject=entry["subject"], tenant=entry["tenant"],
                  workspaces=entry["workspaces"], audiences=entry["audiences"])
catalog = Catalog(DATA / "knowledge.db")
QUESTIONS = ["入职当天在哪里领取办公电脑", "年假有几天", "出差住宿每晚上限多少", "采购一万元以上要几家比价"]

def count():
    try:
        return len(catalog.eligible(actor, "handbook"))
    except KBError as e:
        return f"LIMIT({e.code})"

def measure(label, n):
    samples = []
    for q in QUESTIONS * 3:
        t0 = time.perf_counter()
        rows = catalog.eligible(actor, "handbook")
        t1 = time.perf_counter()
        bm25(q, [r["text"] for r in rows])
        qt = set(tokens(q))
        [len(qt & set(tokens(r["text"]))) / max(len(qt), 1) for r in rows]
        t2 = time.perf_counter()
        samples.append(((t1 - t0) * 1000, (t2 - t1) * 1000))
    elig = statistics.median(s[0] for s in samples)
    bm = statistics.median(s[1] for s in samples)
    print(f"{label:>12} chunks={n:>5}  eligible_p50={elig:7.1f}ms  bm25+gate_p50={bm:7.1f}ms  total={elig+bm:7.1f}ms")
    return {"label": label, "chunks": n, "eligible_p50_ms": round(elig, 1),
            "bm25_gate_p50_ms": round(bm, 1), "retrieval_total_p50_ms": round(elig + bm, 1)}

curve, skipped, targets, ingested = [], [], [50, 200, 500, 1000, 2000, 3000, 4000], 0
n = 0
for d in docs:
    why = reject_reason(d["text"])
    if why:
        skipped.append({"id": d["id"], "title": d["title"][:60], "reason": why, "bytes": len(d["text"].encode("utf8"))})
        continue
    doc = catalog.create(actor, "handbook", d["title"][:180], d["text"], "all", f"govcn-{d['id']}.md", "perf")
    catalog.ingest(actor, doc["id"], 1)
    ingested += 1
    n = count()
    if targets and isinstance(n, int) and n >= targets[0]:
        curve.append(measure(f"@{ingested}docs", n))
        targets.pop(0)

n = count()
print("ingested docs:", ingested, "chunks:", n, "skipped:", len(skipped))
curve.append(measure(f"@{ingested}docs(max real)", n))

# where the hard ceiling actually lands: duplicates until eligible() refuses
ceiling, dup = None, 0
while dup < 400:
    d = docs[dup % len(docs)]
    if reject_reason(d["text"]):
        dup += 1
        continue
    doc = catalog.create(actor, "handbook", (d["title"][:140] + f" 副本{dup}")[:190],
                         d["text"], "all", f"dup-{dup}.md", "perf")
    catalog.ingest(actor, doc["id"], 1)
    dup += 1
    got = count()
    if isinstance(got, str):
        ceiling = {"at_chunks": dup_n if (dup_n := len(_prev)) else None, "code": got, "extra_docs": dup}
        break
    _prev = [0] * got
if ceiling is None:
    ceiling = {"code": "not reached", "extra_docs": dup, "chunks": count()}
print("ceiling:", ceiling)

out = {"corpus_docs": len(docs), "ingested_docs": ingested, "skipped": skipped,
       "curve": curve, "ceiling": ceiling}
OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf8")
print(json.dumps({"curve": curve, "skipped": len(skipped), "ceiling": ceiling}, ensure_ascii=False))
