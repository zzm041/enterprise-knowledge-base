"""One-shot evaluation on a project-owned Ollama service during an owner-held GPU window."""

import ctypes
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "runtime/model-eval"
PORT = 11535
OLLAMA = Path("C:/Users/lmj/AppData/Local/Programs/Ollama/ollama.exe")
MODELS = ROOT / "runtime/models"
SRC_MANIFEST = ROOT / "reports/model-selection-manifest.json"
CASES = frozenset(
    ("S1", "S2", "S3", "M1", "M2", "M3", "M4", "N1", "N2", "N3", "A1", "B1")
)
CANDIDATES = ("candidate_4b", "candidate_27b")
ORIGINAL_PORTS = (11435, 11436, 11437, 11438)
BASELINE_GPU_PIDS = None


def now():
    return datetime.now(UTC).isoformat()


def load_startup_readiness_policy():
    path = STATE / "config.json"
    config = (
        json.loads(path.read_text(encoding="utf8"))
        if path.exists()
        else {
            "startup_readiness_timeout_seconds": 60,
            "startup_connect_timeout_seconds": 2,
        }
    )
    if not isinstance(config, dict):
        raise RuntimeError("STARTUP_READINESS_POLICY_INVALID")
    maximum = config.get("startup_readiness_timeout_seconds")
    connect = config.get("startup_connect_timeout_seconds")
    if (
        type(maximum) not in (int, float)
        or type(connect) not in (int, float)
        or not math.isfinite(maximum)
        or not math.isfinite(connect)
        or not 1 <= maximum <= 60
        or not 0 < connect <= min(3, maximum)
    ):
        raise RuntimeError("STARTUP_READINESS_POLICY_INVALID")
    return {
        "readiness_timeout_seconds": maximum,
        "connect_timeout_seconds": connect,
    }


def parse_gpu_probe_timing(stderr_log):
    started = None
    finished = None
    for line in (
        Path(stderr_log).read_text(encoding="utf8", errors="replace").splitlines()
    ):
        if (
            'msg="discovering available GPUs...' not in line
            and 'msg="inference compute"' not in line
        ):
            continue
        match = re.search(r"(?:^|\s)time=(\S+)", line)
        if not match:
            continue
        stamp = datetime.fromisoformat(match.group(1)).astimezone(UTC)
        if 'msg="discovering available GPUs...' in line and started is None:
            started = stamp
        elif 'msg="inference compute"' in line and started is not None:
            finished = stamp
            break
    if started is None or finished is None or finished < started:
        raise RuntimeError("GPU_PROBE_TIMING_UNOBSERVED")
    return {
        "start_utc": started.isoformat(timespec="microseconds"),
        "end_utc": finished.isoformat(timespec="microseconds"),
        "wait_seconds": round((finished - started).total_seconds(), 3),
        "source": str(Path(stderr_log)),
    }


