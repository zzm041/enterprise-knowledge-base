
"""Compare retrieval modes on the frozen 28-case set without touching reports/.

The shipped evaluation (ekb/evaluate.py) writes into reports/, which is frozen
evidence for the portfolio. This runner reuses the same corpus/cases, the same
k=4 / candidate_k=12 / bad-case rules, but writes its reports under evals-rag/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ekb.auth import Principal
from ekb.catalog import Catalog
from ekb.config import load_config
from ekb.evaluate import metrics
from ekb.retrieval import Engine


def providers(rag_base: str, *, embed_model: str, rerank_model: str, chat_model: str | None,
              dimensions: int, gen_base: str | None = None) -> dict:
    blocks = {
        "embedding": {"base_url": rag_base, "model": embed_model,
                      "dimensions": dimensions, "revision": embed_model, "timeout": 120.0},
    }
    if rerank_model:
        blocks["reranking"] = {"base_url": rag_base, "model": rerank_model, "timeout": 120.0}
    if chat_model:
        # ekb ModelHTTP rejects timeout > 120s, so the ceiling is the harness budget.
        blocks["generation"] = {"base_url": gen_base or rag_base, "model": chat_model,
                                "timeout": 120.0}
    return blocks


def write_config(directory: Path, blocks: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    config_path = directory / "config.json"
    config_path.write_text(json.dumps({
        "database": str((directory / "eval.db").relative_to(ROOT)).replace("\\\\", "/"),
        "credentials": [{
            "token_sha256": hashlib.sha256(b"eval-only-token").hexdigest(),
            "tenant": "acme",
            "subject": "synthetic-reader",
            "workspaces": {"handbook": "reader"},
            "audiences": ["all", "internal"],
        }],
        "embedding": blocks.get("embedding", {}),
        "generation": blocks.get("generation", {}),
        "reranking": blocks.get("reranking", {}),
    }, ensure_ascii=False, indent=2), encoding="utf8")
    return config_path


def populate(catalog: Catalog, corpus: list, embedder) -> dict:
    ids = {}
    for item in corpus:
        workspace = item.get("workspace", "handbook")
        owner = Principal("synthetic-editor", item.get("tenant", "acme"),
                          {workspace: "editor"}, ("all", "internal"))
        doc = catalog.create(owner, workspace, item["title"], item["text"], item["audience"])
        catalog.ingest(owner, doc["id"], 1, embedder)
        ids[doc["id"]] = item["key"]
    return ids


def run_mode(mode: str, conf, corpus: list, questions: list, candidate_k: int = 12) -> dict:
    embedder = conf.provider(conf.embedding) if conf.embedding else None
    generator = conf.provider(conf.generation) if conf.generation else None
    reranker = conf.provider(conf.reranking) if conf.reranking else None
    db = conf.database
    if db.exists():
        db.unlink()
    catalog = Catalog(db)
    ids = populate(catalog, corpus, embedder)
    engine = Engine(catalog, embedder, generator, reranker, candidate_k=candidate_k)
    rows = []
    started = time.perf_counter()
    for case in questions:
        audiences = ("all", "internal") if case["audience"] == "internal" else ("all",)
        actor = Principal("synthetic-reader", "acme", {"handbook": "reader"}, audiences)
        answer = engine.query(actor, "handbook", case["question"], mode)
        ranked = list(dict.fromkeys(ids[i] for i in answer["trace"].get("ranked_document_ids", [])))
        rows.append({
            **case,
            "ranked": ranked,
            "status": answer["status"],
            "citations": [ids[c["document_id"]] for c in answer["citations"]],
            "answer": answer["answer"],
            "trace": answer["trace"],
        })
    elapsed = round(time.perf_counter() - started, 1)
    latency = [r["trace"]["total_ms"] for r in rows]
    bad = [
        r["id"] for r in rows
        if (r["relevant"] and (not set(r["relevant"]).issubset(r["ranked"][:4])
                              or r["status"] != "answered"))
        or (not r["relevant"] and r["status"] != "insufficient_knowledge")
    ]
    m = metrics(rows)
    m["answered"] = sum(1 for r in rows if r["status"] == "answered")
    return {
        "retrieval_mode": mode,
        "answer_mode": engine.generator.mode,
        "embedding_fingerprint": embedder.fingerprint if embedder else None,
        "reranker_model": reranker.model if reranker else None,
        "metrics": m,
        "latency_ms": {"median": statistics.median(latency), "max": max(latency)},
        "bad_case_ids": bad,
        "cases": rows,
        "wall_seconds": elapsed,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rag", default="http://127.0.0.1:5599/v1")
    ap.add_argument("--modes", default="bm25,dense,hybrid")
    ap.add_argument("--embed-model", default="bge-m3")
    ap.add_argument("--dimensions", type=int, default=1024)
    ap.add_argument("--rerank-model", default="")
    ap.add_argument("--chat-model", default="")
    ap.add_argument("--gen-base", default="",
                    help="generation endpoint; defaults to --rag (set http://127.0.0.1:11434/v1 to bypass the gateway)")
    ap.add_argument("--candidate-k", type=int, default=12,
                    help="retrieval candidates handed to the reranker (upstream default 12)")
    ap.add_argument("--out", default="evals-rag")
    args = ap.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
    out_dir = ROOT / args.out / ("eval-" + stamp)
    out_dir.mkdir(parents=True, exist_ok=True)

    blocks = providers(args.rag, embed_model=args.embed_model, rerank_model=args.rerank_model,
                       chat_model=args.chat_model, dimensions=args.dimensions,
                       gen_base=args.gen_base or None)
    config_path = write_config(out_dir, blocks)
    conf = load_config(config_path)

    corpus_path = ROOT / "evaluation" / "corpus.json"
    cases_path = ROOT / "evaluation" / "cases.json"
    corpus = json.loads(corpus_path.read_text(encoding="utf8"))
    questions = json.loads(cases_path.read_text(encoding="utf8"))

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    reports = []
    for mode in modes:
        print(f"[eval] mode={mode} start", flush=True)
        report = run_mode(mode, conf, corpus, questions, args.candidate_k)
        report.update({
            "run_id": stamp,
            "dataset_kind": "hand-authored synthetic validation, not independent blind benchmark",
            "corpus_sha256": hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
            "cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
            "candidate_k": args.candidate_k,
            "top_k": 4,
            "python": platform.python_version(),
        })
        (out_dir / f"results-{mode}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
        m = report["metrics"]
        print(f"[eval] {mode}: recall={m['recall_at_k']} mrr={m['mrr_at_k']} "
              f"ndcg={m['ndcg_at_k']} neg={m['negative_refusal_rate']} "
              f"answered={m['answered']}/{len(questions)} bad={len(report['bad_case_ids'])} "
              f"median_ms={report['latency_ms']['median']} wall={report['wall_seconds']}s", flush=True)
        reports.append(report)

    summary = {
        "run_id": stamp,
        "corpus_sha256": reports[0]["corpus_sha256"],
        "cases_sha256": reports[0]["cases_sha256"],
        "answer_mode": reports[0]["answer_mode"],
        "reranker_model": reports[0]["reranker_model"],
        "rows": [
            {
                "mode": r["retrieval_mode"],
                "recall_at_4": r["metrics"]["recall_at_k"],
                "mrr_at_4": r["metrics"]["mrr_at_k"],
                "ndcg_at_4": r["metrics"]["ndcg_at_k"],
                "negative_refusal_rate": r["metrics"]["negative_refusal_rate"],
                "answered": r["metrics"]["answered"],
                "bad_cases": len(r["bad_case_ids"]),
                "median_ms": r["latency_ms"]["median"],
            }
            for r in reports
        ],
        "limits": [
            "Same 28 hand-authored cases; questions and corpus authored together, not a blind benchmark.",
            "Recall/MRR/nDCG measure retrieval order only; citation presence is not factual entailment.",
            "Single process SQLite, small corpus: no concurrency or scale claim.",
        ],
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps({"report_dir": str(out_dir), "summary": summary["rows"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
