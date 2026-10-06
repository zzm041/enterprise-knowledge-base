"""Copy only pinned Ollama model manifests and their content-addressed blobs."""

import hashlib
import json
import os
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("D:/property-ai/tmp/selfhost-ai-local/models")
DEST = ROOT / "runtime/models"
TAGS = (("qwen3.5", "4b"), ("qwen38-27b-q3km-g55-ctx8192", "7af3b8f"))
DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")


def manifest_items(source=SOURCE):
    result = []
    for name, tag in TAGS:
        relative = Path("manifests/registry.ollama.ai/library") / name / tag
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("MANIFEST_MISSING_OR_LINK")
        content = path.read_bytes()
        manifest = json.loads(content)
        if manifest.get("schemaVersion") != 2:
            raise RuntimeError("MANIFEST_SCHEMA")
        layers = [manifest["config"], *manifest["layers"]]
        blobs = []
        for layer in layers:
            match = DIGEST.fullmatch(layer["digest"])
            if not match:
                raise RuntimeError("BLOB_DIGEST_INVALID")
            blob = source / "blobs" / ("sha256-" + match.group(1))
            if (
                blob.is_symlink()
                or not blob.is_file()
                or blob.stat().st_size != layer["size"]
            ):
                raise RuntimeError("BLOB_MISSING_OR_SIZE")
            blobs.append((blob, match.group(1), layer["size"]))
        result.append((relative, content, blobs))
    return result


def copy_verified(source=SOURCE, dest=DEST):
    if dest.exists():
        raise RuntimeError("DEST_ALREADY_EXISTS")
    items = manifest_items(source)
    unique = {
        digest: (path, size) for _, _, blobs in items for path, digest, size in blobs
    }
    required = sum(size for _, size in unique.values())
    if shutil.disk_usage(dest.parent).free < required + 2_000_000_000:
        raise RuntimeError("DISK_CAPACITY")
    staging = dest.with_name(dest.name + ".incomplete")
    if staging.exists():
        raise RuntimeError("INCOMPLETE_COPY_EXISTS")
    staging.mkdir(parents=True)
    report = {
        "tags": [],
        "blobs": [],
        "bytes": required,
        "source_kind": "existing local Ollama model assets only",
    }
    try:
        (staging / "blobs").mkdir()
        for digest, (src, size) in unique.items():
            target = staging / "blobs" / ("sha256-" + digest)
            h = hashlib.sha256()
            before = (src.stat().st_size, src.stat().st_mtime_ns)
            with src.open("rb") as inp, target.open("xb") as out:
                while chunk := inp.read(8 * 1024 * 1024):
                    h.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            if (
                h.hexdigest() != digest
                or target.stat().st_size != size
                or (src.stat().st_size, src.stat().st_mtime_ns) != before
            ):
                raise RuntimeError("SOURCE_CHANGED_OR_DIGEST_MISMATCH")
            d = hashlib.sha256()
            with target.open("rb") as check:
                while chunk := check.read(8 * 1024 * 1024):
                    d.update(chunk)
            if d.hexdigest() != digest:
                raise RuntimeError("DEST_DIGEST_MISMATCH")
            report["blobs"].append({"digest": "sha256:" + digest, "size": size})
            print(
                "verified blob",
                len(report["blobs"]),
                "of",
                len(unique),
                size,
                "bytes",
                flush=True,
            )
        for relative, content, _ in items:
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as out:
                out.write(content)
                out.flush()
                os.fsync(out.fileno())
            report["tags"].append(
                {
                    "tag": relative.parts[-2] + ":" + relative.parts[-1],
                    "manifest_sha256": hashlib.sha256(content).hexdigest(),
                }
            )
        staging.rename(dest)
        return report
    except BaseException:
        # Retain incomplete data for diagnosis; it is never a loadable model root.
        raise


if __name__ == "__main__":
    output = copy_verified()
    path = ROOT / "runtime/model-eval/asset-inventory.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf8")
    print("ASSETS_VERIFIED", output["bytes"], flush=True)
