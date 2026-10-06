"""One-time E-only archival of the pre-tree receipt after independent release proof."""

import json
from pathlib import Path

from scripts import run_independent_model_eval as runner


def main():
    root = Path(__file__).resolve().parents[1]
    receipt_path = root / "runtime/model-eval/services/current.json"
    proof_path = (
        root
        / "reports/model-eval/20261005T153403Z/post-abort-release-verification.json"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf8"))
    proof = json.loads(proof_path.read_text(encoding="utf8"))
    if receipt.get("tree_verified") is not None or receipt.get("pid") != 46772:
        raise RuntimeError("NOT_EXPECTED_LEGACY_RECEIPT")
    if (
        proof.get("own_service_pid") != receipt["pid"]
        or proof.get("handoff") != "GPU_RELEASE_CONFIRMED"
        or proof.get("own_service_pid_absent") is not True
        or proof.get("all_five_ports_closed") is not True
        or proof.get("own_service_stopped") is not True
        or proof.get("new_gpu_pids_since_window_preflight") != []
    ):
        raise RuntimeError("LEGACY_RELEASE_PROOF_INVALID")
    if (
        runner.process_exists(receipt["pid"])
        or runner.listener_pids().get(runner.PORT)
        or receipt["pid"] in runner.gpu_process_ids()
    ):
        raise RuntimeError("LEGACY_PROCESS_STILL_PRESENT")
    archive = receipt_path.with_name("retired-legacy-20261005T153403Z.json")
    if archive.exists():
        raise RuntimeError("LEGACY_ARCHIVE_ALREADY_EXISTS")
    receipt_path.replace(archive)
    audit = {
        "utc": runner.now(),
        "status": "LEGACY_RECEIPT_RETIRED",
        "receipt": str(archive),
        "proof": str(proof_path),
        "old_pid_absent_now": True,
        "own_port_closed_now": True,
        "old_pid_absent_from_gpu_now": True,
    }
    (root / "reports/model-eval/legacy-receipt-retirement.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf8"
    )
    print("LEGACY_RECEIPT_RETIRED")


if __name__ == "__main__":
    main()