def save_new(path, value):
    with path.open("x", encoding="utf8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def persist_service_receipt(logs, receipt):
    replacement = logs / f"current-{time.time_ns()}.update"
    save_new(replacement, receipt)
    replacement.replace(logs / "current.json")


class Budget:
    def __init__(self, root, manifest_digest):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        binding = self.root / "authorization.json"
        try:
            save_new(binding, {"manifest_sha256": manifest_digest, "maximum": 24})
        except FileExistsError:
            if json.loads(binding.read_text())["manifest_sha256"] != manifest_digest:
                raise RuntimeError("MANIFEST_CHANGED") from None

    def reserve(self, candidate, case):
        if candidate not in CANDIDATES or case not in CASES:
            raise RuntimeError("CASE_NOT_ALLOWED")
        try:
            save_new(
                self.root / (candidate + "-" + case + ".json"),
                {"status": "UNKNOWN_UNTIL_CONFIRMED", "utc": now()},
            )
        except FileExistsError:
            raise RuntimeError("ALREADY_RESERVED") from None

    def mark_sent(self, candidate, case):
        path = self.root / (candidate + "-" + case + ".json")
        if json.loads(path.read_text())["status"] != "UNKNOWN_UNTIL_CONFIRMED":
            raise RuntimeError("RESERVATION_STATE_CHANGED")
        tmp = path.with_suffix(".sending")
        with tmp.open("x", encoding="utf8") as stream:
            json.dump({"status": "SENT_UNCONFIRMED", "utc": now()}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        tmp.replace(path)

    def finish(self, candidate, case, record):
        path = self.root / (candidate + "-" + case + ".json")
        if json.loads(path.read_text())["status"] != "SENT_UNCONFIRMED":
            raise RuntimeError("RESULT_ALREADY_WRITTEN")
        tmp = path.with_suffix(".update")
        with tmp.open("x", encoding="utf8") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        tmp.replace(path)


def validate_window(confirmation, observation):
    if not confirmation or confirmation.get("established") is not True:
        raise RuntimeError("WINDOW_CONFIRMATION_REQUIRED")
    if (
        not observation.get("ports_empty")
        or observation.get("foreign_model")
        or observation.get("gpu", {}).get("free_mib", 0) < 6000
    ):
        raise RuntimeError("WINDOW_OBSERVATION_FAILED")
    return True


def load_fresh_window_confirmation(path, current_time=None):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT.resolve()):
        raise RuntimeError("WINDOW_CONFIRMATION_OUTSIDE_PROJECT")
    confirmation = json.loads(path.read_text(encoding="utf8"))
    if confirmation.get("established") is not True or not confirmation.get("source"):
        raise RuntimeError("WINDOW_CONFIRMATION_REQUIRED")
    try:
        established = datetime.fromisoformat(confirmation["established_at_utc"])
        if established.utcoffset() is None:
            raise ValueError("naive timestamp")
        established = established.astimezone(UTC)
        if "reconfirmed_at_utc" in confirmation:
            if (
                confirmation["source"]
                != "user message EXCLUSIVE_GPU_WINDOW_RECONFIRMED"
                or confirmation.get("owner_holds_until_hand_back") is not True
            ):
                raise ValueError("renewal provenance missing")
            confirmed = datetime.fromisoformat(confirmation["reconfirmed_at_utc"])
            if confirmed.utcoffset() is None:
                raise ValueError("naive renewal timestamp")
            confirmed = confirmed.astimezone(UTC)
            if confirmed < established:
                raise ValueError("renewal before establishment")
        else:
            confirmed = established
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("WINDOW_CONFIRMATION_TIME_INVALID") from exc
    reference = current_time or datetime.now(UTC)
    age = (reference - confirmed).total_seconds()
    if age < 0 or age > 3600:
        raise RuntimeError("WINDOW_CONFIRMATION_STALE")
    previous = STATE / "window.json"
    if previous.exists():
        old = json.loads(previous.read_text(encoding="utf8"))
        old_recorded = datetime.fromisoformat(old["recorded_utc"])
        if established <= old_recorded.astimezone(UTC):
            raise RuntimeError("WINDOW_CONFIRMATION_NOT_NEW")
    return confirmation


def window_event_fingerprint(confirmation):
    event = {
        "source": confirmation["source"],
        "established_at_utc": datetime.fromisoformat(confirmation["established_at_utc"])
        .astimezone(UTC)
        .isoformat(),
        "confirmed_at_utc": datetime.fromisoformat(
            confirmation.get("reconfirmed_at_utc", confirmation["established_at_utc"])
        )
        .astimezone(UTC)
        .isoformat(),
        "owner_holds_until_hand_back": confirmation.get("owner_holds_until_hand_back"),
    }
    return hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()


def consume_window_authorization(confirmation, run):
    window_id = confirmation.get("window_id")
    if not isinstance(window_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,100}", window_id
    ):
        raise RuntimeError("WINDOW_ID_INVALID")
    used = STATE / "consumed-windows"
    ids = used / "ids"
    events = used / "events"
    ids.mkdir(parents=True, exist_ok=True)
    events.mkdir(parents=True, exist_ok=True)
    if (used / f"{window_id}.json").exists() or (ids / f"{window_id}.json").exists():
        raise RuntimeError("WINDOW_ALREADY_CONSUMED")
    event_digest = window_event_fingerprint(confirmation)
    marker = events / f"{event_digest}.json"
    if marker.exists():
        raise RuntimeError("WINDOW_ALREADY_CONSUMED")
    record = {
        "window_id": window_id,
        "event_fingerprint": event_digest,
        "confirmation_sha256": hashlib.sha256(
            json.dumps(confirmation, sort_keys=True).encode()
        ).hexdigest(),
        "run": str(run),
        "consumed_at_utc": now(),
    }
    try:
        save_new(ids / f"{window_id}.json", record)
        save_new(marker, record)
    except FileExistsError:
        raise RuntimeError("WINDOW_ALREADY_CONSUMED") from None
    return marker


def output(args, timeout=5):
    command = Path(args[0]).name.lower()
    if command not in {"powershell", "netstat", "nvidia-smi", "taskkill"}:
        raise RuntimeError("OBSERVATION_COMMAND_NOT_ALLOWED")
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf8",
            errors="replace",
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"OBSERVATION_TIMEOUT:{command}") from exc
    except OSError as exc:
        raise RuntimeError(
            f"OBSERVATION_LAUNCH_FAILED:{command}:{type(exc).__name__}"
        ) from exc
    if result.returncode:
        # Do not reveal stdout/stderr or the PowerShell program: only stage + code.
        raise RuntimeError(
            f"OBSERVATION_COMMAND_FAILED:{command}:exit={result.returncode}"
        )
    return result.stdout.strip()


def listener_pids():
    lines = output(["netstat", "-ano", "-p", "TCP"], timeout=8).splitlines()
    observed = {}
    pattern = re.compile(r"^\s*TCP\s+127\.0\.0\.1:(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$")
    for line in lines:
        m = pattern.match(line)
        if m:
            observed.setdefault(int(m[1]), set()).add(int(m[2]))
    return observed


def gpu():
    raw = output(
        [
            "nvidia-smi",
            "--query-gpu=memory.free,memory.used,utilization.gpu",
            "--format=csv,noheader,nounits",
        ],
        3,
    )
    if len(raw.splitlines()) != 1:
        raise RuntimeError("GPU_OBSERVATION_FAILED")
    free, used, util = map(int, raw.split(","))
    return {"free_mib": free, "used_mib": used, "utilization_percent": util}


