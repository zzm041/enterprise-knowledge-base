"""Run the scripted assistant dialogues against the live stack and write a report.

This is the assistant layer's own regression bench: it drives real HTTP through the KB and
the real model gateway (no stubs), checks routing / citation / refusal contracts, and
records latency. It writes JSON so the numbers can be re-derived instead of quoted.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assistant.kb import KBClient  # noqa: E402
from assistant.llm import ChatModel  # noqa: E402
from assistant.service import Assistant  # noqa: E402
from assistant.settings import Settings  # noqa: E402
from assistant.store import Store  # noqa: E402


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(pct / 100 * (len(ordered) - 1)))))
    return round(ordered[index], 1)


def run_case(assistant: Assistant, case: dict) -> dict:
    session_id = None
    turns = []
    started = time.perf_counter()
    last = {}
    for text in case["turns"]:
        last = assistant.chat(case["identity"], text, session_id=session_id)
        session_id = last["session_id"]
        turns.append(
            {
                "text": text,
                "intent": last["intent"],
                "status": last["answer_status"],
                "standalone_question": last["standalone_question"],
                "citations": [c.get("title") for c in last.get("citations", [])],
                "latency_ms": last["latency_ms"],
                "reply": last["reply"],
            }
        )
    total_ms = round((time.perf_counter() - started) * 1000, 1)

    expect = case["expect"]
    statuses = expect.get("status") or []
    intents = expect.get("intents") or []
    cite_expect = expect.get("cite")
    got_cites = len(last.get("citations") or [])
    checks = {
        "status": (last["answer_status"] in statuses) if statuses else True,
        "intent": (last["intent"] in intents) if intents else True,
        "citation": (got_cites > 0) == cite_expect if cite_expect is not None else True,
        "rewrite": (last["standalone_question"] != case["turns"][-1])
        if expect.get("rewrite") else True,
    }
    return {
        "id": case["id"],
        "name": case["name"],
        "identity": case["identity"],
        "turns": turns,
        "expect": expect,
        "checks": checks,
        "passed": all(checks.values()),
        "session_id": session_id,
        "total_ms": total_ms,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True,
                        help="assistant config written by scripts/console.py")
    parser.add_argument("--out", default=None, help="report path (default: assistant/reports/eval-<stamp>.json)")
    parser.add_argument("--only", default=None, help="comma-separated case ids")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)

    settings = Settings.load(args.config)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = ROOT / ".cache" / f"eval-assistant-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    settings.database = str(run_dir / "assistant.db")

    store = Store(Path(settings.database))
    assistant = Assistant(
        store,
        KBClient(settings.kb.base_url, settings.kb.tokens),
        ChatModel(settings.llm.base_url, settings.llm.model, timeout=settings.llm.timeout),
        settings.assistant,
    )

    cases = json.loads((ROOT / "assistant" / "evalset.json").read_text(encoding="utf8"))["cases"]
    if args.only:
        wanted = {item.strip() for item in args.only.split(",")}
        cases = [case for case in cases if case["id"] in wanted]
    if args.limit:
        cases = cases[: args.limit]

    results = []
    for case in cases:
        try:
            result = run_case(assistant, case)
        except Exception as exc:  # noqa: BLE001 - a crashed case is a failed case, not a crash
            result = {"id": case["id"], "name": case["name"], "passed": False,
                      "error": f"{type(exc).__name__}: {exc}", "checks": {}}
        results.append(result)
        mark = "PASS" if result.get("passed") else "FAIL"
        detail = ""
        if result.get("turns"):
            last = result["turns"][-1]
            detail = f"{last['intent']}/{last['status']} cites={len(last['citations'])}"
        print(f"[{mark}] {result['id']:<18} {detail}", flush=True)

    passed = sum(1 for item in results if item.get("passed"))
    latencies = [t["latency_ms"] for item in results for t in item.get("turns", [])]
    stats = store.stats()
    summary = {
        "cases": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": round(passed / len(results), 4) if results else 0.0,
        "checks": {
            key: {
                "passed": sum(1 for item in results if item.get("checks", {}).get(key)),
                "total": sum(1 for item in results if key in item.get("checks", {})),
            }
            for key in ("status", "intent", "citation", "rewrite")
        },
        "latency_ms": {
            "median": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "max": round(max(latencies), 1) if latencies else 0.0,
            "samples": len(latencies),
        },
        "store": stats,
    }
    report = {
        "run_id": f"assistant-{stamp}",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stack": {
            "kb": settings.kb.base_url,
            "llm": {"base_url": settings.llm.base_url, "model": settings.llm.model},
            "workspace": settings.assistant.workspace,
            "identities": sorted(settings.kb.tokens),
        },
        "summary": summary,
        "results": results,
    }
    out = Path(args.out) if args.out else ROOT / "assistant" / "reports" / f"eval-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps({"report": str(out), **summary}, ensure_ascii=False), flush=True)
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
