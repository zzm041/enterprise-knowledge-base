#!/usr/bin/env python
"""Re-run every claim on showcase/index.html and fail loudly on any drift.

What it does, in order:
  1. pytest            -> test suite still green (count printed, compared to the page)
  2. ekb.evaluate      -> 28-case metrics recomputed in an isolated database
  3. scripts/demo.py   -> the live HTTP demo, 10 checks including 404/403/401
  4. build_showcase --check -> the page still matches the files it cites

Frozen evidence is protected: reports/latest-evaluation.txt and reports/http-demo.json are
restored byte for byte afterwards, and the evaluation run directory created by step 2 is
moved into .cache/showcase-run/ instead of being left in reports/.

Usage: python scripts/verify_showcase.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
RUN_ROOT = ROOT / ".cache" / "showcase-run"
LATEST_TXT = ROOT / "reports" / "latest-evaluation.txt"
DEMO_JSON = ROOT / "reports" / "http-demo.json"
DATA_JSON = ROOT / "showcase" / "data.json"


def env() -> dict:
    e = dict(os.environ)
    e["TEMP"] = str(ROOT / ".cache" / "temp")
    e["TMP"] = e["TEMP"]
    e["UV_CACHE_DIR"] = str(ROOT / ".cache" / "uv")
    e["UV_PYTHON_DOWNLOADS"] = "never"
    Path(e["TEMP"]).mkdir(parents=True, exist_ok=True)
    return e


def run(args: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, env=env(), timeout=timeout)


def main() -> int:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    out_dir = RUN_ROOT / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    page = json.loads(DATA_JSON.read_text(encoding="utf8"))
    results: list[dict] = []
    ok = True

    # 1. test suite. pyproject already sets -q, so a second -q suppresses the summary
    # line; junit xml is the unambiguous source of truth for the counts.
    junit = out_dir / "junit.xml"
    proc = run([str(PY), "-m", "pytest", f"--junit-xml={junit}"], timeout=1800)
    text = proc.stdout + proc.stderr
    passed = failed = 0
    if junit.exists():
        # "<testsuite\s" deliberately skips the outer "<testsuites>" element, whose
        # attributes are the ones that carry no counts.
        suite = re.search(r"<testsuite\s[^>]*>", junit.read_text(encoding="utf8"))
        if suite:
            attrs = dict(re.findall(r'(\w+)="([^"]*)"', suite.group(0)))
            tests = int(attrs.get("tests", "0"))
            bad = int(attrs.get("failures", "0")) + int(attrs.get("errors", "0"))
            passed, failed = tests - bad - int(attrs.get("skipped", "0")), bad
    if passed == 0:
        passed = int((re.search(r"(\d+) passed", text) or [0, 0])[1] or 0)
        failed = int((re.search(r"(\d+) failed", text) or [0, 0])[1] or 0)
    expected = page["test_suite"]["passed"]
    results.append({"step": "pytest", "ok": proc.returncode == 0 and failed == 0 and passed >= expected,
                    "detail": f"{passed} passed, {failed} failed (page says {expected})"})
    ok &= proc.returncode == 0 and failed == 0 and passed >= expected
    (out_dir / "pytest.txt").write_text(text[-4000:], encoding="utf8")

    # 2. evaluation, with the frozen pointer restored afterwards
    saved_pointer = LATEST_TXT.read_bytes() if LATEST_TXT.exists() else None
    proc = run([str(PY), "-m", "ekb.evaluate"], timeout=1800)
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip().startswith("{")]
    measured = None
    if proc.returncode == 0 and lines:
        try:
            payload = json.loads(lines[-1])
            report_path = Path(payload["report"])
            measured = payload["metrics"]
            if report_path.exists():
                shutil.copy2(report_path, out_dir / "eval-results.json")
                shutil.rmtree(report_path.parent, ignore_errors=True)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            results.append({"step": "evaluate", "ok": False, "detail": f"cannot parse output: {exc}"})
            ok = False
    if saved_pointer is not None:
        LATEST_TXT.write_bytes(saved_pointer)
    matches = measured == page["eval"]["metrics"]
    results.append({"step": "evaluate", "ok": measured is not None and matches,
                    "detail": f"metrics={json.dumps(measured, ensure_ascii=False)} · page_match={matches}"})
    ok &= measured is not None and matches

    # 3. live HTTP demo, with the frozen report restored afterwards
    saved_demo = DEMO_JSON.read_bytes() if DEMO_JSON.exists() else None
    proc = run([str(PY), str(ROOT / "scripts" / "demo.py")], timeout=900)
    demo_checks = 0
    demo_status = "?"
    if DEMO_JSON.exists():
        fresh = json.loads(DEMO_JSON.read_text(encoding="utf8"))
        demo_status = fresh.get("status", "?")
        demo_checks = len(fresh.get("checks", []))
        shutil.copy2(DEMO_JSON, out_dir / "http-demo.json")
    if saved_demo is not None:
        DEMO_JSON.write_bytes(saved_demo)
    demo_ok = proc.returncode == 0 and demo_status == "PASS" and demo_checks == len(page["demo"]["checks"])
    results.append({"step": "demo", "ok": demo_ok,
                    "detail": f"status={demo_status} checks={demo_checks} (page says {len(page['demo']['checks'])}), exit={proc.returncode}"})
    ok &= demo_ok

    # 4. page still matches its sources
    proc = run([str(PY), str(ROOT / "scripts" / "build_showcase.py"), "--check"], timeout=300)
    results.append({"step": "page-drift", "ok": proc.returncode == 0,
                    "detail": (proc.stdout + proc.stderr).strip()[:400]})
    ok &= proc.returncode == 0

    summary = {"ok": ok, "at": stamp, "steps": results}
    (out_dir / "verify.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf8")

    print(f"{'STEP':<12} {'RESULT':<7} DETAIL")
    for r in results:
        print(f"{r['step']:<12} {'PASS' if r['ok'] else 'FAIL':<7} {r['detail']}")
    print(f"\n{'ALL CHECKS PASS' if ok else 'DRIFT OR FAILURE — see ' + str(out_dir)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
