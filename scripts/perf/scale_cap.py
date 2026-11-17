
"""Bump the perf workspace one document at a time until the frozen cap refuses."""
from __future__ import annotations
import json, sys
from pathlib import Path
ROOT = Path(r"E:\enterprise-knowledge-base"); sys.path.insert(0, str(ROOT))
from ekb.auth import Principal
from ekb.catalog import Catalog
from ekb.errors import KBError

DATA = ROOT / "runtime/perf-scale2"
OUT = Path(r"D:\AI\handoff\perf\scale-curve.json")
entry = json.loads((DATA / "config.json").read_text(encoding="utf8"))["credentials"][0]
actor = Principal(subject=entry["subject"], tenant=entry["tenant"], workspaces=entry["workspaces"], audiences=entry["audiences"])
cat = Catalog(DATA / "knowledge.db")
docs = [json.loads(x) for x in Path(r"D:\AI\handoff\perf\corpus\govcn-corpus.jsonl").read_text(encoding="utf8").splitlines() if x.strip()]
small = sorted((d for d in docs if len(d["text"].encode("utf8")) < 60_000), key=lambda d: len(d["text"]))
k = 0
while k < 200:
    d = small[k % len(small)]
    try:
        doc = cat.create(actor, "handbook", (d["title"][:150] + f" 递增{k}")[:190], d["text"], "all", f"bump-{k}.md", "perf")
        cat.ingest(actor, doc["id"], 1)
    except KBError as e:
        before = len(cat.eligible(actor, "handbook"))
        ceiling = {"code": e.code, "status": getattr(e, "status", 409), "chunks_before_refusal": before,
                   "attempted_doc_chunks": len(__import__("ekb").text.chunks(d["text"])),
                   "rule": "ekb/catalog.py:204  active_count + len(pieces) > 5000 -> KB.CORPUS_LIMIT 409"}
        break
    k += 1
else:
    ceiling = {"code": "none", "chunks": len(cat.eligible(actor, "handbook"))}
prev = json.loads(OUT.read_text(encoding="utf8"))
prev["ceiling"] = ceiling
prev["chunks_at_cap"] = len(cat.eligible(actor, "handbook"))
OUT.write_text(json.dumps(prev, ensure_ascii=False, indent=2), encoding="utf8")
print(json.dumps(ceiling, ensure_ascii=False), "docs_added", k)
