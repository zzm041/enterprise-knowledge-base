import json

import pytest

from scripts.run_independent_model_eval import Budget, validate_window


def test_budget_never_replays_or_exceeds_24(tmp_path):
    budget = Budget(tmp_path, "one-manifest")
    budget.reserve("candidate_4b", "S1")
    with pytest.raises(RuntimeError, match="ALREADY_RESERVED"):
        Budget(tmp_path, "one-manifest").reserve("candidate_4b", "S1")
    with pytest.raises(RuntimeError, match="MANIFEST_CHANGED"):
        Budget(tmp_path, "changed")
    with pytest.raises(RuntimeError, match="CASE_NOT_ALLOWED"):
        budget.reserve("candidate_4b", "S99")
    assert (
        json.loads((tmp_path / "candidate_4b-S1.json").read_text())["status"]
        == "UNKNOWN_UNTIL_CONFIRMED"
    )


def test_missing_or_stale_window_is_refused():
    with pytest.raises(RuntimeError, match="WINDOW_CONFIRMATION_REQUIRED"):
        validate_window(
            None, {"ports_empty": True, "gpu_free": 14000, "foreign_model": False}
        )
    with pytest.raises(RuntimeError, match="WINDOW_OBSERVATION_FAILED"):
        validate_window(
            {"established": True},
            {"ports_empty": False, "gpu_free": 14000, "foreign_model": False},
        )


def test_sent_attempt_remains_spent_after_restart(tmp_path):
    budget = Budget(tmp_path, "manifest")
    budget.reserve("candidate_27b", "M2")
    budget.mark_sent("candidate_27b", "M2")
    assert (
        json.loads((tmp_path / "candidate_27b-M2.json").read_text())["status"]
        == "SENT_UNCONFIRMED"
    )
    with pytest.raises(RuntimeError, match="ALREADY_RESERVED"):
        Budget(tmp_path, "manifest").reserve("candidate_27b", "M2")
    budget.finish("candidate_27b", "M2", {"status": "SUCCESS"})
    with pytest.raises(RuntimeError, match="ALREADY_RESERVED"):
        budget.reserve("candidate_27b", "M2")


def test_foreign_pid_never_stopped(monkeypatch):
    import scripts.run_independent_model_eval as mod

    monkeypatch.setattr(
        mod,
        "verify_owned",
        lambda receipt: (_ for _ in ()).throw(
            RuntimeError("OWN_PROCESS_IDENTITY_CHANGED")
        ),
    )
    called = []
    monkeypatch.setattr(mod, "output", lambda *args, **kwargs: called.append(args))
    monkeypatch.setattr(mod, "process_exists", lambda pid: True)
    with pytest.raises(RuntimeError, match="OWN_IDENTITY_UNCONFIRMED_STOP_REFUSED"):
        mod.stop_owned({"pid": 123})
    assert called == []


def test_window_rejects_foreign_model(monkeypatch):
    import scripts.run_independent_model_eval as mod

    monkeypatch.setattr(mod, "listener_pids", lambda: {})
    monkeypatch.setattr(mod, "foreign_model_process", lambda: [{"Name": "ollama.exe"}])
    with pytest.raises(RuntimeError, match="FOREIGN_MODEL_PROCESS"):
        mod.preflight()


def test_real_preflight_shape_is_accepted(monkeypatch):
    import scripts.run_independent_model_eval as mod

    monkeypatch.setattr(mod, "listener_pids", lambda: {})
    monkeypatch.setattr(mod, "foreign_model_process", lambda: [])
    monkeypatch.setattr(
        mod,
        "gpu",
        lambda: {"free_mib": 14000, "used_mib": 2000, "utilization_percent": 0},
    )
    monkeypatch.setattr(mod, "ram_free", lambda: 9000)
    assert mod.validate_window({"established": True}, mod.preflight())


def test_gpu_window_rejects_busy_gpu_even_when_ports_are_idle(monkeypatch):
    import scripts.run_independent_model_eval as mod

    monkeypatch.setattr(mod, "listener_pids", lambda: {})
    monkeypatch.setattr(mod, "foreign_model_process", lambda: [])
    monkeypatch.setattr(
        mod,
        "gpu",
        lambda: {"free_mib": 13000, "used_mib": 3000, "utilization_percent": 91},
    )
    with pytest.raises(RuntimeError, match="GPU_WINDOW_NOT_QUIET"):
        mod.preflight()


def test_ollama_runner_child_is_foreign_model_process(monkeypatch):
    import scripts.run_independent_model_eval as mod

    monkeypatch.setattr(
        mod,
        "output",
        lambda args, timeout=5: json.dumps(
            {
                "ProcessId": 44013,
                "Name": "ollama_llama_server.exe",
                "ExecutablePath": "C:/Ollama/ollama_llama_server.exe",
            }
        ),
    )
    assert mod.foreign_model_process()[0]["ProcessId"] == 44013
