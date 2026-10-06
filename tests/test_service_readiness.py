"""Bounded, single-probe startup policy and evidence tests; no model is loaded."""

import json
import time
from pathlib import Path

import pytest

import scripts.run_independent_model_eval as runner


def test_readiness_policy_uses_e_config_and_caps_this_round_at_60(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(runner, "STATE", tmp_path)
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "startup_readiness_timeout_seconds": 60,
                "startup_connect_timeout_seconds": 2,
            }
        )
    )
    assert runner.load_startup_readiness_policy() == {
        "readiness_timeout_seconds": 60,
        "connect_timeout_seconds": 2,
    }
    config.write_text(
        json.dumps(
            {
                "startup_readiness_timeout_seconds": 61,
                "startup_connect_timeout_seconds": 2,
            }
        )
    )
    with pytest.raises(RuntimeError, match="STARTUP_READINESS_POLICY_INVALID"):
        runner.load_startup_readiness_policy()


def test_gpu_probe_wait_is_derived_from_ollama_stderr(tmp_path):
    log = tmp_path / "ollama.stderr.log"
    log.write_text(
        'time=2026-10-06T11:53:14.849+08:00 level=INFO msg="discovering available GPUs..."\n'
        'time=2026-10-06T11:53:19.399+08:00 level=INFO msg="inference compute"\n'
    )
    timing = runner.parse_gpu_probe_timing(log)
    assert timing["wait_seconds"] == 4.55
    assert timing["start_utc"] == "2026-10-06T03:53:14.849000+00:00"
    assert timing["end_utc"] == "2026-10-06T03:53:19.399000+00:00"
    assert timing["source"] == str(Path(log))
    log.write_text("fixture-ready\n")
    with pytest.raises(RuntimeError, match="GPU_PROBE_TIMING_UNOBSERVED"):
        runner.parse_gpu_probe_timing(log)


def _startup_fixture(monkeypatch, tmp_path, request_behavior):
    class Process:
        pid = 44100
        exit_code = None

        def poll(self):
            return self.exit_code

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"version": "0.34.4"}

    class Client:
        def __init__(self, **kwargs):
            calls["timeout"] = kwargs["timeout"]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, route):
            calls["version"] += 1
            assert route.endswith("/api/version")
            return request_behavior(process, clock, Response())

        def post(self, *args, **kwargs):
            calls["generation"] += 1
            raise AssertionError("generation before READY")

    state = tmp_path / "state"
    models = tmp_path / "models"
    ollama = tmp_path / "ollama.exe"
    models.mkdir()
    ollama.write_bytes(b"fixture")
    identity = {"pid": 44100, "path": str(ollama), "start": "2026-10-06T00:00:00Z"}
    process = Process()
    calls = {"version": 0, "generation": 0, "stopped": [], "timeout": None}
    clock = [0.0]
    calls["clock"] = clock
    monkeypatch.setattr(runner, "STATE", state)
    monkeypatch.setattr(runner, "MODELS", models)
    monkeypatch.setattr(runner, "OLLAMA", ollama)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(runner, "process_identity", lambda pid: identity)
    monkeypatch.setattr(runner, "listener_pids", lambda: {runner.PORT: {44100}})
    monkeypatch.setattr(runner, "_capture_tree", lambda receipt: [identity])
    monkeypatch.setattr(
        runner, "parse_gpu_probe_timing", lambda path: {"wait_seconds": 4.55}
    )
    monkeypatch.setattr(runner.httpx, "Client", Client)
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        runner,
        "stop_owned",
        lambda receipt, **kwargs: calls["stopped"].append(receipt.copy()),
    )
    return state, calls


def test_version_ready_after_old_20_seconds_within_60(monkeypatch, tmp_path):
    def response(process, clock, result):
        clock[0] = 25.0
        return result

    state, calls = _startup_fixture(monkeypatch, tmp_path, response)
    receipt = runner.start_service()
    evidence = json.loads((state / "services/current.json").read_text())
    assert receipt["readiness"]["status"] == "READY"
    assert evidence["readiness"]["total_elapsed_seconds"] == 25.0
    assert evidence["readiness"]["version_request_start_utc"]
    assert evidence["readiness"]["version_request_end_utc"]
    assert evidence["readiness"]["listener_ready_utc"]
    assert evidence["readiness"]["process_start_utc"]
    assert evidence["readiness"]["gpu_probe"]["wait_seconds"] == 4.55
    assert evidence["stdout_log"] and evidence["stderr_log"]
    assert 0 < calls["timeout"].connect <= 2
    assert 20 < calls["timeout"].read <= 60
    assert calls["version"] == 1
    assert calls["generation"] == 0
    assert calls["stopped"] == []