def observe_quiet_gpu():
    """Reject an apparently idle port set while another workload owns the GPU."""
    samples = []
    for index in range(3):
        sample = gpu()
        samples.append(sample)
        if sample["free_mib"] < 12000 or sample["utilization_percent"] > 20:
            raise RuntimeError("GPU_WINDOW_NOT_QUIET")
        if index < 2:
            time.sleep(0.5)
    return samples


def gpu_process_ids():
    raw = output(
        ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits"], 4
    )
    result = set()
    for line in raw.splitlines():
        value = line.strip()
        if not value:
            continue
        if not value.isdecimal():
            raise RuntimeError("GPU_PROCESS_OBSERVATION_UNKNOWN")
        result.add(int(value))
    return result


def assert_exclusive_gpu(own_pid=None):
    if BASELINE_GPU_PIDS is None:
        raise RuntimeError("GPU_BASELINE_MISSING")
    allowed = set(BASELINE_GPU_PIDS)
    if own_pid is not None:
        allowed.update(owned_tree_pids(own_pid))
    competing = gpu_process_ids() - allowed
    if competing:
        raise RuntimeError("COMPETING_GPU_PROCESS")


def ram_free():
    class M(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (x, ctypes.c_ulonglong)
            for x in (
                "total",
                "available",
                "page",
                "available_page",
                "virtual",
                "available_virtual",
                "extended",
            )
        ]

    m = M()
    m.length = ctypes.sizeof(m)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
        raise RuntimeError("RAM_OBSERVATION_FAILED")
    return m.available // 1048576


def foreign_model_process():
    # Read OS process table, without touching upstream endpoints or runtime files.
    ps = output(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            r"@(Get-CimInstance Win32_Process | Where-Object {$_.Name -match '^(ollama|ollama_llama_server|llama-server)(\.exe)?$'} | Select-Object ProcessId,Name,ExecutablePath) | ConvertTo-Json -Compress",
        ],
        12,
    )
    observed = json.loads(ps) if ps else []
    if isinstance(observed, dict):
        observed = [observed]
    return [
        p
        for p in observed
        if p.get("Name", "").lower()
        in ("ollama.exe", "ollama_llama_server.exe", "llama-server.exe")
    ]


def preflight(own_pid=None):
    ports = listener_pids()
    if any(ports.get(p) for p in ORIGINAL_PORTS):
        raise RuntimeError("UPSTREAM_PORT_LISTENING")
    if own_pid is None and ports.get(PORT):
        raise RuntimeError("OWN_PORT_OCCUPIED")
    if own_pid is not None and ports.get(PORT) != {own_pid}:
        raise RuntimeError("OWN_LISTENER_CHANGED")
    if own_pid is None and foreign_model_process():
        raise RuntimeError("FOREIGN_MODEL_PROCESS")
    point = gpu()
    quiet_samples = observe_quiet_gpu() if own_pid is None else None
    memory = ram_free()
    if memory < 4500 or point["free_mib"] < (6000 if own_pid is None else 2000):
        raise RuntimeError("RESOURCE_FLOOR")
    return {
        "utc": now(),
        "gpu": point,
        "ram_free_mib": memory,
        "ports_empty": True,
        "foreign_model": False,
        "gpu_quiet_samples": quiet_samples,
    }


def process_identity(pid):
    code = f"$p=Get-Process -Id {int(pid)} -ErrorAction Stop; [ordered]@{{pid=$p.Id;path=$p.Path;start=$p.StartTime.ToUniversalTime().ToString('o')}}|ConvertTo-Json -Compress"
    return json.loads(output(["powershell", "-NoProfile", "-Command", code], 12))


def verify_owned(receipt):
    observed = process_identity(receipt["pid"])
    if (
        observed["pid"] != receipt["pid"]
        or Path(observed["path"]).resolve() != OLLAMA.resolve()
        or observed["start"] != receipt["start"]
    ):
        raise RuntimeError("OWN_PROCESS_IDENTITY_CHANGED")
    return observed


def process_exists(pid):
    code = f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}' -ErrorAction Stop; if($null -eq $p){{'ABSENT'}}else{{'PRESENT'}}"
    value = output(["powershell", "-NoProfile", "-Command", code], 12)
    if value not in ("ABSENT", "PRESENT"):
        raise RuntimeError("PROCESS_OBSERVATION_UNKNOWN")
    return value == "PRESENT"


def owned_tree_pids(pid):
    code = f"$all=@(Get-CimInstance Win32_Process -ErrorAction Stop);$seen=[Collections.Generic.HashSet[int]]::new();$null=$seen.Add({int(pid)});do{{$changed=$false;foreach($x in $all){{if($seen.Contains([int]$x.ParentProcessId) -and $seen.Add([int]$x.ProcessId)){{$changed=$true}}}}}}while($changed);@($seen)|ConvertTo-Json -Compress"
    raw = output(["powershell", "-NoProfile", "-Command", code], 15)
    tree = json.loads(raw)
    return [tree] if isinstance(tree, int) else tree


def _check_recorded_child(identity):
    pid = int(identity["pid"])
    if not process_exists(pid):
        return False
    actual = process_identity(pid)
    if (
        Path(actual["path"]).resolve() != Path(identity["path"]).resolve()
        or actual["start"] != identity["start"]
    ):
        raise RuntimeError("CHILD_IDENTITY_CHANGED")
    return True


