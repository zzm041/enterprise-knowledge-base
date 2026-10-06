import hashlib
import json

import pytest

from scripts.model_eval_assets import copy_verified, manifest_items


def setup_assets(root):
    blob = b"synthetic model bytes"
    digest = hashlib.sha256(blob).hexdigest()
    path = root / "blobs" / ("sha256-" + digest)
    path.parent.mkdir(parents=True)
    path.write_bytes(blob)
    for name, tag in (("qwen3.5", "4b"), ("qwen38-27b-q3km-g55-ctx8192", "7af3b8f")):
        manifest = {
            "schemaVersion": 2,
            "config": {"digest": "sha256:" + digest, "size": len(blob)},
            "layers": [],
        }
        target = root / "manifests/registry.ollama.ai/library" / name / tag
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(manifest))
    return path


def test_copy_only_digest_closure(tmp_path):
    source = tmp_path / "source"
    blob = setup_assets(source)
    report = copy_verified(source, tmp_path / "models")
    assert len(report["tags"]) == 2
    assert len(report["blobs"]) == 1
    assert blob.read_bytes() == (tmp_path / "models/blobs" / blob.name).read_bytes()


def test_corrupt_blob_does_not_promote(tmp_path):
    source = tmp_path / "source"
    blob = setup_assets(source)
    blob.write_bytes(b"x" * blob.stat().st_size)
    with pytest.raises(RuntimeError, match="SOURCE_CHANGED_OR_DIGEST_MISMATCH"):
        copy_verified(source, tmp_path / "models")
    assert not (tmp_path / "models").exists()


def test_missing_blob_rejected_before_copy(tmp_path):
    source = tmp_path / "source"
    blob = setup_assets(source)
    blob.unlink()
    with pytest.raises(RuntimeError, match="BLOB_MISSING_OR_SIZE"):
        manifest_items(source)
