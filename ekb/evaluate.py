"""Small synthetic validation set. Never interpreted as production or semantic proof."""

import argparse
import hashlib
import json
import math
import platform
import statistics
from datetime import datetime, timezone
from uuid import uuid4

from ekb.auth import Principal
from ekb.catalog import Catalog
from ekb.config import ROOT, load_config
from ekb.retrieval import Engine


def metrics(cases, k=4):
    positive = [c for c in cases if c["relevant"]]
    negative = [c for c in cases if not c["relevant"]]
    recall = []
    reciprocal = []
    ndcg = []
    for case in positive:
        relevant = set(case["relevant"])
        ranking = list(dict.fromkeys(case["ranked"]))[:k]
        hits = [1 if x in relevant else 0 for x in ranking]
        recall.append(sum(hits) / len(relevant))
        reciprocal.append(next((1 / (i + 1) for i, x in enumerate(hits) if x), 0))
        dcg = sum(x / math.log2(i + 2) for i, x in enumerate(hits))
        ideal = sum(1 / math.log2(i + 2) for i in range(min(k, len(relevant))))
        ndcg.append(dcg / ideal)

    def mean(values):
        return sum(values) / len(values) if values else None

    return {
        "k": k,
        "answerable_count": len(positive),
        "negative_count": len(negative),
        "recall_at_k": mean(recall),
        "mrr_at_k": mean(reciprocal),
        "ndcg_at_k": mean(ndcg),
        "negative_refusal_rate": mean(
            [int(c["status"] == "insufficient_knowledge") for c in negative]
        ),
    }


def populate(catalog, corpus, embedder=None):
    ids = {}
    for item in corpus:
        workspace = item.get("workspace", "handbook")
        owner = Principal(
            "synthetic-editor",
            item.get("tenant", "acme"),
            {workspace: "editor"},
            ("all", "internal"),
        )
        d = catalog.create(
            owner, workspace, item["title"], item["text"], item["audience"]
        )
        catalog.ingest(owner, d["id"], 1, embedder)
        ids[d["id"]] = item["key"]
    return ids


def run(mode="bm25", config_path=None):
    stamp = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
    )
    report_dir = ROOT / "reports" / ("eval-" + stamp)
    report_dir.mkdir(parents=True)
    db = ROOT / "runtime" / ("eval-" + stamp + ".db")
    corpus_path = ROOT / "evaluation" / "corpus.json"
    cases_path = ROOT / "evaluation" / "cases.json"
    corpus = json.loads(corpus_path.read_text(encoding="utf8"))
    questions = json.loads(cases_path.read_text(encoding="utf8"))
    conf = load_config(config_path) if config_path else None
    embedder = conf.provider(conf.embedding) if conf else None
    generator = conf.provider(conf.generation) if conf else None
    reranker = conf.provider(conf.reranking) if conf else None
    catalog = Catalog(db)
    ids = populate(catalog, corpus, embedder)
    engine = Engine(catalog, embedder, generator, reranker)
    rows = []
    for case in questions:
        audiences = ("all", "internal") if case["audience"] == "internal" else ("all",)
        actor = Principal("synthetic-reader", "acme", {"handbook": "reader"}, audiences)
        answer = engine.query(actor, "handbook", case["question"], mode)
        ranked = list(
            dict.fromkeys(
                ids[i] for i in answer["trace"].get("ranked_document_ids", [])
            )
        )
        citations = [ids[x["document_id"]] for x in answer["citations"]]
        rows.append(
            {
                **case,
                "ranked": ranked,
                "status": answer["status"],
                "citations": citations,
                "answer": answer["answer"],
                "trace": answer["trace"],
            }
        )
    latency = [r["trace"]["total_ms"] for r in rows]
    bad = [
        r["id"]
        for r in rows
        if (
            r["relevant"]
            and (
                not set(r["relevant"]).issubset(r["ranked"][:4])
                or r["status"] != "answered"
            )
        )
        or (not r["relevant"] and r["status"] != "insufficient_knowledge")
    ]
    report = {
        "run_id": stamp,
        "dataset_kind": "hand-authored synthetic validation, not independent blind benchmark",
        "retrieval_mode": mode,
        "answer_mode": engine.generator.mode,
        "embedding_fingerprint": embedder.fingerprint if embedder else None,
        "reranker_model": reranker.model if reranker else None,
        "corpus_sha256": hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
        "cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "python": platform.python_version(),
        "candidate_k": 12,
        "top_k": 4,
        "metrics": metrics(rows),
        "latency_ms": {"median": statistics.median(latency), "max": max(latency)},
        "bad_case_ids": bad,
        "cases": rows,
        "limits": [
            "Lexical retrieval is not semantic embedding quality.",
            "Citation existence is not a measure of factual entailment.",
            "Single process small synthetic corpus; no concurrency performance claim.",
            "Questions and corpus authored together; no unbiased generalization claim.",
        ],
    }
    (report_dir / "results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8"
    )
    (ROOT / "reports" / "latest-evaluation.txt").write_text(
        str(report_dir / "results.json"), encoding="utf8"
    )
    print(
        json.dumps(
            {
                "report": str(report_dir / "results.json"),
                "metrics": report["metrics"],
                "bad_case_ids": bad,
            },
            ensure_ascii=False,
        )
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["bm25", "dense", "hybrid"], default="bm25")
    parser.add_argument("--config")
    args = parser.parse_args()
    run(args.mode, args.config)
