"""Generation-only microbenchmark: how much of the EKB ceiling is the model?

Hits the ollama chat endpoint directly (no retrieval, no rerank, no EKB), so the
numbers answer one question: at 64 output tokens, what throughput can the
serving stack deliver for a given model / parallelism setting?

Usage:
  python scripts/perf/bench_gen.py --model qwen3:8b-q4km --levels 1,4,8,16 --duration 15
"""
from __future__ import annotations

import argparse, json, statistics, threading, time, uuid
from pathlib import Path

import httpx

def pct(xs, q):
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round(q * (len(xs) - 1)))))
    return round(xs[k], 1)

def one(client, base, model, i, tokens, results, lock):
    prompt = f"[{uuid.uuid4().hex[:8]}] 用一句话说明物业报修工单的派单规则。"
    t0 = time.perf_counter()
    try:
        r = client.post(f"{base}/v1/chat/completions", json={
            "model": model, "stream": False, "max_tokens": tokens,
            "messages": [{"role": "user", "content": prompt}],
        })
        el = (time.perf_counter() - t0) * 1000
        ok = r.status_code == 200
        usage = r.json().get("usage", {}) if ok else {}
    except Exception:
        el, ok, usage = (time.perf_counter() - t0) * 1000, False, {}
    with lock:
        results.append((el, ok, usage.get("completion_tokens", 0)))

def run(base, model, level, duration, tokens, warmup):
    client = httpx.Client(timeout=180.0)
    if warmup:
        one(client, base, model, 0, tokens, [], threading.Lock())
    results, lock, stop = [], threading.Lock(), False
    def worker():
        while not stop:
            one(client, base, model, 0, tokens, results, lock)
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(level)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    time.sleep(duration)
    stop = True
    for t in threads:
        t.join(timeout=60)
    el = time.perf_counter() - t0
    lats = [r[0] for r in results]
    oks = [r for r in results if r[1]]
    toks = sum(r[2] for r in oks)
    client.close()
    return {
        "model": model, "concurrency": level, "requests": len(lats),
        "rps": round(len(lats) / el, 3), "ok": len(oks),
        "errors": len(lats) - len(oks),
        "client_p50_ms": pct(lats, 0.50), "client_p95_ms": pct(lats, 0.95),
        "client_p99_ms": pct(lats, 0.99),
        "tokens_per_s": round(toks / el, 1),
        "max_tokens": tokens,
    }

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:11434")
    ap.add_argument("--model", required=True)
    ap.add_argument("--levels", default="1,4,8,16")
    ap.add_argument("--duration", type=float, default=15.0)
    ap.add_argument("--warmup", type=float, default=5.0)
    ap.add_argument("--tokens", type=int, default=64)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    rows = []
    for level in [int(x) for x in args.levels.split(",")]:
        row = run(args.base, args.model, level, args.duration, args.tokens, args.warmup)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        rows.append(row)
    if args.out:
        Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf8")
        print("written", args.out)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