def test_version_after_60_seconds_fails_closed_and_cleans(monkeypatch, tmp_path):
    def response(process, clock, result):
        clock[0] = 61.0
        return result

    state, calls = _startup_fixture(monkeypatch, tmp_path, response)
    with pytest.raises(RuntimeError, match="OWN_SERVICE_START_TIMEOUT"):
        runner.start_service()
    evidence = json.loads((state / "services/current.json").read_text())
    assert evidence["readiness"]["status"] == "FAILED"
    assert "OWN_SERVICE_START_TIMEOUT" in evidence["readiness"]["failure_reason"]
    assert calls["version"] == 1
    assert calls["generation"] == 0
    assert len(calls["stopped"]) == 1
    assert calls["stopped"][0]["tree_verified"] is True


def test_drip_response_cannot_extend_overall_readiness_deadline(monkeypatch, tmp_path):
    def response(process, clock, result):
        # Mimics a server sending another byte before every per-read timeout.
        for _ in range(5):
            time.sleep(0.3)
        return result

    state, calls = _startup_fixture(monkeypatch, tmp_path, response)
    (state / "config.json").parent.mkdir(parents=True, exist_ok=True)
    (state / "config.json").write_text(
        json.dumps(
            {
                "startup_readiness_timeout_seconds": 1,
                "startup_connect_timeout_seconds": 0.2,
            }
        )
    )
    monkeypatch.setattr(runner.time, "monotonic", time.perf_counter)
    started = time.perf_counter()
    with pytest.raises(RuntimeError, match="OWN_SERVICE_START_TIMEOUT"):
        runner.start_service()
    elapsed = time.perf_counter() - started
    evidence = json.loads((state / "services/current.json").read_text())
    assert elapsed < 3
    assert evidence["readiness"]["status"] == "FAILED"
    assert (
        evidence["readiness"]["version_wait_elapsed_seconds"]
        < evidence["readiness"]["version_request_elapsed_seconds"]
    )
    assert calls["version"] == 1
    assert calls["generation"] == 0
    assert len(calls["stopped"]) == 1
    assert calls["stopped"][0]["tree_verified"] is True


def test_evidence_observation_past_deadline_cannot_publish_ready(monkeypatch, tmp_path):
    def response(process, clock, result):
        clock[0] = 59.0
        return result

    state, calls = _startup_fixture(monkeypatch, tmp_path, response)

    def slow_gpu_log(path):
        calls["gpu_log_reads"] = calls.get("gpu_log_reads", 0) + 1
        # Log parsing/receipt observation consumes the last second.
        calls["clock"][0] = 61.0
        return {"wait_seconds": 4.55}

    monkeypatch.setattr(runner, "parse_gpu_probe_timing", slow_gpu_log)
    with pytest.raises(RuntimeError, match="OWN_SERVICE_START_TIMEOUT"):
        runner.start_service()
    evidence = json.loads((state / "services/current.json").read_text())
    assert evidence["readiness"]["status"] == "FAILED"
    assert calls["version"] == 1
    assert calls["generation"] == 0
    assert len(calls["stopped"]) == 1


@pytest.mark.parametrize("fault", ["root_crash", "interrupt"])
def test_version_probe_fault_cleans_verified_tree(monkeypatch, tmp_path, fault):
    def response(process, clock, result):
        if fault == "root_crash":
            process.exit_code = 17
            return result
        raise KeyboardInterrupt("simulated interruption")

    state, calls = _startup_fixture(monkeypatch, tmp_path, response)
    expected = RuntimeError if fault == "root_crash" else KeyboardInterrupt
    with pytest.raises(expected):
        runner.start_service()
    evidence = json.loads((state / "services/current.json").read_text())
    assert evidence["readiness"]["status"] == "FAILED"
    assert evidence["readiness"]["version_request_end_utc"]
    assert calls["version"] == 1
    assert calls["generation"] == 0
    assert len(calls["stopped"]) == 1
    assert calls["stopped"][0]["tree_verified"] is True


def test_cleanup_failure_keeps_failure_and_never_readies(monkeypatch, tmp_path):
    def response(process, clock, result):
        raise runner.httpx.ReadTimeout("probe timed out")

    state, calls = _startup_fixture(monkeypatch, tmp_path, response)
    monkeypatch.setattr(
        runner,
        "stop_owned",
        lambda receipt, **kwargs: (_ for _ in ()).throw(
            RuntimeError("CLEANUP_UNCONFIRMED")
        ),
    )
    with pytest.raises(RuntimeError, match="CLEANUP_FAILED.*CLEANUP_UNCONFIRMED"):
        runner.start_service()
    evidence = json.loads((state / "services/current.json").read_text())
    assert evidence["readiness"]["status"] == "FAILED"
    assert calls["version"] == 1
    assert calls["generation"] == 0


def test_generation_entry_rejects_listener_without_version_ready(monkeypatch):
    monkeypatch.setattr(
        runner,
        "preflight",
        lambda *args: (_ for _ in ()).throw(AssertionError("preflight too late")),
    )
    with pytest.raises(RuntimeError, match="OWN_SERVICE_VERSION_NOT_READY"):
        runner.one_call(
            {}, "digest", {"pid": 44100, "readiness": {"status": "STARTING"}}, None
        )