def _capture_tree(receipt):
    identities = [verify_owned(receipt)]
    for pid in owned_tree_pids(receipt["pid"]):
        if pid == receipt["pid"]:
            continue
        try:
            identities.append(process_identity(pid))
        except BaseException:
            if process_exists(pid):
                raise RuntimeError("CHILD_IDENTITY_UNOBSERVABLE") from None
    return identities


def _discover_orphans(receipt, captured):
    """Bind children added after ready using preserved Windows parent PID links."""
    known = {int(x["pid"]) for x in captured}
    root_started = datetime.fromisoformat(receipt["start"])
    allowed_dir = Path(receipt["executable"]).resolve().parent
    for pid in owned_tree_pids(receipt["pid"]):
        if pid == receipt["pid"] or pid in known or not process_exists(pid):
            continue
        identity = process_identity(pid)
        path = Path(identity["path"]).resolve()
        started = datetime.fromisoformat(identity["start"])
        if not path.is_relative_to(allowed_dir) or started < root_started:
            raise RuntimeError("ORPHAN_IDENTITY_UNBOUND")
        captured.append(identity)
    return captured


def retire_absent_receipt(logs):
    """Keep an old receipt as evidence only when its root and listener are gone."""
    current = logs / "current.json"
    if not current.exists():
        return
    receipt = json.loads(current.read_text(encoding="utf8"))
    if process_exists(int(receipt["pid"])) or listener_pids().get(PORT):
        raise RuntimeError("PRIOR_SERVICE_STATE_UNRESOLVED")
    if not receipt.get("tree_verified"):
        raise RuntimeError("PRIOR_SERVICE_DESCENDANTS_UNBOUND")
    if any(
        _check_recorded_child(x)
        for x in receipt.get("tree", [])
        if x["pid"] != receipt["pid"]
    ):
        raise RuntimeError("PRIOR_SERVICE_CHILD_STILL_RUNNING")
    current.replace(logs / f"retired-{time.time_ns()}.json")


def stop_owned(receipt, allowed_foreign_pids=()):
    if not receipt:
        return {"status": "NOT_STARTED"}
    captured = [x for x in receipt.get("tree", []) if int(x["pid"]) != receipt["pid"]]
    try:
        verify_owned(receipt)
    except BaseException:
        if process_exists(receipt["pid"]):
            raise RuntimeError("OWN_IDENTITY_UNCONFIRMED_STOP_REFUSED") from None
        if not receipt.get("tree_verified"):
            raise RuntimeError("OWN_DESCENDANTS_UNBOUND")
        if listener_pids().get(PORT):
            raise RuntimeError("OWN_LISTENER_STILL_PRESENT")
        captured = _discover_orphans(receipt, captured)
        for child in captured:
            if _check_recorded_child(child):
                output(["taskkill", "/PID", str(child["pid"]), "/T", "/F"], 20)
        for _ in range(40):
            if not any(_check_recorded_child(child) for child in captured):
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("OWN_CHILD_EXIT_UNCONFIRMED")
    else:
        if listener_pids().get(PORT) not in (None, {receipt["pid"]}):
            raise RuntimeError("OWN_LISTENER_CHANGED_STOP_REFUSED")
        current = _capture_tree(receipt)
        all_children = {
            int(x["pid"]): x
            for x in [*captured, *current]
            if int(x["pid"]) != receipt["pid"]
        }
        for child in all_children.values():
            _check_recorded_child(child)
        captured = list(all_children.values())
        output(["taskkill", "/PID", str(receipt["pid"]), "/T", "/F"], 20)
        for _ in range(40):
            if (
                not listener_pids().get(PORT)
                and not process_exists(receipt["pid"])
                and not any(_check_recorded_child(child) for child in captured)
            ):
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("OWN_PROCESS_EXIT_UNCONFIRMED")
    if any(
        int(x["ProcessId"]) not in allowed_foreign_pids for x in foreign_model_process()
    ):
        raise RuntimeError("MODEL_PROCESS_REMAINS_AFTER_STOP")
    point = gpu()
    if point["free_mib"] < 6000:
        raise RuntimeError("GPU_RELEASE_NOT_CONFIRMED")
    assert_exclusive_gpu()
    return {
        "status": "GPU_RELEASE_CONFIRMED",
        "utc": now(),
        "own_listener_closed": True,
        "own_process_exited": True,
        "verified_children": [int(x["pid"]) for x in captured],
        "gpu": point,
    }


