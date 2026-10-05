import runpy
from pathlib import Path


def test_model_manifest_is_paired_frozen_and_unexecuted():
    namespace = runpy.run_path(
        str(
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "prepare_model_selection.py"
        )
    )
    manifest = namespace["prepare"]()
    assert manifest["status"] == "PREPARED_NOT_RUN"
    assert manifest["scores"] is None
    assert len(manifest["requests"]) == 24
    for case in manifest["cases"]:
        pair = [r for r in manifest["requests"] if r["case_id"] == case["id"]]
        assert len(pair) == 2
        assert pair[0]["body"]["messages"] == pair[1]["body"]["messages"]
        assert pair[0]["body"]["max_tokens"] == pair[1]["body"]["max_tokens"] == 512
        assert pair[0]["evidence_sha256"] == pair[1]["evidence_sha256"]
    assert set(manifest["models"]) == {"candidate_4b", "candidate_27b"}
