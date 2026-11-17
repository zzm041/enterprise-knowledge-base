# -*- coding: utf-8 -*-
"""Make sure the local Ollama server runs with the slot count the demo needs.

Why this exists: with the default OLLAMA_NUM_PARALLEL=1 every request queues
behind the previous one. Measured on this machine (RTX 5070 Ti, qwen3:8b-q4km,
64 output tokens): 1.75 rps / 112 tok/s at any concurrency; with four slots the
same model does 6.0 rps / 384 tok/s. Nothing else in the demo changes — no code
path, no model — just this variable, so it is enforced before the console boots.

Usage:
  python scripts/ensure_ollama.py            # ensure (restarts a default server)
  python scripts/ensure_ollama.py --check    # report only, never touch anything
  python scripts/ensure_ollama.py --no-restart
  python scripts/ensure_ollama.py --warm qwen3:8b-q4km
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
STATE = RUNTIME / "ollama-serve-state.json"
LOG = RUNTIME / "ollama-serve.log"
HOST = os.environ.get("EKB_OLLAMA_HOST", "http://127.0.0.1:11434")
SLOTS = int(os.environ.get("EKB_OLLAMA_NUM_PARALLEL", "4"))


def reachable(timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(f"{HOST}/api/tags", timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def owner_pid(port: int = 11434) -> int | None:
    """PID listening on the port, via netstat (works without psutil)."""
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if f":{port} " in line and "LISTENING" in line:
            parts = line.split()
            if parts and parts[-1].isdigit():
                return int(parts[-1])
    return None


def recorded_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {}


def stop_server() -> list[str]:
    """Stop the tray app and the serve process; both are restarted by us."""
    steps = []
    for image in ("ollama app.exe", "ollama.exe"):
        proc = subprocess.run(["taskkill", "/IM", image, "/F"], capture_output=True, text=True)
        steps.append(f"taskkill {image}: rc={proc.returncode}")
    time.sleep(3)
    return steps


def start_server() -> subprocess.Popen:
    env = dict(os.environ)
    env["OLLAMA_NUM_PARALLEL"] = str(SLOTS)
    env.setdefault("OLLAMA_MAX_LOADED_MODELS", "1")
    env.setdefault("OLLAMA_KEEP_ALIVE", "30m")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log = open(LOG, "a", encoding="utf8")
    log.write(f"\n=== ensure_ollama start {time.strftime('%Y-%m-%dT%H:%M:%S')} slots={SLOTS}\n")
    log.flush()
    creation = 0
    if sys.platform == "win32":
        creation = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(["ollama", "serve"], env=env, stdout=log, stderr=log,
                            stdin=subprocess.DEVNULL, creationflags=creation, close_fds=True)


def wait_ready(seconds: float = 40.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if reachable():
            return True
        time.sleep(1.0)
    return False


def warm(model: str) -> bool:
    body = json.dumps({"model": model, "prompt": "hi", "stream": False}).encode("utf8")
    req = urllib.request.Request(f"{HOST}/api/generate", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report state, change nothing")
    ap.add_argument("--no-restart", action="store_true", help="refuse to restart a running server")
    ap.add_argument("--warm", default="", help="model to preload after the server is ready")
    args = ap.parse_args()

    if shutil.which("ollama") is None:
        print("[ollama] ollama not on PATH; start it yourself, demo will run at 1 slot")
        return 2

    state = recorded_state()
    up = reachable()
    pid = owner_pid() if up else None
    ours = bool(state.get("pid") and pid and int(state["pid"]) == int(pid))

    if args.check:
        print(json.dumps({"reachable": up, "pid": pid, "state": state,
                          "matches_our_state": ours, "wanted_slots": SLOTS},
                         ensure_ascii=False, indent=1))
        return 0

    if up and ours:
        print(f"[ollama] already serving with {state.get('num_parallel')} slots (pid {pid})")
    else:
        if up and args.no_restart:
            print(f"[ollama] a server is already running (pid {pid}) and --no-restart was given; "
                  f"leave it, but throughput may stay at 1 slot")
            return 0
        if up:
            print(f"[ollama] restarting the running server (pid {pid}) to set OLLAMA_NUM_PARALLEL={SLOTS}")
            for step in stop_server():
                print("  " + step)
        proc = start_server()
        if not wait_ready():
            print(f"[ollama] server did not become ready; see {LOG}")
            return 1
        state = {"pid": owner_pid(), "num_parallel": SLOTS, "host": HOST,
                 "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf8")
        print(f"[ollama] serving on {HOST} with OLLAMA_NUM_PARALLEL={SLOTS} (pid {state['pid']})")

    if args.warm:
        ok = warm(args.warm)
        print(f"[ollama] warm {args.warm}: {'ok' if ok else 'failed'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