def start_service(command=None, allowed_foreign_pids=()):
    policy = load_startup_readiness_policy()
    logs = STATE / "services"
    logs.mkdir(parents=True, exist_ok=True)
    retire_absent_receipt(logs)
    command = command or [str(OLLAMA), "serve"]
    if Path(command[0]).resolve() != OLLAMA.resolve():
        raise RuntimeError("SERVICE_EXECUTABLE_NOT_OWNED")
    if not OLLAMA.is_file() or not MODELS.is_dir():
        raise RuntimeError("OWN_RUNTIME_OR_MODELS_MISSING")
    launch_id = time.time_ns()
    stdout_log = logs / f"ollama-{launch_id}.stdout.log"
    stderr_log = logs / f"ollama-{launch_id}.stderr.log"
    out = stdout_log.open("xb")
    err = stderr_log.open("xb")
    env = {
        **os.environ,
        "OLLAMA_HOST": f"127.0.0.1:{PORT}",
        "OLLAMA_MODELS": str(MODELS),
        "OLLAMA_NO_CLOUD": "1",
        "OLLAMA_NOPRUNE": "1",
        "OLLAMA_MAX_LOADED_MODELS": "1",
        "OLLAMA_NUM_PARALLEL": "1",
        "OLLAMA_MAX_QUEUE": "2",
        "OLLAMA_KEEP_ALIVE": "30s",
        "OLLAMA_LOAD_TIMEOUT": "120s",
        "OLLAMA_CONTEXT_LENGTH": "4096",
        "OLLAMA_IGPU_ENABLE": "0",
        "OLLAMA_GPU_OVERHEAD": "5368709120",
        "OLLAMA_DEBUG": "0",
        "TEMP": str(ROOT / ".cache/temp"),
        "TMP": str(ROOT / ".cache/temp"),
    }
    launch_requested_utc = now()
    launch_monotonic = time.monotonic()
    deadline = launch_monotonic + policy["readiness_timeout_seconds"]
    try:
        proc = subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            stdout=out,
            stderr=err,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    finally:
        out.close()
        err.close()
    receipt = None
    version_thread = None
    version_result = None
    try:
        observed = process_identity(proc.pid)
        receipt = {
            "pid": proc.pid,
            "start": observed["start"],
            "executable": observed["path"],
            "port": PORT,
            "utc": now(),
            "stdout_log": str(stdout_log),
            "stderr_log": str(stderr_log),
            "tree": [observed],
            "tree_verified": False,
            "readiness": {
                "status": "STARTING",
                "policy": policy,
                "launch_requested_utc": launch_requested_utc,
                "process_start_utc": observed["start"],
                "gpu_probe": {"status": "UNOBSERVED"},
            },
        }
        save_new(logs / "current.json", receipt)
        while time.monotonic() < deadline:
            exit_code = proc.poll()
            if exit_code is not None:
                raise RuntimeError(f"OWN_SERVICE_EXITED_DURING_START:exit={exit_code}")
            if listener_pids().get(PORT) == {proc.pid}:
                readiness = receipt["readiness"]
                readiness["listener_ready_utc"] = now()
                readiness["listener_ready_elapsed_seconds"] = round(
                    time.monotonic() - launch_monotonic, 3
                )
                receipt["tree"] = _capture_tree(receipt)
                receipt["tree_verified"] = True
                persist_service_receipt(logs, receipt)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("OWN_SERVICE_START_TIMEOUT")
                timeout = httpx.Timeout(
                    connect=min(policy["connect_timeout_seconds"], remaining),
                    read=remaining,
                    write=min(2, remaining),
                    pool=min(2, remaining),
                )
                readiness["version_request_start_utc"] = now()
                request_start = time.monotonic()
                persist_service_receipt(logs, receipt)
                version_result = {}

                def request_version():
                    try:
                        with httpx.Client(trust_env=False, timeout=timeout) as client:
                            version_result["response"] = client.get(
                                f"http://127.0.0.1:{PORT}/api/version"
                            )
                    except BaseException as exc:
                        version_result["error"] = exc
                    finally:
                        version_result["end_utc"] = now()
                        version_result["elapsed_seconds"] = round(
                            time.monotonic() - request_start, 3
                        )

                try:
                    version_thread = threading.Thread(
                        target=request_version, daemon=True
                    )
                    version_thread.start()
                    version_thread.join(timeout=max(0, deadline - time.monotonic()))
                    if version_thread.is_alive():
                        raise RuntimeError("OWN_SERVICE_START_TIMEOUT")
                    if "error" in version_result:
                        raise version_result["error"]
                    response = version_result["response"]
                finally:
                    readiness["version_wait_end_utc"] = now()
                    readiness["version_wait_elapsed_seconds"] = round(
                        time.monotonic() - request_start, 3
                    )
                    if "end_utc" in version_result:
                        readiness["version_request_end_utc"] = version_result["end_utc"]
                        readiness["version_request_elapsed_seconds"] = version_result[
                            "elapsed_seconds"
                        ]
                    persist_service_receipt(logs, receipt)
                response.raise_for_status()
                if response.json().get("version") != "0.34.4":
                    raise RuntimeError("OWN_OLLAMA_VERSION_CHANGED")
                if proc.poll() is not None:
                    raise RuntimeError("OWN_SERVICE_EXITED_DURING_VERSION_PROBE")
                if time.monotonic() >= deadline:
                    raise RuntimeError("OWN_SERVICE_START_TIMEOUT")
                readiness["gpu_probe"] = parse_gpu_probe_timing(stderr_log)
                receipt["tree"] = _capture_tree(receipt)
                if time.monotonic() >= deadline:
                    raise RuntimeError("OWN_SERVICE_START_TIMEOUT")
                readiness["status"] = "READY"
                readiness["ready_utc"] = now()
                readiness["total_elapsed_seconds"] = round(
                    time.monotonic() - launch_monotonic, 3
                )
                persist_service_receipt(logs, receipt)
                if time.monotonic() >= deadline:
                    raise RuntimeError("OWN_SERVICE_START_TIMEOUT")
                return receipt
            time.sleep(0.25)
        raise RuntimeError("OWN_SERVICE_START_TIMEOUT")
    except BaseException as startup_error:
        evidence_error = None
        if receipt is not None:
            receipt["readiness"]["status"] = "FAILED"
            receipt["readiness"]["failure_utc"] = now()
            receipt["readiness"]["failure_reason"] = (
                f"{type(startup_error).__name__}:{startup_error}"
            )
            try:
                receipt["readiness"]["gpu_probe"] = parse_gpu_probe_timing(stderr_log)
            except (OSError, RuntimeError, ValueError):
                pass
            try:
                persist_service_receipt(logs, receipt)
            except BaseException as exc:
                evidence_error = exc
        cleanup_error = None
        try:
            if receipt is not None:
                stop_owned(receipt, allowed_foreign_pids=allowed_foreign_pids)
            elif proc.poll() is None:
                # The Popen handle still binds the process this call created.
                output(["taskkill", "/PID", str(proc.pid), "/T", "/F"], 20)
                proc.wait(timeout=10)
        except BaseException as exc:
            cleanup_error = exc
        if version_thread is not None and version_thread.is_alive():
            version_thread.join(timeout=2)
            if version_thread.is_alive():
                cleanup_error = RuntimeError(
                    f"{cleanup_error}; VERSION_PROBE_THREAD_UNCONFIRMED"
                )
        if (
            receipt is not None
            and version_result is not None
            and "end_utc" in version_result
        ):
            receipt["readiness"]["version_request_end_utc"] = version_result["end_utc"]
            receipt["readiness"]["version_request_elapsed_seconds"] = version_result[
                "elapsed_seconds"
            ]
            try:
                persist_service_receipt(logs, receipt)
            except BaseException as exc:
                evidence_error = exc
        if cleanup_error is not None:
            raise RuntimeError(
                f"STARTUP_FAILED:{type(startup_error).__name__}:{startup_error}; "
                f"EVIDENCE_FAILED:{type(evidence_error).__name__}:{evidence_error}; "
                f"CLEANUP_FAILED:{type(cleanup_error).__name__}:{cleanup_error}"
            ) from startup_error
        if evidence_error is not None:
            raise RuntimeError(
                f"STARTUP_FAILED:{type(startup_error).__name__}:{startup_error}; "
                f"EVIDENCE_FAILED:{type(evidence_error).__name__}:{evidence_error}"
            ) from startup_error
        if receipt is None:
            raise RuntimeError(
                f"STARTUP_FAILED:{type(startup_error).__name__}:{startup_error}; "
                "STARTUP_DESCENDANTS_UNBOUND"
            ) from startup_error
        raise


