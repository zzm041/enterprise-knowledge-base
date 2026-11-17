
"""Load test for the frozen EKB API: concurrency gradient over POST /query.

Measures end-to-end latency (client side) and server-reported trace.total_ms at
1/2/4/8/16 concurrent workers, for a fixed question set, and reports throughput,
p50/p95/p99 and the error mix. Read-only: it only asks questions.
"""
from __future__ import annotations

import argparse, json, statistics, sys, threading, time
from pathlib import Path

import httpx

import os

ROOT = Path(__file__).resolve().parents[2]
NONCE = os.environ.get("EKB_NONCE") == "1"
CREDS = ROOT / "runtime/console-live/demo-credentials.json"

# Questions chosen so that every request takes the "answered" path (rerank +
# generation both actually run); otherwise insufficient_knowledge short-circuits
# and the latency distribution mixes a fast path with the real one.
# 2026-11-18: verified one by one against --mode bm25; only the questions that
# actually return status=answered are kept. The previous list mixed in questions
# that short-circuited on the 0.45 coverage gate (~70-95 ms each), which inflated
# throughput by roughly 2x and hid what the model was really doing.
QUESTIONS = [
    "入职当天在哪里领取办公电脑",
    "出差住宿每晚上限多少",
    "竞业限制补偿按什么标准支付",
    "停工留薪期一般多长",
    "采购一万元以上要几家比价",
    "备份恢复演练多久一次",
    "门禁权限怎么申请",
]


def pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round(q * (len(xs) - 1)))))
    return round(xs[k], 1)


def run_level(base, token, workspace, mode, concurrency, duration, repeat):
    lat, srv, codes = [], [], {}
    lock = threading.Lock()
    stop_at = time.perf_counter() + duration
    counter = {"i": 0}

    def worker():
        local_lat, local_srv, local_codes = [], [], {}
        with httpx.Client(timeout=120.0, headers={"Authorization": f"Bearer {token}"}) as c:
            while time.perf_counter() < stop_at:
                with lock:
                    i = counter["i"]; counter["i"] += 1
                q = QUESTIONS[i % len(QUESTIONS)] if repeat else QUESTIONS[i % len(QUESTIONS)]
                if NONCE:
                    # Ollama reuses its prompt cache for an identical prompt, which makes a
                    # repeated-question load test measure the cache instead of the model.
                    # A unique suffix forces a real prefill+decode every request while the
                    # retrieval terms stay intact.
                    q = q + "\u200b" * (1 + (i % 7))
                t0 = time.perf_counter()
                try:
                    r = c.post(f"{base}/query", json={"workspace": workspace, "question": q, "mode": mode})
                    dt = (time.perf_counter() - t0) * 1000
                    local_lat.append(dt)
                    key = r.status_code
                    local_codes[key] = local_codes.get(key, 0) + 1
                    xc = r.headers.get("x-cache")
                    if xc:
                        local_codes["cache_" + xc] = local_codes.get("cache_" + xc, 0) + 1
                    if r.status_code == 200:
                        body = r.json()
                        tm = (body.get("trace") or {}).get("total_ms")
                        if tm is not None:
                            local_srv.append(tm)
                except Exception as exc:  # noqa: BLE001
                    local_codes[type(exc).__name__] = local_codes.get(type(exc).__name__, 0) + 1
        with lock:
            lat.extend(local_lat); srv.extend(local_srv)
            for k, v in local_codes.items():
                codes[k] = codes.get(k, 0) + v

    threads = [threading.Thread(target=worker) for _ in range(concurrency)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - t0
    n = len(lat)
    return {
        "concurrency": concurrency,
        "requests": n,
        "rps": round(n / wall, 3) if wall else None,
        "client_p50_ms": pct(lat, 0.50),
        "client_p95_ms": pct(lat, 0.95),
        "client_p99_ms": pct(lat, 0.99),
        "client_max_ms": round(max(lat), 1) if lat else None,
        "server_p50_ms": pct(srv, 0.50),
        "server_p95_ms": pct(srv, 0.95),
        "errors": {str(k): v for k, v in codes.items() if str(k) != "200"},
        "ok": codes.get(200, 0),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:53122")
    ap.add_argument("--workspace", default="handbook")
    ap.add_argument("--mode", default="bm25")
    ap.add_argument("--identity", default="reader")
    ap.add_argument("--levels", default="1,2,4,8,16")
    ap.add_argument("--duration", type=float, default=20.0)
    ap.add_argument("--warmup", type=float, default=3.0)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    token = json.loads(CREDS.read_text(encoding="utf8"))[args.identity]
    with httpx.Client(timeout=120.0, headers={"Authorization": f"Bearer {token}"}) as c:
        r = c.post(f"{args.base}/query", json={"workspace": args.workspace, "question": QUESTIONS[0], "mode": args.mode})
        assert r.status_code == 200, (r.status_code, r.text[:200])
        print("warmup ok:", json.dumps({k: r.json()["trace"].get(k) for k in ("retrieval_mode", "rerank", "eligible_chunks", "total_ms")}, ensure_ascii=False))
    time.sleep(args.warmup)

    rows = []
    for level in [int(x) for x in args.levels.split(",")]:
        row = run_level(args.base, token, args.workspace, args.mode, level, args.duration, True)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
    if args.out:
        Path(args.out).write_text(json.dumps({"base": args.base, "mode": args.mode, "workspace": args.workspace, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf8")
        print("written", args.out)


if __name__ == "__main__":
    main()
