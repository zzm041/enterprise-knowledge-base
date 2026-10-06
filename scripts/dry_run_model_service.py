"""Exercise the runner's Windows lifecycle using an E-owned zero-model fixture."""

import json
import os
import sys
import time
from pathlib import Path

from scripts import run_independent_model_eval as runner


def main():
    root = Path(__file__).resolve().parents[1]
    if root != runner.ROOT:
        raise RuntimeError("PROJECT_ROOT_CHANGED")
    fixture = root / "tests/fixtures/fake_model_server.py"
    if not fixture.is_file():
        raise RuntimeError("FIXTURE_MISSING")
    if runner.listener_pids().get(runner.PORT):
        raise RuntimeError("OWN_PORT_OCCUPIED")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    state = root / "runtime/dry-run" / stamp
    state.mkdir(parents=True, exist_ok=False)
    (state / "models").mkdir()
    (root / ".cache/temp").mkdir(parents=True, exist_ok=True)
    runner.STATE = state
    runner.MODELS = state / "models"
    runner.OLLAMA = Path(sys._base_executable).resolve()
    runner.BASELINE_GPU_PIDS = runner.gpu_process_ids()
    baseline_foreign = {
        int(item["ProcessId"]) for item in runner.foreign_model_process()
    }
    command = [str(runner.OLLAMA), str(fixture), str(runner.PORT)]
    report = {
        "utc": runner.now(),
        "state": str(state),
        "real_inference_calls": 0,
        "fixture": str(fixture),
        "runs": [],
    }
    report_path = state / "report.json"
    try:
        for mode in ("slow_readiness", "root_crash", "interrupt"):
            receipt = None
            record = {"mode": mode}
            report["runs"].append(record)
            try:
                started = time.monotonic()
                receipt = runner.start_service(
                    command
                    + (["--delay-first-version"] if mode == "slow_readiness" else []),
                    allowed_foreign_pids=baseline_foreign,
                )
                record["startup_seconds"] = time.monotonic() - started
                record["pid"] = receipt["pid"]
                record["child_pids"] = [
                    item["pid"]
                    for item in receipt["tree"]
                    if item["pid"] != receipt["pid"]
                ]
                record["tree_verified"] = receipt["tree_verified"]
                record["listener"] = sorted(
                    runner.listener_pids().get(runner.PORT, set())
                )
                record["models"] = runner.local_get("/api/ps")["models"]
                if (
                    not record["tree_verified"]
                    or not record["child_pids"]
                    or record["listener"] != [receipt["pid"]]
                    or record["models"]
                ):
                    raise RuntimeError("FIXTURE_OBSERVATION_FAILED")
                if mode == "interrupt":
                    raise KeyboardInterrupt("CONTROLLED_FIXTURE_INTERRUPTION")
                elif mode == "root_crash":
                    record["post_ready_child_pid"] = runner.local_get("/spawn-child")[
                        "pid"
                    ]
                    if not runner.process_exists(record["post_ready_child_pid"]):
                        raise RuntimeError("POST_READY_CHILD_NOT_OBSERVED")
                    runner.verify_owned(receipt)
                    runner.output(["taskkill", "/PID", str(receipt["pid"]), "/F"], 20)
                    for _ in range(40):
                        if not runner.process_exists(receipt["pid"]):
                            break
                        time.sleep(0.25)
                    else:
                        raise RuntimeError("ROOT_CRASH_NOT_OBSERVED")
                    record["root_crash_observed"] = True
            finally:
                if receipt is not None:
                    record["release"] = runner.stop_owned(
                        receipt, allowed_foreign_pids=baseline_foreign
                    )
                    record["listener_after"] = sorted(
                        runner.listener_pids().get(runner.PORT, set())
                    )
                    if (
                        mode == "root_crash"
                        and record.get("post_ready_child_pid")
                        not in record["release"]["verified_children"]
                    ):
                        raise RuntimeError("POST_READY_CHILD_NOT_RELEASED")
                    if record["listener_after"]:
                        raise RuntimeError("OWN_LISTENER_AFTER_STOP")
                    stdout = Path(receipt["stdout_log"]).read_text(
                        encoding="utf8", errors="replace"
                    )
                    stderr = Path(receipt["stderr_log"]).read_text(
                        encoding="utf8", errors="replace"
                    )
                    record["stdout_observed"] = "fixture-ready" in stdout
                    record["gpu_probe_logged"] = (
                        'msg="discovering available GPUs...' in stderr
                        and 'msg="inference compute"' in stderr
                    )
                    record["gpu_probe_wait_seconds"] = receipt["readiness"][
                        "gpu_probe"
                    ]["wait_seconds"]
                    record["generation_requests_in_log"] = stdout.count('"POST ')
                    record["delayed_version_observed"] = (
                        "fixture-delaying-version" in stdout
                    )
                    if (
                        not record["stdout_observed"]
                        or not record["gpu_probe_logged"]
                        or record["generation_requests_in_log"]
                        or (
                            mode == "slow_readiness"
                            and (
                                not record["delayed_version_observed"]
                                or record["startup_seconds"] < 20
                            )
                        )
                    ):
                        raise RuntimeError("FIXTURE_LOG_OBSERVATION_FAILED")
            report_path.write_text(json.dumps(report, indent=2), encoding="utf8")
        raise RuntimeError("EXPECTED_INTERRUPTION_NOT_OBSERVED")
    except KeyboardInterrupt as exc:
        if (
            str(exc) != "CONTROLLED_FIXTURE_INTERRUPTION"
            or len(report["runs"]) != 3
            or any("release" not in run for run in report["runs"])
        ):
            report["status"] = "FAILED"
            report["reason"] = f"KeyboardInterrupt:{exc}"
            raise
        report["runs"][-1]["interruption_observed_after_cleanup"] = True
        report["status"] = "PASS_ZERO_INFERENCE"
        report["gpu_pids_after"] = sorted(runner.gpu_process_ids())
        report["baseline_gpu_pids"] = sorted(runner.BASELINE_GPU_PIDS)
        if set(report["gpu_pids_after"]) - runner.BASELINE_GPU_PIDS:
            raise RuntimeError("NEW_GPU_PROCESS_AFTER_DRY_RUN")
    except BaseException as exc:
        report["status"] = "FAILED"
        report["reason"] = f"{type(exc).__name__}:{exc}"
        raise
    finally:
        report_path.write_text(json.dumps(report, indent=2), encoding="utf8")
        print(str(report_path), flush=True)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    main()