def local_get(route):
    with httpx.Client(trust_env=False, timeout=5) as client:
        r = client.get(f"http://127.0.0.1:{PORT}" + route)
        r.raise_for_status()
        return r.json()


def manifest_and_tags():
    historical = json.loads(SRC_MANIFEST.read_text(encoding="utf8"))
    inventory = json.loads((STATE / "asset-inventory.json").read_text(encoding="utf8"))
    tags = {x["tag"]: x["manifest_sha256"] for x in inventory["tags"]}
    if len(historical["requests"]) != 24 or {
        x["candidate"] for x in historical["requests"]
    } != set(CANDIDATES):
        raise RuntimeError("MANIFEST_SCOPE")
    for candidate, item in historical["models"].items():
        if candidate not in CANDIDATES or item["tag"] not in tags:
            raise RuntimeError("ASSET_TAG_MISMATCH")
    return historical, tags


def event(log, event_name, **data):
    record = {"utc": now(), "event": event_name, **data}
    with log.open("a", encoding="utf8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    print(
        event_name,
        {
            k: v
            for k, v in data.items()
            if k in ("candidate", "case", "status", "count", "reason")
        },
        flush=True,
    )


def unload_and_confirm(tag, timeout=45):
    # Existing keep_alive=30s unloads without an uncounted generation request.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not local_get("/api/ps").get("models"):
            return
        time.sleep(1)
    raise RuntimeError("OWN_MODEL_UNLOAD_UNCONFIRMED")


