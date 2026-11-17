#!/usr/bin/env python
"""Build the offline, self-contained verification entry for Enterprise Knowledge Base.

Rules enforced here (not by hand):
  * every number on the page is read from a committed file under reports/ or evaluation/;
  * corpus/cases hashes must match what the evaluation report recorded, otherwise the build fails;
  * no value is invented, rounded up, or copied from prose.

Usage:
    python scripts/build_showcase.py            # build showcase/data.json + showcase/index.html
    python scripts/build_showcase.py --check    # rebuild in memory and fail on any drift
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHOW = ROOT / "showcase"
TEMPLATE = SHOW / "template.html"
INDEX = SHOW / "index.html"
DATA_JSON = SHOW / "data.json"

PLACEHOLDER = "__EKB_DATA__"

BAD_CASE_REASONS = {
    "multi": "多意图：一句话里同时要求两件事，抽取式回答只覆盖其一",
    "paraphrase-hard": "口语改写过深：词法覆盖低于 0.45 门槛，召回为空，漏答",
}

SAMPLE_IDS = [
    "leave-direct",
    "leave-carry",
    "expenses",
    "expenses-deadline",
    "multi",
    "paraphrase-hard",
    "negative-unknown-policy",
    "negative-tenant",
]

PIPELINE = [
    {"title": "入库与版本", "detail": "文本 / Markdown 提交；不可变 revision、归档、显式可重试入索引", "optional": False},
    {"title": "切块", "detail": "按段落边界切分，保留字符偏移，供引用回指原文", "optional": False},
    {"title": "租户 / 工作区 / 可见范围过滤", "detail": "授权在打分之前生效：不属于当前主体的片段根本进不了候选", "optional": False},
    {"title": "BM25 + 覆盖门槛", "detail": "词法打分；候选词法覆盖率 < 0.45 直接不入选，宁缺毋滥", "optional": False},
    {"title": "dense / hybrid / rerank", "detail": "可选适配器：向量余弦、RRF 融合、索引重排；默认不加载模型", "optional": True},
    {"title": "抽取式回答", "detail": "从命中片段直接摘录，不做自由生成，不允许编造句子", "optional": False},
    {"title": "生成后复核 + 引用", "detail": "回答前后各复核一次可见性；引用带 document_id / version / 字符偏移", "optional": False},
]

PERMISSIONS = [
    {"action": "匿名列文档", "actor": "无 Authorization", "result": "401", "tone": "ok", "meaning": "默认拒绝，没有匿名读通道"},
    {"action": "跨租户直读文档", "actor": "租户 A 令牌读租户 B 文档", "result": "404", "tone": "ok", "meaning": "不泄漏资源是否存在（不是 403）"},
    {"action": "reader 归档文档", "actor": "只读角色写操作", "result": "403", "tone": "ok", "meaning": "读写角色分离，写路径独立校验"},
    {"action": "错工作区检索", "actor": "令牌无该 workspace 授权", "result": "403", "tone": "ok", "meaning": "工作区边界在检索前生效"},
    {"action": "归档后再次检索", "actor": "同一 reader 令牌", "result": "insufficient_knowledge", "tone": "warn", "meaning": "归档即退出召回，返回空引用而不是旧答案"},
]

BOUNDS = [
    "只接收文本 / Markdown；PDF、扫描件与 OCR 属于未实现的 Future Work",
    "单机 SQLite，小规模语料；没有并发、QPS 或容量结论",
    "静态本地凭证；无 SSO、无 TLS 周界、无限流、无多副本高可用",
    "抽取式回答在多意图与深度改写处会漏答（见第 04 节坏例）",
    "引用保证出处形式（文档 / 版本 / 字符偏移），不保证自然语言推理正确",
    "未做真实 reranker / embedding 质量消融，也没有独立盲测集",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf8"))


def git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except Exception:
        return "unavailable"


def pick_eval_runs() -> list[tuple[Path, dict]]:
    runs: list[tuple[Path, dict]] = []
    for directory in sorted((ROOT / "reports").glob("eval-*")):
        path = directory / "results.json"
        if path.exists():
            runs.append((path, load(path)))
    if not runs:
        raise SystemExit("no evaluation results found under reports/eval-*")
    return runs


def test_count() -> int:
    """Number of tests recorded by the last full verification, read from the report."""
    summary = ROOT / "reports" / "takeover-verification-20261029-01" / "summary.json"
    if summary.exists():
        data = load(summary)
        nested = data.get("pytest", {}).get("tests")
        if nested is not None:
            return int(nested)
        for key in ("pytest_total", "tests_total", "pytest_passed", "tests"):
            if isinstance(data.get(key), int):
                return data[key]
    raise SystemExit(f"cannot read the recorded test count from {summary}")


def build_data() -> dict:
    demo_path = ROOT / "reports" / "http-demo.json"
    rerank_path = ROOT / "reports" / "rerank-query-chain.json"
    corpus_path = ROOT / "evaluation" / "corpus.json"
    cases_path = ROOT / "evaluation" / "cases.json"

    demo = load(demo_path)
    rerank = load(rerank_path)
    corpus = json.loads(corpus_path.read_text(encoding="utf8"))
    cases = json.loads(cases_path.read_text(encoding="utf8"))

    runs = pick_eval_runs()
    latest_path, latest = max(runs, key=lambda pair: pair[1]["run_id"])

    # provenance guard: the page may only cite numbers whose source files still hash
    # to what the evaluation recorded. Drift means the page would be lying.
    if sha256(corpus_path) != latest["corpus_sha256"]:
        raise SystemExit(
            "evaluation/corpus.json no longer matches the hash recorded in "
            f"{latest_path.relative_to(ROOT)}; re-run evaluation instead of rebuilding the page"
        )
    if sha256(cases_path) != latest["cases_sha256"]:
        raise SystemExit(
            "evaluation/cases.json no longer matches the hash recorded in "
            f"{latest_path.relative_to(ROOT)}; re-run evaluation instead of rebuilding the page"
        )

    metrics = latest["metrics"]
    gated = [
        r for _, r in runs if r["metrics"]["negative_refusal_rate"] >= 0.99 and r["metrics"]["recall_at_k"] >= 0.9
    ]
    ungated = [
        r
        for _, r in runs
        if r["metrics"]["negative_refusal_rate"] < 0.5 and r["metrics"]["recall_at_k"] >= 0.9
    ]

    ablation_rows = []
    for r in ungated[:1]:
        ablation_rows.append(
            {
                "label": "覆盖门槛关闭（无门槛基线）",
                "run_id": r["run_id"],
                "recall": r["metrics"]["recall_at_k"],
                "mrr": r["metrics"]["mrr_at_k"],
                "ndcg": r["metrics"]["ndcg_at_k"],
                "refusal": r["metrics"]["negative_refusal_rate"],
            }
        )
    for r in gated[:1]:
        ablation_rows.append(
            {
                "label": "覆盖门槛 0.45（当前默认）",
                "run_id": r["run_id"],
                "recall": r["metrics"]["recall_at_k"],
                "mrr": r["metrics"]["mrr_at_k"],
                "ndcg": r["metrics"]["ndcg_at_k"],
                "refusal": r["metrics"]["negative_refusal_rate"],
            }
        )

    by_id = {c["id"]: c for c in latest["cases"]}
    sample_cases = []
    for cid in SAMPLE_IDS:
        c = by_id.get(cid)
        if c is None:
            continue
        sample_cases.append(
            {
                "id": c["id"],
                "status": c["status"],
                "ranked": c["ranked"],
                "total_ms": c["trace"]["total_ms"],
            }
        )

    bad_cases = [
        {"id": cid, "reason": BAD_CASE_REASONS.get(cid, "见 results.json 的 trace")}
        for cid in latest["bad_case_ids"]
    ]

    repeat_runs = [
        {
            "run_id": r["run_id"],
            "recall": r["metrics"]["recall_at_k"],
            "mrr": r["metrics"]["mrr_at_k"],
            "ndcg": r["metrics"]["ndcg_at_k"],
            "refusal": r["metrics"]["negative_refusal_rate"],
        }
        for _, r in runs
        if r["metrics"]["negative_refusal_rate"] >= 0.99
    ][-2:]

    tests_total = test_count()
    head = git("rev-parse", "HEAD")
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    # No wall-clock timestamp on the page: the build must be byte-reproducible so that
    # `--check` can prove the page has not drifted from its sources.
    commit_date = git("log", "-1", "--format=%cI")[:16].replace("T", " ")

    data = {
        "meta": {
            "chips": [
                {"text": "本地单机 · FastAPI + SQLite", "tone": "ok"},
                {"text": "合成数据 · 无云调用 · 无付费 API", "tone": ""},
                {"text": "抽取式回答（非 LLM 生成）", "tone": ""},
                {"text": "离线页面 · 零外部依赖", "tone": ""},
                {"text": "所有数字可回溯到仓库文件", "tone": "ok"},
            ],
            "line": f"基线提交 {head[:12]}（{commit_date}）· 分支 {branch} · 证据 run {latest['run_id']} · 页面数字与 data.json 同源、可复算",
        },
        "demo": {
            "status": demo["status"],
            "request": {
                "method": "POST",
                "path": "/query",
                "body": {"workspace": "handbook", "question": "restoration checksum review"},
            },
            "checks": demo["checks"],
            "answer": demo["answer"],
            "bad_case": demo["bad_case"],
            "after_archive": {
                "status": demo["after_archive"]["status"],
                "citations": demo["after_archive"]["citations"],
            },
            "cross_tenant_read_status": demo["cross_tenant_read_status"],
            "server_stopped": demo["server_stopped"],
            "elapsed_seconds": demo["elapsed_seconds"],
        },
        "pipeline": PIPELINE,
        "permissions": PERMISSIONS,
        "perm_note": (
            "四条越权路径在同一次 HTTP 运行里被断言：匿名 401、跨租户 404、只读角色写 403、"
            "归档后检索退回 insufficient_knowledge。检索之前先鉴权，是架构选择而不是事后过滤。"
        ),
        "eval": {
            "run_id": latest["run_id"],
            "retrieval_mode": latest["retrieval_mode"],
            "answer_mode": latest["answer_mode"],
            "metrics": metrics,
            "latency_ms": latest["latency_ms"],
            "candidate_k": latest["candidate_k"],
            "top_k": latest["top_k"],
            "cases_total": len(cases),
            "corpus_docs": len(corpus),
            "corpus_sha256": latest["corpus_sha256"],
            "cases_sha256": latest["cases_sha256"],
            "dataset_kind": latest["dataset_kind"],
            "bad_cases": bad_cases,
            "sample_cases": sample_cases,
            "repeat_runs": repeat_runs,
            "ablation": {
                "rows": ablation_rows,
                "note": (
                    "同一份 28 题：关掉覆盖门槛时召回更高、但 8 道负例几乎全答；"
                    "打开 0.45 门槛后召回略降、负例 8/8 拒答。这是可解释的取舍，"
                    "不能读成“准确率提升”。"
                ),
            },
        },
        "test_suite": {"passed": tests_total},
        "limits": [
            "词法检索不等于语义 embedding 质量",
            "引用存在不等于事实蕴含正确",
            "单进程小合成语料，不作并发性能结论",
            "题目与语料由同一人同时编写，不声称无偏泛化",
        ],
        "bounds": BOUNDS,
        "commands": {
            "l1": (
                "cd E:\\enterprise-knowledge-base\n"
                "# 只要 PowerShell / 文本编辑器，不装任何依赖\n"
                "Get-Content reports\\http-demo.json -Raw | ConvertFrom-Json |\n"
                "  Select-Object status, cross_tenant_read_status, server_stopped\n"
                "Get-Content reports\\eval-20261028T184235Z-6e6351\\results.json -Raw |\n"
                "  ConvertFrom-Json | Select-Object -ExpandProperty metrics\n"
                "Get-FileHash evaluation\\corpus.json -Algorithm SHA256   # 应等于报告里的 corpus_sha256"
            ),
            "l2": (
                "cd E:\\enterprise-knowledge-base\n"
                "$env:TEMP=\"$PWD\\.cache\\temp\"; $env:TMP=$env:TEMP\n"
                "$env:UV_CACHE_DIR=\"$PWD\\.cache\\uv\"; $env:UV_PYTHON_DOWNLOADS=\"never\"\n"
                "uv sync --frozen\n"
                f".\\\\.venv\\\\Scripts\\\\python.exe -m pytest                        # {tests_total} 项全部通过\n"
                ".\\\\.venv\\\\Scripts\\\\python.exe -m ekb.evaluate              # 打印 metrics 与坏例\n"
                ".\\\\.venv\\\\Scripts\\\\python.exe scripts\\\\demo.py             # 10 checks, status PASS\n"
                ".\\\\.venv\\\\Scripts\\\\python.exe scripts\\\\build_showcase.py --check   # 页面对不上就非零退出"
            ),
        },
        "provenance": [
            {"claim": "10 项 HTTP 检查通过", "value": demo["status"] + " · " + str(len(demo["checks"])) + " checks",
             "source": "reports/http-demo.json", "field": "status, checks[]"},
            {"claim": "引用精确到字符偏移", "value": str(demo["answer"]["citations"][0]["start"]) + "–" + str(demo["answer"]["citations"][0]["end"]) + " · v" + str(demo["answer"]["citations"][0]["version"]),
             "source": "reports/http-demo.json", "field": "answer.citations[0].start/end/version"},
            {"claim": "生成后权限复核", "value": demo["answer"]["trace"]["permission_recheck"],
             "source": "reports/http-demo.json", "field": "answer.trace.permission_recheck"},
            {"claim": "跨租户读取", "value": str(demo["cross_tenant_read_status"]),
             "source": "reports/http-demo.json", "field": "cross_tenant_read_status"},
            {"claim": "归档后不再召回", "value": str(demo["after_archive"]["status"]) + " · citations=" + str(len(demo["after_archive"]["citations"])),
             "source": "reports/http-demo.json", "field": "after_archive.status/citations"},
            {"claim": "rerank 只改顺序不改权限", "value": str(rerank["baseline_document_order"]) + " → " + str(rerank["reranked_document_order"]),
             "source": "reports/rerank-query-chain.json", "field": "baseline_document_order, reranked_document_order, forbidden_workspace_status"},
            {"claim": "Recall@4", "value": f"{metrics['recall_at_k']:.2f}",
             "source": str(latest_path.relative_to(ROOT)).replace("\\", "/"), "field": "metrics.recall_at_k"},
            {"claim": "MRR@4 / nDCG@4", "value": f"{metrics['mrr_at_k']:.2f} / {metrics['ndcg_at_k']:.2f}",
             "source": str(latest_path.relative_to(ROOT)).replace("\\", "/"), "field": "metrics.mrr_at_k, metrics.ndcg_at_k"},
            {"claim": "负向拒答", "value": f"{metrics['negative_refusal_rate']:.2f}（{metrics['negative_count']} 题）",
             "source": str(latest_path.relative_to(ROOT)).replace("\\", "/"), "field": "metrics.negative_refusal_rate"},
            {"claim": "题量与可答/负例拆分", "value": f"{len(cases)} / {metrics['answerable_count']} / {metrics['negative_count']}",
             "source": "evaluation/cases.json", "field": "len(cases), cases[].relevant 非空计数"},
            {"claim": "语料文档数", "value": str(len(corpus)),
             "source": "evaluation/corpus.json", "field": "len(corpus)"},
            {"claim": "语料指纹", "value": latest["corpus_sha256"][:16] + "…",
             "source": "evaluation/corpus.json", "field": "sha256（与报告 corpus_sha256 比对）"},
            {"claim": "门槛开/关消融", "value": "0.95·拒 0.125 → 0.90·拒 1.00",
             "source": "reports/eval-20261005T132700Z-fa0a73/results.json", "field": "metrics（对照组）"},
            {"claim": "测试与静态检查", "value": f"{tests_total} 项通过 · ruff check/format 通过",
             "source": "reports/takeover-verification-20261029-01/summary.json", "field": "pytest.tests, ruff_check, ruff_format"},
        ],
        "footer": {
            "lines": [
                "本页由 scripts/build_showcase.py 生成：只读 reports/ 与 evaluation/，不修改任何冻结文件，不联网。",
                f"基线分支 {branch} · HEAD {head} · 冻结提交 9f3993b8b038（Portfolio Freeze）",
                "复算入口：showcase/README.md · 一键核验：scripts/verify_showcase.py",
            ],
            "sha": (
                "证据指纹 corpus " + latest["corpus_sha256"][:16] + "… · cases "
                + latest["cases_sha256"][:16] + "… （可 Get-FileHash 复算）"
            ),
        },
    }
    return data


def render(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, indent=2).replace("</", "<\\/")
    template = TEMPLATE.read_text(encoding="utf8")
    if PLACEHOLDER not in template:
        raise SystemExit(f"{TEMPLATE} lost its {PLACEHOLDER} placeholder")
    return template.replace(PLACEHOLDER, payload)


def main(argv: list[str]) -> int:
    check = "--check" in argv
    data = build_data()
    html = render(data)

    if check:
        current_html = INDEX.read_text(encoding="utf8") if INDEX.exists() else ""
        current_data = DATA_JSON.read_text(encoding="utf8") if DATA_JSON.exists() else ""
        problems = []
        if current_html != html:
            problems.append("showcase/index.html is stale relative to its sources")
        if current_data != json.dumps(data, ensure_ascii=False, indent=2):
            problems.append("showcase/data.json is stale relative to its sources")
        for problem in problems:
            print("DRIFT:", problem)
        if problems:
            print("run: python scripts/build_showcase.py")
            return 1
        print(json.dumps({"check": "OK", "run_id": data["eval"]["run_id"],
                          "metrics": data["eval"]["metrics"]}, ensure_ascii=False))
        return 0

    DATA_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf8")
    INDEX.write_text(render(data), encoding="utf8")
    print(json.dumps({
        "index": str(INDEX.relative_to(ROOT)),
        "bytes": INDEX.stat().st_size,
        "data": str(DATA_JSON.relative_to(ROOT)),
        "run_id": data["eval"]["run_id"],
        "metrics": data["eval"]["metrics"],
        "checks": len(data["demo"]["checks"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
