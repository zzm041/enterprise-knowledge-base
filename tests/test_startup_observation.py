"""No-model replay of the first run's startup and cleanup failure path."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import scripts.run_independent_model_eval as runtime


def test_observation_failure_keeps_command_identity(monkeypatch):
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    with pytest.raises(RuntimeError, match="netstat.*exit=1"):
        runtime.output(["netstat", "-ano", "-p", "TCP"])


def test_ready_observation_failure_and_cleanup_failure_preserve_both(
    monkeypatch, tmp_path
):
    class FakeProcess:
        pid = 44001

        def poll(self):
            return None

    monkeypatch.setattr(runtime, "STATE", tmp_path / "state")
    monkeypatch.setattr(runtime, "MODELS", tmp_path / "models")
    monkeypatch.setattr(runtime, "OLLAMA", tmp_path / "ollama.exe")
    runtime.MODELS.mkdir()
    runtime.OLLAMA.write_bytes(b"fixture")
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *a, **k: FakeProcess())
    monkeypatch.setattr(
        runtime,
        "process_identity",
        lambda pid: {"pid": pid, "path": str(runtime.OLLAMA), "start": "pinned"},
    )
    monkeypatch.setattr(
        runtime,
        "listener_pids",
        lambda: (_ for _ in ()).throw(RuntimeError("NETSTAT_READY_EXIT_1")),
    )
    monkeypatch.setattr(
        runtime,
        "stop_owned",
        lambda receipt, **kwargs: (_ for _ in ()).throw(
            RuntimeError("CLEANUP_OBSERVATION_EXIT_1")
        ),
    )
    with pytest.raises(RuntimeError) as error:
        runtime.start_service()
    assert "NETSTAT_READY_EXIT_1" in str(error.value)
    assert "CLEANUP_OBSERVATION_EXIT_1" in str(error.value)
    assert (runtime.STATE / "services/current.json").exists()


def test_version_health_allows_gpu_discovery_wait_with_one_get(monkeypatch, tmp_path):
    class FakeProcess:
        pid = 44015

        def poll(self):
            return None

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"version": "0.34.4"}

    class Client:
        def __init__(self, timeout):
            timeouts.append(timeout)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, route):
            calls.append(route)
            return Response()

    calls = []
    timeouts = []
    identity = {
        "pid": 44015,
        "path": str(tmp_path / "ollama.exe"),
        "start": "2026-10-06T00:00:00Z",
    }
    monkeypatch.setattr(runtime, "STATE", tmp_path / "state")
    monkeypatch.setattr(runtime, "MODELS", tmp_path / "models")
    monkeypatch.setattr(runtime, "OLLAMA", tmp_path / "ollama.exe")
    runtime.MODELS.mkdir()
    runtime.OLLAMA.write_bytes(b"fixture")
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *a, **k: FakeProcess())
    monkeypatch.setattr(runtime, "process_identity", lambda pid: identity)
    monkeypatch.setattr(runtime, "listener_pids", lambda: {runtime.PORT: {44015}})
    monkeypatch.setattr(runtime, "_capture_tree", lambda receipt: [identity])
    monkeypatch.setattr(
        runtime, "parse_gpu_probe_timing", lambda path: {"wait_seconds": 4.55}
    )
    monkeypatch.setattr(
        runtime.httpx, "Client", lambda **kwargs: Client(kwargs["timeout"])
    )
    monkeypatch.setattr(runtime, "stop_owned", lambda *a, **k: None)
    receipt = runtime.start_service()
    assert receipt["tree_verified"] is True
    assert len(calls) == 1
    assert 0 < timeouts[0].connect <= 2
    assert 20 < timeouts[0].read <= 60
    assert all(route.endswith("/api/version") for route in calls)


def test_version_read_timeout_stops_immediately_and_cleans_up(monkeypatch, tmp_path):
    class FakeProcess:
        pid = 44016

        def poll(self):
            return None

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, route):
            calls.append(route)
            raise runtime.httpx.ReadTimeout("GPU discovery still running")

    calls = []
    stopped = []
    identity = {
        "pid": 44016,
        "path": str(tmp_path / "ollama.exe"),
        "start": "2026-10-06T00:00:00Z",
    }
    monkeypatch.setattr(runtime, "STATE", tmp_path / "state")
    monkeypatch.setattr(runtime, "MODELS", tmp_path / "models")
    monkeypatch.setattr(runtime, "OLLAMA", tmp_path / "ollama.exe")
    runtime.MODELS.mkdir()
    runtime.OLLAMA.write_bytes(b"fixture")
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *a, **k: FakeProcess())
    monkeypatch.setattr(runtime, "process_identity", lambda pid: identity)
    monkeypatch.setattr(runtime, "listener_pids", lambda: {runtime.PORT: {44016}})
    monkeypatch.setattr(runtime, "_capture_tree", lambda receipt: [identity])
    monkeypatch.setattr(runtime.httpx, "Client", lambda **kwargs: Client())
    monkeypatch.setattr(
        runtime, "stop_owned", lambda receipt, **kwargs: stopped.append(receipt["pid"])
    )
    with pytest.raises(runtime.httpx.ReadTimeout):
        runtime.start_service()
    assert stopped == [44016]
    assert len(calls) == 1
    assert all(route.endswith("/api/version") for route in calls)


def test_receipt_write_interruption_stops_owned_process(monkeypatch, tmp_path):
    class FakeProcess:
        pid = 44002

        def poll(self):
            return None

    monkeypatch.setattr(runtime, "STATE", tmp_path / "state")
    monkeypatch.setattr(runtime, "MODELS", tmp_path / "models")
    monkeypatch.setattr(runtime, "OLLAMA", tmp_path / "ollama.exe")
    runtime.MODELS.mkdir()
    runtime.OLLAMA.write_bytes(b"fixture")
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *a, **k: FakeProcess())
    monkeypatch.setattr(
        runtime,
        "process_identity",
        lambda pid: {"pid": pid, "path": str(runtime.OLLAMA), "start": "pinned"},
    )
    stopped = []
    monkeypatch.setattr(
        runtime, "stop_owned", lambda receipt, **kwargs: stopped.append(receipt["pid"])
    )
    monkeypatch.setattr(
        runtime, "save_new", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    with pytest.raises(RuntimeError, match="EVIDENCE_FAILED:KeyboardInterrupt"):
        runtime.start_service()
    assert stopped == [44002]


def test_early_exit_before_receipt_never_claims_descendant_release(
    monkeypatch, tmp_path
):
    class ExitedProcess:
        pid = 44012

        def poll(self):
            return 1

    monkeypatch.setattr(runtime, "STATE", tmp_path / "state")
    monkeypatch.setattr(runtime, "MODELS", tmp_path / "models")
    monkeypatch.setattr(runtime, "OLLAMA", tmp_path / "ollama.exe")
    runtime.MODELS.mkdir()
    runtime.OLLAMA.write_bytes(b"fixture")
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *a, **k: ExitedProcess())
    monkeypatch.setattr(
        runtime,
        "process_identity",
        lambda pid: (_ for _ in ()).throw(RuntimeError("PROCESS_IDENTITY_FAILED")),
    )
    with pytest.raises(RuntimeError, match="STARTUP_DESCENDANTS_UNBOUND"):
        runtime.start_service()


def test_process_observation_failure_never_means_exited(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "process_identity",
        lambda pid: (_ for _ in ()).throw(
            RuntimeError("OBSERVATION_TIMEOUT:powershell")
        ),
    )
    monkeypatch.setattr(
        runtime,
        "process_exists",
        lambda pid: (_ for _ in ()).throw(
            RuntimeError("OBSERVATION_TIMEOUT:powershell")
        ),
    )
    with pytest.raises(RuntimeError, match="OBSERVATION_TIMEOUT:powershell"):
        runtime.stop_owned({"pid": 44003})


def test_startup_command_is_injectable_for_no_model_fixture():
    import inspect

    assert "command" in inspect.signature(runtime.start_service).parameters


def test_crashed_root_kills_only_recorded_child(monkeypatch):
    child = {
        "pid": 44005,
        "path": "E:/enterprise-knowledge-base/.venv/Scripts/python.exe",
        "start": "child-start",
    }
    receipt = {
        "pid": 44004,
        "tree": [child],
        "tree_verified": True,
        "start": "2026-10-05T00:00:00Z",
        "executable": child["path"],
    }
    alive = {44004: False, 44005: True}
    monkeypatch.setattr(
        runtime,
        "verify_owned",
        lambda r: (_ for _ in ()).throw(RuntimeError("ROOT_ABSENT")),
    )
    monkeypatch.setattr(runtime, "process_exists", lambda pid: alive.get(pid, False))
    monkeypatch.setattr(runtime, "process_identity", lambda pid: child)
    monkeypatch.setattr(runtime, "listener_pids", lambda: {})
    monkeypatch.setattr(runtime, "owned_tree_pids", lambda pid: [44004, 44005])
    monkeypatch.setattr(runtime, "foreign_model_process", lambda: [])
    monkeypatch.setattr(runtime, "gpu", lambda: {"free_mib": 12000})
    monkeypatch.setattr(runtime, "assert_exclusive_gpu", lambda: None)
    killed = []

    def fake_output(args, timeout=5):
        if args[0] == "taskkill":
            killed.append(int(args[2]))
            alive[int(args[2])] = False
        return ""

    monkeypatch.setattr(runtime, "output", fake_output)
    result = runtime.stop_owned(receipt)
    assert result["status"] == "GPU_RELEASE_CONFIRMED"
    assert killed == [44005]


def test_crashed_root_refuses_reused_child_pid(monkeypatch):
    child = {
        "pid": 44007,
        "path": "E:/enterprise-knowledge-base/.venv/Scripts/python.exe",
        "start": "old-start",
    }
    receipt = {
        "pid": 44006,
        "tree": [child],
        "tree_verified": True,
        "start": "2026-10-05T00:00:00Z",
        "executable": child["path"],
    }
    monkeypatch.setattr(
        runtime,
        "verify_owned",
        lambda r: (_ for _ in ()).throw(RuntimeError("ROOT_ABSENT")),
    )
    monkeypatch.setattr(runtime, "process_exists", lambda pid: pid == 44007)
    monkeypatch.setattr(runtime, "listener_pids", lambda: {})
    monkeypatch.setattr(runtime, "owned_tree_pids", lambda pid: [44006, 44007])
    monkeypatch.setattr(
        runtime, "process_identity", lambda pid: {**child, "start": "new-start"}
    )
    killed = []
    monkeypatch.setattr(runtime, "output", lambda args, timeout=5: killed.append(args))
    with pytest.raises(RuntimeError, match="CHILD_IDENTITY_CHANGED"):
        runtime.stop_owned(receipt)
    assert killed == []


def test_crashed_root_discovers_child_started_after_ready(monkeypatch):
    root = 44010
    child = 44011
    executable = "E:/enterprise-knowledge-base/.venv/Scripts/python.exe"
    observed = {
        "pid": child,
        "path": executable,
        "start": "2026-10-05T00:00:01Z",
    }
    receipt = {
        "pid": root,
        "start": "2026-10-05T00:00:00Z",
        "executable": executable,
        "tree": [],
        "tree_verified": True,
    }
    alive = {root: False, child: True}
    monkeypatch.setattr(
        runtime,
        "verify_owned",
        lambda r: (_ for _ in ()).throw(RuntimeError("ROOT_ABSENT")),
    )
    monkeypatch.setattr(runtime, "process_exists", lambda pid: alive.get(pid, False))
    monkeypatch.setattr(runtime, "process_identity", lambda pid: observed)
    monkeypatch.setattr(runtime, "owned_tree_pids", lambda pid: [root, child])
    monkeypatch.setattr(runtime, "listener_pids", lambda: {})
    monkeypatch.setattr(runtime, "foreign_model_process", lambda: [])
    monkeypatch.setattr(runtime, "gpu", lambda: {"free_mib": 12000})
    monkeypatch.setattr(runtime, "assert_exclusive_gpu", lambda: None)
    killed = []

    def fake_output(args, timeout=5):
        if args[0] == "taskkill":
            killed.append(int(args[2]))
            alive[int(args[2])] = False
        return ""

    monkeypatch.setattr(runtime, "output", fake_output)
    release = runtime.stop_owned(receipt)
    assert release["verified_children"] == [child]
    assert killed == [child]


def test_retire_absent_receipt_preserves_history(monkeypatch, tmp_path):
    logs = tmp_path / "services"
    logs.mkdir()
    original = {"pid": 44008, "tree": [{"pid": 44008}], "tree_verified": True}
    (logs / "current.json").write_text(runtime.json.dumps(original))
    monkeypatch.setattr(runtime, "process_exists", lambda pid: False)
    monkeypatch.setattr(runtime, "listener_pids", lambda: {})
    runtime.retire_absent_receipt(logs)
    assert not (logs / "current.json").exists()
    archived = list(logs.glob("retired-*.json"))
    assert len(archived) == 1
    assert runtime.json.loads(archived[0].read_text()) == original


def test_retire_refuses_live_or_unbound_prior_receipt(monkeypatch, tmp_path):
    logs = tmp_path / "services"
    logs.mkdir()
    current = logs / "current.json"
    current.write_text(runtime.json.dumps({"pid": 44009, "tree_verified": True}))
    monkeypatch.setattr(runtime, "process_exists", lambda pid: True)
    with pytest.raises(RuntimeError, match="PRIOR_SERVICE_STATE_UNRESOLVED"):
        runtime.retire_absent_receipt(logs)
    monkeypatch.setattr(runtime, "process_exists", lambda pid: False)
    monkeypatch.setattr(runtime, "listener_pids", lambda: {})
    current.write_text(runtime.json.dumps({"pid": 44009, "tree_verified": False}))
    with pytest.raises(RuntimeError, match="PRIOR_SERVICE_DESCENDANTS_UNBOUND"):
        runtime.retire_absent_receipt(logs)
    assert current.exists()


def test_fresh_window_requires_new_explicit_file(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "ROOT", tmp_path)
    monkeypatch.setattr(runtime, "STATE", tmp_path / "runtime")
    runtime.STATE.mkdir()
    (runtime.STATE / "window.json").write_text(
        runtime.json.dumps({"recorded_utc": "2026-10-05T15:34:03Z"})
    )
    path = tmp_path / "new-window.json"
    path.write_text(
        runtime.json.dumps(
            {
                "established": True,
                "source": "new owner confirmation",
                "established_at_utc": "2026-10-06T00:00:00Z",
            }
        )
    )
    with pytest.raises(RuntimeError, match="WINDOW_CONFIRMATION_STALE"):
        runtime.load_fresh_window_confirmation(
            path, datetime(2026, 10, 6, 2, 0, tzinfo=UTC)
        )
    assert runtime.load_fresh_window_confirmation(
        path, datetime(2026, 10, 6, 0, 1, tzinfo=UTC)
    )["established"]
    path.write_text(
        runtime.json.dumps(
            {
                "established": True,
                "source": "old owner confirmation",
                "established_at_utc": "2026-10-05T15:30:00Z",
            }
        )
    )
    with pytest.raises(RuntimeError, match="WINDOW_CONFIRMATION_NOT_NEW"):
        runtime.load_fresh_window_confirmation(
            path, datetime(2026, 10, 5, 15, 31, tzinfo=UTC)
        )


def test_explicit_owner_renewal_keeps_original_start_and_expires(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "ROOT", tmp_path)
    monkeypatch.setattr(runtime, "STATE", tmp_path / "runtime")
    runtime.STATE.mkdir()
    (runtime.STATE / "window.json").write_text(
        runtime.json.dumps({"recorded_utc": "2026-10-05T15:34:03Z"})
    )
    path = tmp_path / "renewed-window.json"
    record = {
        "established": True,
        "source": "user message EXCLUSIVE_GPU_WINDOW_RECONFIRMED",
        "established_at_utc": "2026-10-05T16:55:26Z",
        "reconfirmed_at_utc": "2026-10-06T03:50:53Z",
        "owner_holds_until_hand_back": True,
    }
    path.write_text(runtime.json.dumps(record))
    assert (
        runtime.load_fresh_window_confirmation(
            path, datetime(2026, 10, 6, 3, 51, tzinfo=UTC)
        )
        == record
    )
    with pytest.raises(RuntimeError, match="WINDOW_CONFIRMATION_STALE"):
        runtime.load_fresh_window_confirmation(
            path, datetime(2026, 10, 6, 4, 52, tzinfo=UTC)
        )
    record["reconfirmed_at_utc"] = "2026-10-05T16:00:00Z"
    path.write_text(runtime.json.dumps(record))
    with pytest.raises(RuntimeError, match="WINDOW_CONFIRMATION_TIME_INVALID"):
        runtime.load_fresh_window_confirmation(
            path, datetime(2026, 10, 6, 3, 51, tzinfo=UTC)
        )


def test_window_id_is_consumed_once_before_any_service_start(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "STATE", tmp_path / "runtime")
    grant = {
        "window_id": "20261006-0051-new-window",
        "source": "user message EXCLUSIVE_GPU_WINDOW_RECONFIRMED",
        "established_at_utc": "2026-10-05T16:55:26Z",
        "reconfirmed_at_utc": "2026-10-06T03:50:53Z",
    }
    marker = runtime.consume_window_authorization(grant, tmp_path / "run")
    assert marker.exists()
    assert runtime.json.loads(marker.read_text())["window_id"] == grant["window_id"]
    with pytest.raises(RuntimeError, match="WINDOW_ALREADY_CONSUMED"):
        runtime.consume_window_authorization(grant, tmp_path / "another-run")
    with pytest.raises(RuntimeError, match="WINDOW_ALREADY_CONSUMED"):
        runtime.consume_window_authorization(
            {**grant, "window_id": "renamed-copy"}, tmp_path / "another-run"
        )
    with pytest.raises(RuntimeError, match="WINDOW_ALREADY_CONSUMED"):
        runtime.consume_window_authorization(
            {**grant, "established_at_utc": "2026-10-05T16:55:27Z"},
            tmp_path / "another-run",
        )
    assert len(list((runtime.STATE / "consumed-windows/events").glob("*.json"))) == 1
    with pytest.raises(RuntimeError, match="WINDOW_ID_INVALID"):
        runtime.consume_window_authorization(
            {**grant, "window_id": "../outside"}, tmp_path / "run"
        )