def one_call(request, expected_digest, receipt, budget):
    if receipt.get("readiness", {}).get("status") != "READY":
        raise RuntimeError("OWN_SERVICE_VERSION_NOT_READY")
    preflight(receipt["pid"])
    assert_exclusive_gpu(receipt["pid"])
    resident = local_get("/api/ps").get("models", [])
    if len(resident) > 1:
        raise RuntimeError("MULTIPLE_RESIDENT_MODELS")
    if resident and (
        resident[0].get("name") != request["body"]["model"]
        or resident[0].get("digest") != expected_digest
        or resident[0].get("context_length") != 4096
    ):
        raise RuntimeError("RESIDENT_IDENTITY_CHANGED")
    baseline = {"gpu": gpu(), "ram_free_mib": ram_free(), "cold": not resident}
    if baseline["ram_free_mib"] < 4500 or baseline["gpu"]["free_mib"] < (
        6000 if not resident else 2000
    ):
        raise RuntimeError("RESOURCE_FLOOR")
    payload = {
        "model": request["body"]["model"],
        "messages": request["body"]["messages"],
        "stream": False,
        "format": "json",
        "think": False,
        "options": {"temperature": 0, "num_predict": 512, "num_ctx": 4096},
        "keep_alive": "30s",
    }
    box = {}
    samples = []
    started = time.monotonic()

    def send():
        try:
            with httpx.Client(trust_env=False, timeout=180) as client:
                r = client.post(f"http://127.0.0.1:{PORT}/api/chat", json=payload)
                r.raise_for_status()
                box["response"] = r.json()
        except BaseException as exc:
            box["error"] = type(exc).__name__
        finally:
            box["client_seconds"] = time.monotonic() - started

    thread = threading.Thread(target=send, daemon=True)
    budget.mark_sent(request["candidate"], request["case_id"])
    thread.start()
    failure = None
    while thread.is_alive():
        time.sleep(0.5)
        try:
            sample = {"utc": now(), "gpu": gpu(), "ram_free_mib": ram_free()}
            samples.append(sample)
            if sample["ram_free_mib"] < 4500 or sample["gpu"]["free_mib"] < 2000:
                raise RuntimeError("RESOURCE_FLOOR")
            if time.monotonic() - started > 180:
                raise RuntimeError("INFERENCE_TIMEOUT")
            # Read OS port table: upstream restoration or unexpected listener ends experiment.
            if any(listener_pids().get(p) for p in ORIGINAL_PORTS):
                raise RuntimeError("EXCLUSIVE_WINDOW_LOST")
            assert_exclusive_gpu(receipt["pid"])
        except BaseException as exc:
            failure = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            break
    elapsed = time.monotonic() - started
    if failure:
        return {
            "status": "UNKNOWN",
            "reason": failure,
            "seconds": elapsed,
            "baseline": baseline,
            "samples": samples,
            "response": box.get("response"),
        }
    if "error" in box:
        return {
            "status": "UNKNOWN",
            "reason": box["error"],
            "seconds": elapsed,
            "baseline": baseline,
            "samples": samples,
        }
    response = box.get("response")
    if (
        not isinstance(response, dict)
        or not response.get("done")
        or response.get("model") != payload["model"]
    ):
        return {
            "status": "UNKNOWN",
            "reason": "COMPLETION_UNCONFIRMED",
            "seconds": elapsed,
            "baseline": baseline,
            "samples": samples,
            "response": response,
        }
    active = local_get("/api/ps").get("models", [])
    if (
        len(active) != 1
        or active[0].get("name") != payload["model"]
        or active[0].get("digest") != expected_digest
        or active[0].get("context_length") != 4096
    ):
        return {
            "status": "FAILURE",
            "reason": "LOADED_MODEL_IDENTITY_OR_CONTEXT",
            "seconds": elapsed,
            "baseline": baseline,
            "samples": samples,
            "response": response,
            "active": active,
        }
    answer = response.get("message", {}).get("content", "")
    try:
        parsed = json.loads(answer)
    except (TypeError, ValueError):
        return {
            "status": "FAILURE",
            "reason": "INVALID_JSON_ANSWER",
            "seconds": elapsed,
            "baseline": baseline,
            "samples": samples,
            "response": response,
        }
    evidence = json.loads(request["body"]["messages"][1]["content"])["evidence"]
    allowed_citations = {item["chunk_id"] for item in evidence}
    if (
        not isinstance(parsed.get("answer"), str)
        or not isinstance(parsed.get("citation_chunk_ids"), list)
        or any(
            type(x) is not int or x not in allowed_citations
            for x in parsed["citation_chunk_ids"]
        )
        or (
            parsed.get("status") == "answered"
            and (not parsed["answer"].strip() or not parsed["citation_chunk_ids"])
        )
        or (
            parsed.get("status") == "insufficient_knowledge"
            and (parsed["answer"] or parsed["citation_chunk_ids"])
        )
    ):
        return {
            "status": "FAILURE",
            "reason": "CITATION_OR_REFUSAL_CONTRACT",
            "seconds": elapsed,
            "baseline": baseline,
            "samples": samples,
            "response": response,
        }
    if (
        response.get("done_reason") not in (None, "stop")
        or not isinstance(parsed, dict)
        or set(parsed) != {"status", "answer", "citation_chunk_ids"}
        or parsed["status"] not in ("answered", "insufficient_knowledge")
    ):
        return {
            "status": "FAILURE",
            "reason": "ANSWER_CONTRACT",
            "seconds": elapsed,
            "baseline": baseline,
            "samples": samples,
            "response": response,
        }
    return {
        "status": "SUCCESS",
        "seconds": elapsed,
        "baseline": baseline,
        "samples": samples,
        "response": response,
        "parsed": parsed,
        "ollama_timing": {
            k: response.get(k)
            for k in (
                "total_duration",
                "load_duration",
                "prompt_eval_duration",
                "eval_duration",
                "eval_count",
            )
        },
    }


def main():
    global BASELINE_GPU_PIDS
    if len(sys.argv) != 3 or sys.argv[1] != "--window-confirmation":
        raise RuntimeError("FRESH_WINDOW_CONFIRMATION_REQUIRED")
    confirmation = load_fresh_window_confirmation(sys.argv[2])
    STATE.mkdir(parents=True, exist_ok=True)
    historical, tags = manifest_and_tags()
    # Do not execute historical endpoint bindings. Freeze the exact same prompts/evidence.
    manifest = {
        "requests": [
            {
                "candidate": x["candidate"],
                "case_id": x["case_id"],
                "body": x["body"],
                "evidence_sha256": x["evidence_sha256"],
            }
            for x in historical["requests"]
        ],
        "source_corpus_sha256": historical["corpus_sha256"],
        "source_cases_sha256": historical["cases_sha256"],
        "runtime_endpoint": f"127.0.0.1:{PORT}",
        "tags": tags,
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
    budget = Budget(STATE / "budget", hashlib.sha256(manifest_bytes).hexdigest())
    run = ROOT / "reports/model-eval" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run.mkdir(parents=True, exist_ok=False)
    consume_window_authorization(confirmation, run)
    save_new(run / "window-confirmation.json", confirmation)
    save_new(run / "manifest.json", manifest)
    log = run / "events.jsonl"
    receipt = None
    service_attempted = False
    success = failure = unknown = 0
    cleanup = None
    active_key = None
    try:
        observation = preflight()
        validate_window(confirmation, observation)
        BASELINE_GPU_PIDS = gpu_process_ids()
        event(
            log,
            "WINDOW_VERIFIED",
            observation=observation,
            baseline_gpu_pids=sorted(BASELINE_GPU_PIDS),
        )
        service_attempted = True
        receipt = start_service()
        event(log, "OWN_SERVICE_READY", receipt=receipt)
        loaded = local_get("/api/tags")["models"]
        for candidate in CANDIDATES:
            model = historical["models"][candidate]
            tag = model["tag"]
            digest = tags[tag]
            if not any(
                m.get("name") == tag and m.get("digest") == digest for m in loaded
            ):
                raise RuntimeError("OWN_ASSET_DIGEST_CHANGED")
            for request in manifest["requests"]:
                if request["candidate"] != candidate:
                    continue
                if request["body"]["model"] != tag:
                    raise RuntimeError("MANIFEST_MODEL_DRIFT")
                budget.reserve(candidate, request["case_id"])
                active_key = (candidate, request["case_id"])
                event(log, "RESERVED", candidate=candidate, case=request["case_id"])
                result = one_call(request, digest, receipt, budget)
                budget.finish(candidate, request["case_id"], result)
                active_key = None
                event(
                    log,
                    "RESULT",
                    candidate=candidate,
                    case=request["case_id"],
                    status=result["status"],
                    reason=result.get("reason"),
                    seconds=result["seconds"],
                )
                if result["status"] == "SUCCESS":
                    success += 1
                elif result["status"] == "FAILURE":
                    failure += 1
                    raise RuntimeError("CALL_FAILURE_STOP")
                else:
                    unknown += 1
                    raise RuntimeError("CALL_UNKNOWN_STOP")
            unload_and_confirm(tag)
            event(log, "OWN_MODEL_UNLOADED", candidate=candidate)
        event(log, "ALL_CALLS_COMPLETE", count=success)
    except BaseException as exc:
        if active_key:
            reservation = json.loads(
                (
                    budget.root / (active_key[0] + "-" + active_key[1] + ".json")
                ).read_text()
            )
            if reservation["status"] == "SENT_UNCONFIRMED":
                unknown += 1
                event(
                    log,
                    "CALL_UNKNOWN",
                    candidate=active_key[0],
                    case=active_key[1],
                    reason="INTERRUPTED_OR_UNOBSERVED_COMPLETION",
                )
        event(
            log,
            "RUN_STOPPED",
            reason=str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__,
            count=success + failure + unknown,
        )
    finally:
        try:
            cleanup = stop_owned(receipt)
            if receipt is None and service_attempted:
                cleanup = {
                    "status": "CLEANUP_UNCONFIRMED",
                    "reason": "STARTED_WITHOUT_VERIFIED_RECEIPT",
                }
            elif receipt is None:
                observation = preflight()
                assert_exclusive_gpu()
                cleanup = {
                    "status": "GPU_RELEASE_CONFIRMED",
                    "reason": "NO_OWN_SERVICE_REMAINS",
                    "observation": observation,
                }
            event(log, "OWN_SERVICE_STOPPED", cleanup=cleanup)
        except BaseException as exc:
            cleanup = {"status": "CLEANUP_UNCONFIRMED", "reason": type(exc).__name__}
            event(log, "CLEANUP_UNCONFIRMED", reason=type(exc).__name__)
        summary = {
            "actual_calls": success + failure + unknown,
            "success": success,
            "failure": failure,
            "unknown": unknown,
            "own_service": cleanup["status"],
            "gpu_release_confirmed": cleanup["status"] == "GPU_RELEASE_CONFIRMED",
            "report": str(run),
            "utc": now(),
        }
        (run / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf8"
        )
        print("FINAL", json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if success == 24 and cleanup["status"] == "GPU_RELEASE_CONFIRMED" else 1


if __name__ == "__main__":
    sys.exit(main())
